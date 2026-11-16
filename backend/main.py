"""Game server: seats players at a table and runs the game for them.

It also relays the WebRTC offers, answers and ICE candidates players need to
connect to each other directly. Live voice never passes through this server;
the only audio it receives is what players send to be captioned. When every
player opts in, a finished game is sent to Claude for a summary.
"""

import asyncio
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ai_summary import SummaryUnavailable, summarize
from captions import BYTES_PER_SECOND, load_model, transcribe
from game import Game, IllegalMove, Phase
from game_log import GameLog
from recap import build_recap
from voice_features import VoiceBaseline, measure_voice

logger = logging.getLogger(__name__)

MAX_PLAYERS_PER_ROOM = 4
MIN_PLAYERS_TO_START = 2
MAX_NAME_LENGTH = 32
ROOM_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

CLOSE_INVALID_ROOM = 4400
CLOSE_ROOM_FULL = 4409
CLOSE_GAME_IN_PROGRESS = 4423

# How long each phase lasts before the server moves the game on by itself.
PHASE_SECONDS = {
    Phase.PLAYING: 30.0,
    Phase.REVEAL: 4.0,
    Phase.ROULETTE: 3.5,
    Phase.SHOT: 3.5,
    Phase.OVER: 10.0,
}

# Clients end each utterance themselves; these protect the server if they don't.
MIN_SPEECH_BYTES = BYTES_PER_SECOND // 4
MAX_SPEECH_BYTES = BYTES_PER_SECOND * 10
# Rather than captions falling further and further behind, drop speech once the
# server has this many utterances waiting.
MAX_PENDING_CAPTIONS = 8


@dataclass(eq=False)
class Peer:
    id: str
    name: str
    socket: WebSocket
    # Whether this player agreed to have their words saved in the game log.
    save_speech: bool = False
    # Whether this player agreed to have the game sent to Claude for a summary.
    ai_recap: bool = False
    speech: bytearray = field(default_factory=bytearray)
    # Kept for the whole connection, so it carries over between games.
    voice_baseline: VoiceBaseline = field(default_factory=VoiceBaseline)

    def info(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name}


@dataclass(eq=False)
class Room:
    id: str
    peers: dict[str, Peer] = field(default_factory=dict)
    game: Game | None = None
    log: GameLog | None = None
    # Stats from the last game, until the next one starts.
    recap: dict | None = None
    # Claude's summary of the last game: {"status": "pending" | "ready" |
    # "failed" | "off", "text"?: str}. "off" means not everyone opted in.
    ai_summary: dict | None = None
    ai_consent: bool = False
    summary_task: asyncio.Task | None = None
    timer: asyncio.Task | None = None
    # Lets clients discard a state update that overtook a newer one.
    version: int = 0


app = FastAPI()
rooms: dict[str, Room] = {}

# Serializes joins so a player always receives their welcome before hearing
# about anyone who joined after them.
join_lock = asyncio.Lock()

# Whisper uses every CPU core, so utterances take turns, in the order spoken.
transcribe_lock = asyncio.Lock()
caption_tasks: set[asyncio.Task] = set()


async def send(peer: Peer, message: dict) -> None:
    try:
        await peer.socket.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        # The peer is going away; its own handler removes it from the room.
        pass


async def broadcast(room: Room, message: dict, exclude: Peer) -> None:
    others = [peer for peer in room.peers.values() if peer is not exclude]
    await asyncio.gather(*(send(peer, message) for peer in others))


async def run_phase_timer(room: Room) -> None:
    await asyncio.sleep(PHASE_SECONDS[room.game.phase])
    # This task is finishing, so sync_room must not cancel it.
    room.timer = None
    if room.game.phase is Phase.OVER:
        # Once more, to include anything that was still being transcribed.
        room.recap = build_recap(room.log.events)
        room.game = None
        room.log = None
    elif room.game.phase is Phase.PLAYING:
        room.game.timeout()
    else:
        room.game.advance()
    await sync_room(room)


async def write_summary(room: Room, events: list[dict]) -> None:
    try:
        room.ai_summary = {"status": "ready", "text": await summarize(events)}
    except SummaryUnavailable as error:
        room.ai_summary = {"status": "failed", "text": str(error)}
    except Exception:
        logger.exception("Could not summarize the game")
        room.ai_summary = {"status": "failed", "text": "Claude couldn't write a summary"}
    room.summary_task = None
    # Not sync_room: that would restart the timer of whatever phase it is now.
    await send_state(room)


def start_summary(room: Room) -> None:
    """Asks Claude for a summary of the game that just ended, if everyone agreed."""
    if room.ai_summary is not None:
        return
    if not room.ai_consent:
        room.ai_summary = {"status": "off"}
        return
    room.ai_summary = {"status": "pending"}
    room.summary_task = asyncio.create_task(write_summary(room, list(room.log.events)))


def cancel_summary(room: Room) -> None:
    if room.summary_task:
        room.summary_task.cancel()
        room.summary_task = None
    room.ai_summary = None


async def sync_room(room: Room) -> None:
    """Restarts the phase timer and sends every player their view of the room."""
    if room.timer:
        room.timer.cancel()
        room.timer = None
    if room.game:
        room.timer = asyncio.create_task(run_phase_timer(room))
        if room.game.phase is Phase.OVER:
            room.recap = build_recap(room.log.events)
            start_summary(room)
    await send_state(room)


async def send_state(room: Room) -> None:
    room.version += 1
    players = [peer.info() for peer in room.peers.values()]
    await asyncio.gather(
        *(
            send(
                peer,
                {
                    "type": "state",
                    "version": room.version,
                    "players": players,
                    "game": room.game and room.game.view_for(peer.id),
                    "secondsLeft": room.game and PHASE_SECONDS[room.game.phase],
                    "recap": room.recap and {**room.recap, "ai": room.ai_summary},
                },
            )
            for peer in room.peers.values()
        )
    )


async def handle_game_action(room: Room, peer: Peer, message: dict) -> None:
    try:
        match message.get("type"):
            case "start":
                if room.game:
                    raise IllegalMove("A game is already in progress")
                if len(room.peers) < MIN_PLAYERS_TO_START:
                    raise IllegalMove(
                        f"Need at least {MIN_PLAYERS_TO_START} players to start"
                    )
                room.recap = None
                cancel_summary(room)
                room.ai_consent = all(p.ai_recap for p in room.peers.values())
                room.log = GameLog(room.id)
                room.game = Game(
                    [(p.id, p.name) for p in room.peers.values()],
                    on_event=room.log.write,
                )
            case "play" if room.game:
                room.game.play(peer.id, message.get("cards"))
            case "call" if room.game:
                room.game.call_liar(peer.id)
            case _:
                return
    except IllegalMove as error:
        await send(peer, {"type": "error", "message": str(error)})
        return
    await sync_room(room)


async def caption_speech(
    room: Room, peer: Peer, pcm: bytes, log: GameLog | None, end: float
) -> None:
    measured = {}
    try:
        async with transcribe_lock:
            text = await asyncio.to_thread(transcribe, pcm)
            # Only speech Whisper heard words in is measured, so coughs and
            # background noise never shift a player's baseline.
            if text:
                try:
                    measured = await asyncio.to_thread(measure_voice, pcm, text)
                except Exception:
                    # Captions still work without it.
                    logger.exception("Could not measure the voice")
    except Exception:
        logger.exception("Could not transcribe speech")
        await send(peer, {"type": "error", "message": "Captions are unavailable"})
        return
    if text:
        if log and peer.save_speech:
            log.write_speech(peer.id, end - len(pcm) / BYTES_PER_SECOND, end, text)
        caption = {
            "type": "caption",
            "playerId": peer.id,
            "name": peer.name,
            "text": text,
            "voice": peer.voice_baseline.compare(measured),
        }
        await asyncio.gather(*(send(other, caption) for other in room.peers.values()))


def finish_speech(room: Room, peer: Peer) -> None:
    pcm = bytes(peer.speech)
    peer.speech.clear()
    if len(pcm) < MIN_SPEECH_BYTES:
        return
    if len(caption_tasks) >= MAX_PENDING_CAPTIONS:
        logger.warning("Dropping speech: too many captions are waiting")
        return
    # Noted now, because the game may have moved on by the time this is
    # transcribed. Speech still belongs to the game it was said in.
    log = room.log
    end = log.now() if log else 0.0
    task = asyncio.create_task(caption_speech(room, peer, pcm, log, end))
    caption_tasks.add(task)
    task.add_done_callback(caption_tasks.discard)


async def handle_messages(room: Room, peer: Peer) -> None:
    while True:
        frame = await peer.socket.receive()
        if frame["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(frame.get("code", 1000))
        # Binary frames are the sender's speech; text frames are JSON messages.
        if frame.get("bytes") is not None:
            peer.speech += frame["bytes"]
            if len(peer.speech) >= MAX_SPEECH_BYTES:
                finish_speech(room, peer)
            continue
        try:
            message = json.loads(frame.get("text") or "")
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("type") == "speech-end":
            finish_speech(room, peer)
        elif message.get("type") == "signal":
            target = room.peers.get(message.get("to"))
            if target is not None and target is not peer:
                # `from` is set here so that a client cannot impersonate another.
                await send(
                    target,
                    {"type": "signal", "from": peer.id, "data": message.get("data")},
                )
        else:
            await handle_game_action(room, peer, message)


@app.websocket("/ws/{room_id}")
async def game_room(
    socket: WebSocket,
    room_id: str,
    name: str = "Player",
    save_speech: bool = False,
    ai_recap: bool = False,
) -> None:
    # Accept before rejecting: a browser only sees the close reason of an
    # established connection.
    await socket.accept()
    if not ROOM_ID.fullmatch(room_id):
        await socket.close(CLOSE_INVALID_ROOM, "Invalid room name")
        return

    peer = Peer(
        uuid4().hex,
        name.strip()[:MAX_NAME_LENGTH] or "Player",
        socket,
        save_speech,
        ai_recap,
    )

    async with join_lock:
        room = rooms.get(room_id) or Room(room_id)
        if room.game:
            await socket.close(CLOSE_GAME_IN_PROGRESS, "That table is mid-game")
            return
        if len(room.peers) >= MAX_PLAYERS_PER_ROOM:
            await socket.close(CLOSE_ROOM_FULL, "That table is full")
            return
        rooms[room_id] = room
        await send(
            peer,
            {
                "type": "welcome",
                "id": peer.id,
                "peers": [other.info() for other in room.peers.values()],
            },
        )
        room.peers[peer.id] = peer
        await broadcast(room, {"type": "peer-joined", "peer": peer.info()}, peer)
        await sync_room(room)

    try:
        await handle_messages(room, peer)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        del room.peers[peer.id]
        if room.game:
            room.game.remove_player(peer.id)
        if room.peers:
            await broadcast(room, {"type": "peer-left", "id": peer.id}, peer)
            await sync_room(room)
        else:
            if room.timer:
                room.timer.cancel()
            cancel_summary(room)
            if rooms.get(room_id) is room:
                del rooms[room_id]


if __name__ == "__main__":
    # Load the model early, so that the first thing anyone says gets a caption
    # without a wait. This downloads it the first time the server ever runs.
    threading.Thread(target=load_model, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, ws_max_size=64 * 1024)
