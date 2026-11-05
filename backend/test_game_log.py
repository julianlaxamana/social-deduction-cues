import json
import random

import pytest
from fastapi.testclient import TestClient

import game_log
import main
from captions import BYTES_PER_SECOND
from game import Card, Game, Phase


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(game_log, "LOG_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def events():
    return []


def make_game(events, count=2):
    def record(event, **fields):
        events.append({"event": event, **fields})

    players = [(f"p{i}", f"Player {i}") for i in range(count)]
    return Game(players, random.Random(1), on_event=record)


def named(events, name):
    return [event for event in events if event["event"] == name]


def set_hand(game, index, ranks):
    start = 100 * (index + 1)
    game.players[index].hand = [Card(start + i, rank) for i, rank in enumerate(ranks)]
    return [card.id for card in game.players[index].hand]


def wrong_rank(game):
    return next(rank for rank in ("Q", "K") if rank != game.table_rank)


def read_log(log_dir):
    [path] = log_dir.glob("*.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()]


def receive_until(socket, message_type):
    while True:
        message = socket.receive_json()
        if message["type"] == message_type:
            return message


def test_game_start_records_players_and_dealt_hands(events):
    game = make_game(events)
    assert named(events, "game-start")[0]["players"][0] == {"id": "p0", "name": "Player 0"}
    round_start = named(events, "round-start")[0]
    assert round_start["tableRank"] == game.table_rank
    assert round_start["firstPlayerId"] == "p0"
    assert round_start["hands"]["p1"] == [card.rank for card in game.players[1].hand]


@pytest.mark.parametrize("honest", [True, False])
def test_every_play_is_labelled_even_if_never_called(events, honest):
    game = make_game(events, 3)
    rank = game.table_rank if honest else wrong_rank(game)
    ids = set_hand(game, 0, [rank, "J"])
    game.play("p0", ids)
    play = named(events, "play")[0]
    assert play["lied"] is not honest
    assert play["ranks"] == [rank, "J"]
    assert play["cardsLeft"] == 0
    assert play["nextPlayerId"] == "p1"
    assert play["auto"] is False


def test_call_and_shot_are_recorded(events):
    game = make_game(events, 3)
    game.players[0].bullet = 5
    ids = set_hand(game, 0, [wrong_rank(game)])
    game.play("p0", ids)
    game.call_liar("p1")
    game.advance()
    game.advance()
    call = named(events, "call")[0]
    assert call["callerId"] == "p1"
    assert call["accusedId"] == "p0"
    assert call["loserId"] == "p0"
    assert call["forced"] is False
    shot = named(events, "shot")[0]
    assert shot == {
        "event": "shot", "round": 1, "playerId": "p0", "fired": False, "pull": 1
    }


def test_forced_calls_and_timeouts_are_marked(events):
    game = make_game(events)
    game.timeout()
    assert named(events, "play")[0]["auto"] is True
    set_hand(game, 0, [])
    game.call_liar("p1")
    assert named(events, "call")[0]["forced"] is True


def test_game_over_and_leaving_are_recorded(events):
    game = make_game(events)
    game.remove_player("p0")
    assert named(events, "player-left")[0]["playerId"] == "p0"
    assert named(events, "game-over")[0]["winnerId"] == "p1"
    assert game.phase is Phase.OVER


def test_log_entries_are_timed_json_lines(log_dir):
    log = game_log.GameLog("table")
    log.write("something", value=1)
    log.write_speech("p1", 1.5, 2.25, "hello")
    start, something, speech = read_log(log_dir)
    assert start["event"] == "log-start"
    assert start["room"] == "table"
    assert something["value"] == 1
    assert something["t"] >= start["t"]
    assert speech == {
        "t": 1.5, "event": "speech", "playerId": "p1", "end": 2.25, "text": "hello"
    }


def test_unwritable_log_does_not_break_the_game(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(game_log, "LOG_DIR", blocker / "logs")
    log = game_log.GameLog("table")
    game = Game([("a", "A"), ("b", "B")], on_event=log.write)
    game.timeout()


def play_with_speech(monkeypatch, ann_query):
    """Starts a game, then has Ann say something and waits for the caption."""
    monkeypatch.setattr(main, "transcribe", lambda pcm: "three kings")
    with TestClient(main.app) as client:
        with client.websocket_connect(f"/ws/log-table?name=Ann{ann_query}") as ann:
            ann_id = receive_until(ann, "welcome")["id"]
            with client.websocket_connect("/ws/log-table?name=Bo") as bo:
                ann.send_json({"type": "start"})
                receive_until(bo, "state")
                ann.send_bytes(b"\x01\x00" * BYTES_PER_SECOND)  # Two seconds of audio.
                ann.send_json({"type": "speech-end"})
                receive_until(bo, "caption")
    return ann_id


def test_game_is_logged_with_speech_when_the_player_opted_in(log_dir, monkeypatch):
    ann_id = play_with_speech(monkeypatch, "&save_speech=true")
    log = read_log(log_dir)
    names = [entry["event"] for entry in log]
    assert names[:3] == ["log-start", "game-start", "round-start"]
    assert "player-left" in names
    assert "game-over" in names
    [speech] = named(log, "speech")
    assert speech["playerId"] == ann_id
    assert speech["text"] == "three kings"
    assert speech["end"] - speech["t"] == pytest.approx(2.0, abs=0.01)


def test_speech_is_captioned_but_not_saved_without_opting_in(log_dir, monkeypatch):
    play_with_speech(monkeypatch, "")
    log = read_log(log_dir)
    assert named(log, "speech") == []
    assert "three kings" not in json.dumps(log)


def test_lobby_speech_is_not_logged(log_dir, monkeypatch):
    monkeypatch.setattr(main, "transcribe", lambda pcm: "hello")
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/quiet-table?name=Ann&save_speech=true") as ann:
            ann.send_bytes(b"\x01\x00" * BYTES_PER_SECOND)
            ann.send_json({"type": "speech-end"})
            receive_until(ann, "caption")
    assert list(log_dir.glob("*.jsonl")) == []
