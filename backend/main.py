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
from captions import BYTES_PER_SECOND, Transcription, load_model, transcribe
from game import Game, IllegalMove, Phase
from game_log import GameLog
from gemini_judgment import insufficient, judge_play
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
MAX_PENDING_JUDGMENTS = 8
CLAIM_LOOKBACK_SECONDS = 8.0
CLAIM_WAIT_SECONDS = 2.5


@dataclass(frozen=True)
class ClaimEvidence:
    transcription: Transcription
    features: dict[str, float]
    baseline: dict


@dataclass(frozen=True)
class Claim:
    # The server validates this turn before accepting any audio as evidence.
    turn: tuple[str, int, int]
    started: float
    evidence: asyncio.Future[ClaimEvidence | None]


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
    gemini_analysis: bool = False
    captions_enabled: bool = True
    pending_claim: Claim | None = None
    latest_claim: Claim | None = None

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
    deadline: float = 0.0
    play_reference: dict | None = None
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
judgment_tasks: set[asyncio.Task] = set()


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
        previous_seq = room.game.play_seq
        room.game.timeout()
        await sync_after_action(room, previous_seq, automatic=True)
        return
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
        room.deadline = asyncio.get_running_loop().time() + PHASE_SECONDS[room.game.phase]
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
                    "gameId": room.log.id if room.game else None,
                    "game": room.game and room.game.view_for(peer.id),
                    "secondsLeft": room.game and max(
                        0, room.deadline - asyncio.get_running_loop().time(),
                    ),
                    "playReference": room.play_reference if peer.gemini_analysis and current_reference(room) else None,
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
                room.play_reference = None
                cancel_summary(room)
                room.ai_consent = all(p.ai_recap for p in room.peers.values())
                room.log = GameLog(room.id)
                room.game = Game(
                    [(p.id, p.name) for p in room.peers.values()],
                    on_event=room.log.write,
                )
            case "play" if room.game:
                previous_seq = room.game.play_seq
                room.game.play(peer.id, message.get("cards"))
                await sync_after_action(room, previous_seq, automatic=False)
                return
            case "call" if room.game:
                room.game.call_liar(peer.id)
            case _:
                return
    except IllegalMove as error:
        await send(peer, {"type": "error", "message": str(error)})
        return
    await sync_room(room)


def current_reference(room: Room) -> bool:
    game, log, reference = room.game, room.log, room.play_reference
    return bool(
        game and log and reference and game.phase is Phase.PLAYING
        and game.last_play and reference["gameId"] == log.id
        and reference["round"] == game.round and reference["playSeq"] == game.last_play.seq
    )


async def sync_after_action(room: Room, previous_seq: int, *, automatic: bool) -> None:
    game, log = room.game, room.log
    if game.play_seq == previous_seq or not any(peer.gemini_analysis for peer in room.peers.values()):
        await sync_room(room)
        return

    play = game.last_play
    peer = room.peers[play.player_id]
    turn = (log.id, game.round, previous_seq)
    claim = peer.latest_claim if peer.gemini_analysis else None
    peer.latest_claim = None
    if claim and (claim.turn != turn or log.now() - claim.started > CLAIM_LOOKBACK_SECONDS):
        claim = None
    if claim and peer.pending_claim is claim and peer.speech:
        # The server can finish the buffered audio even when the timer played the cards.
        finish_speech(room, peer)

    reference = {
        "gameId": log.id, "round": game.round, "playSeq": play.seq, "status": "analyzing",
    }
    room.play_reference = reference
    played_at = log.now()
    if len(judgment_tasks) >= MAX_PENDING_JUDGMENTS:
        reference["status"] = "unavailable"
        log.write(
            "play-judgment", playerId=peer.id, playSeq=play.seq, judgment="insufficient_evidence",
            diagnostic="over_capacity", metrics=[], elapsedMs=0, published=True,
        )
    else:
        task = asyncio.create_task(record_judgment(
            room, log, peer.id, reference, game.table_rank, len(play.cards), claim,
            played_at, automatic,
        ))
        judgment_tasks.add(task)
        task.add_done_callback(judgment_tasks.discard)
    await sync_room(room)


async def record_judgment(
    room: Room, log: GameLog, player_id: str, reference: dict, table_rank: str,
    card_count: int, claim: Claim | None, played_at: float, automatic: bool,
) -> None:
    def still_callable() -> bool:
        return room.play_reference is reference and current_reference(room) and player_id in room.peers

    evidence = None
    diagnostic = "no_matching_speech"
    if claim:
        try:
            evidence = await asyncio.wait_for(
                asyncio.shield(claim.evidence), CLAIM_WAIT_SECONDS,
            )
        except TimeoutError:
            diagnostic = "transcript_timeout"
    if evidence and not evidence.transcription.reliable:
        diagnostic = "uncertain_transcript"
        evidence = None
    if not still_callable():
        attempt = insufficient("The play is no longer callable.", "stale_play")
    elif evidence or automatic:
        attempt = await judge_play(
            table_rank=table_rank, card_count=card_count,
            transcript=evidence.transcription.text if evidence else None,
            speech_features=evidence.features if evidence else {},
            baseline=evidence.baseline if evidence else None,
            automatic=automatic,
        )
    else:
        attempt = insufficient("No reliable speech was available.", diagnostic)
    result = attempt.result
    play_seq = reference["playSeq"]
    log.judgments[play_seq] = result
    elapsed_ms = round((log.now() - played_at) * 1000)
    publish = still_callable()
    log.write(
        "play-judgment", playerId=player_id, playSeq=play_seq, judgment=result.judgment,
        elapsedMs=elapsed_ms, published=publish, diagnostic=attempt.diagnostic,
        metrics=list(attempt.metrics),
        calibratedMetrics=[item["metric"] for item in attempt.comparisons],
        modelVoiceMetric=result.voice_metric,
    )
    if publish:
        status = (
            "unavailable"
            if attempt.diagnostic in ("not_configured", "api_timeout", "api_error")
            else result.judgment
        )
        room.play_reference = {**reference, "status": status}
        room.play_reference["voiceComparisons"] = attempt.comparisons
        room.play_reference["modelVoiceMetric"] = result.voice_metric
        if status in ("likely_bluff", "likely_truthful"):
            room.play_reference["explanation"] = result.explanation
        # Updating the reference must not restart the game timer.
        await send_state(room)


def start_claim(room: Room, peer: Peer, message: dict) -> None:
    game, log = room.game, room.log
    if (
        not peer.gemini_analysis or peer.speech or peer.pending_claim
        or not log or not game or game.phase is not Phase.PLAYING or game.current.id != peer.id
    ):
        return
    turn = (log.id, game.round, game.play_seq)
    previous = game.last_play.seq if game.last_play else None
    if (message.get("gameId"), message.get("round"), message.get("previousPlaySeq")) == (
        log.id, game.round, previous,
    ):
        peer.pending_claim = peer.latest_claim = Claim(
            turn, log.now(), asyncio.get_running_loop().create_future(),
        )


async def caption_speech(
    room: Room, peer: Peer, pcm: bytes, log: GameLog | None, end: float,
    claim: Claim | None, show_caption: bool,
) -> None:
    measured = {}
    try:
        async with transcribe_lock:
            transcription = await asyncio.to_thread(transcribe, pcm)
            # Only speech Whisper heard words in is measured, so coughs and
            # background noise never shift a player's baseline.
            if transcription.text:
                try:
                    measured = await asyncio.to_thread(measure_voice, pcm, transcription.text)
                except Exception:
                    # Captions still work without it.
                    logger.exception("Could not measure the voice")
    except Exception:
        logger.exception("Could not transcribe speech")
        await send(peer, {"type": "error", "message": "Captions are unavailable"})
        return
    text = transcription.text
    if text:
        baseline = peer.voice_baseline.compare(measured)
        if claim:
            claim.evidence.set_result(ClaimEvidence(transcription, measured, baseline))
        if log and peer.save_speech:
            log.write_speech(peer.id, end - len(pcm) / BYTES_PER_SECOND, end, text)
        if not show_caption:
            return
        caption = {
            "type": "caption",
            "playerId": peer.id,
            "name": peer.name,
            "text": text,
            "voice": baseline,
        }
        await asyncio.gather(*(send(other, caption) for other in room.peers.values()))


def finish_speech(room: Room, peer: Peer) -> None:
    pcm = bytes(peer.speech)
    peer.speech.clear()
    claim = peer.pending_claim
    peer.pending_claim = None
    if len(pcm) < MIN_SPEECH_BYTES:
        if claim:
            claim.evidence.set_result(None)
        return
    if len(caption_tasks) >= MAX_PENDING_CAPTIONS:
        if claim:
            claim.evidence.set_result(None)
        logger.warning("Dropping speech: too many captions are waiting")
        return
    # Noted now, because the game may have moved on by the time this is
    # transcribed. Speech still belongs to the game it was said in.
    log = room.log
    end = log.now() if log else 0.0
    task = asyncio.create_task(caption_speech(room, peer, pcm, log, end, claim, peer.captions_enabled))
    caption_tasks.add(task)
    task.add_done_callback(caption_tasks.discard)
    if claim:
        def finish_claim(_task: asyncio.Task) -> None:
            if not claim.evidence.done():
                claim.evidence.set_result(None)

        task.add_done_callback(finish_claim)


async def handle_messages(room: Room, peer: Peer) -> None:
    while True:
        frame = await peer.socket.receive()
        if frame["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(frame.get("code", 1000))
        # Binary frames are the sender's speech; text frames are JSON messages.
        if frame.get("bytes") is not None:
            if not peer.captions_enabled and not peer.pending_claim:
                continue
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
        if message.get("type") == "speech-start":
            start_claim(room, peer, message)
        elif message.get("type") == "speech-end":
            finish_speech(room, peer)
        elif message.get("type") == "caption-preference" and isinstance(message.get("enabled"), bool):
            peer.captions_enabled = message["enabled"]
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
    gemini_analysis: bool = False,
    captions_enabled: bool = True,
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
        gemini_analysis=gemini_analysis,
        captions_enabled=captions_enabled,
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
