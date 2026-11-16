import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient

import ai_summary
import game
import game_log
import main
from ai_summary import SummaryUnavailable, game_record, summarize
from game import Phase

EVENTS = [
    {"t": 0, "event": "log-start", "startedAt": "2026-01-01T00:00:00"},
    {"t": 0, "event": "game-start", "players": [{"id": "a", "name": "Ann"}, {"id": "b", "name": "Bo"}]},
    {
        "t": 0, "event": "round-start", "round": 1, "tableRank": "K", "firstPlayerId": "a",
        "hands": {"a": ["Q", "K"], "b": ["A", "J"]},
    },
    {"t": 3.2, "event": "speech", "playerId": "a", "end": 4.0, "text": "Two kings, trust me."},
    {
        "t": 4.5, "event": "play", "round": 1, "playerId": "a", "ranks": ["Q", "K"],
        "lied": True, "cardsLeft": 0, "nextPlayerId": "b", "auto": False,
    },
    {
        "t": 6, "event": "call", "round": 1, "callerId": "b", "accusedId": "a",
        "ranks": ["Q", "K"], "lied": True, "loserId": "a", "forced": False, "auto": False,
    },
    {"t": 9, "event": "shot", "round": 1, "playerId": "a", "fired": True, "pull": 1},
    {"t": 9.5, "event": "game-over", "round": 1, "winnerId": "b"},
]


def test_game_record_reads_as_plain_english_with_names():
    record = game_record(EVENTS)
    assert '"a"' not in record and "'b'" not in record
    assert "Table card: King" in record
    assert "Ann holds Queen, King" in record
    assert 'Ann says: "Two kings, trust me."' in record
    assert "Ann plays 2 cards (really Queen, King, a lie)" in record
    assert "Bo calls Ann a liar. It was a lie: Queen, King. Ann must pull the trigger." in record
    assert "Ann pulls the trigger (pull 1): BANG" in record
    assert record.endswith("Bo wins.")
    # Speech comes before the play it led up to, even if logged later.
    assert record.index("says") < record.index("plays")


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """Points the module at a script that records how it was run."""
    calls = tmp_path / "calls.json"

    def install(reply: dict, delay: float = 0):
        script = tmp_path / "claude"
        script.write_text(
            f"#!{sys.executable}\n"
            "import json, sys, time\n"
            f"time.sleep({delay})\n"
            f"json.dump({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}, open({str(calls)!r}, 'w'))\n"
            f"print(json.dumps({reply!r}))\n"
        )
        script.chmod(0o755)
        monkeypatch.setattr(ai_summary, "CLAUDE_BIN", str(script))
        return lambda: json.loads(calls.read_text())

    return install


def test_summary_runs_claude_with_no_tools_and_the_game_on_stdin(fake_claude):
    last_call = fake_claude({"result": "  Ann bluffed and paid for it.  ", "is_error": False})
    assert asyncio.run(summarize(EVENTS)) == "Ann bluffed and paid for it."
    call = last_call()
    assert call["argv"][call["argv"].index("--tools") + 1] == ""
    assert "-p" in call["argv"]
    assert call["stdin"] == game_record(EVENTS)


def test_signed_out_claude_gives_a_clear_reason(fake_claude):
    fake_claude({"result": "Not logged in · Please run /login", "is_error": True})
    with pytest.raises(SummaryUnavailable, match="isn't signed in"):
        asyncio.run(summarize(EVENTS))


def test_missing_claude_gives_a_clear_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_summary, "CLAUDE_BIN", str(tmp_path / "nowhere"))
    with pytest.raises(SummaryUnavailable, match="isn't installed"):
        asyncio.run(summarize(EVENTS))


def test_slow_claude_is_given_up_on(fake_claude, monkeypatch):
    fake_claude({"result": "late", "is_error": False}, delay=5)
    monkeypatch.setattr(ai_summary, "TIMEOUT_SECONDS", 0.5)
    with pytest.raises(SummaryUnavailable, match="too long"):
        asyncio.run(summarize(EVENTS))


def receive_until(socket, condition):
    while True:
        message = socket.receive_json()
        if message["type"] == "state" and condition(message):
            return message


def play_quick_game(client, table, ann_query, bo_query):
    """Ann plays a card, Bo calls, and one-chamber revolvers end the game."""
    with client.websocket_connect(f"/ws/{table}?name=Ann&{ann_query}") as ann:
        with client.websocket_connect(f"/ws/{table}?name=Bo&{bo_query}") as bo:
            ann.send_json({"type": "start"})
            dealt = receive_until(ann, lambda m: m["game"])
            ann.send_json({"type": "play", "cards": [dealt["game"]["hand"][0]["id"]]})
            receive_until(bo, lambda m: m["game"] and m["game"]["canCall"])
            bo.send_json({"type": "call"})
            over = receive_until(ann, lambda m: m["recap"])
            final = receive_until(ann, lambda m: m["recap"]["ai"]["status"] != "pending")
            return over, final


@pytest.fixture
def quick_games(tmp_path, monkeypatch):
    monkeypatch.setattr(game_log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(game, "CHAMBERS", 1)
    for phase in (Phase.REVEAL, Phase.ROULETTE, Phase.SHOT):
        monkeypatch.setitem(main.PHASE_SECONDS, phase, 0.05)
    summarized = []

    async def fake_summarize(events):
        summarized.append(events)
        await asyncio.sleep(0.1)
        return "What a game."

    monkeypatch.setattr(main, "summarize", fake_summarize)
    return summarized


def test_summary_follows_the_recap_when_everyone_opted_in(quick_games):
    with TestClient(main.app) as client:
        over, final = play_quick_game(client, "ai-table", "ai_recap=true", "ai_recap=true")
    assert over["recap"]["ai"] == {"status": "pending"}
    assert final["recap"]["ai"] == {"status": "ready", "text": "What a game."}
    # Sending it didn't cut the game-over pause short.
    assert final["game"]["phase"] == "over"
    assert quick_games[0][-1]["event"] == "game-over"


def test_no_summary_unless_everyone_opted_in(quick_games):
    with TestClient(main.app) as client:
        _, final = play_quick_game(client, "half-ai-table", "ai_recap=true", "")
    assert final["recap"]["ai"] == {"status": "off"}
    assert quick_games == []


def test_failed_summary_tells_players_why(quick_games, monkeypatch):
    async def broken(_events):
        raise SummaryUnavailable("Claude Code isn't signed in on the server")

    monkeypatch.setattr(main, "summarize", broken)
    with TestClient(main.app) as client:
        _, final = play_quick_game(client, "broken-ai-table", "ai_recap=true", "ai_recap=true")
    assert final["recap"]["ai"] == {
        "status": "failed",
        "text": "Claude Code isn't signed in on the server",
    }
