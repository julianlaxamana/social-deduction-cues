import asyncio
import json
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors

import game_log
import gemini_judgment
import main
from gemini_judgment import judge_play


class FakeModels:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.requests = []

    async def generate_content(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.reply)


def run_judgment(models, transcript="Two queens.", features=None):
    return asyncio.run(judge_play(
        table_rank="Q", card_count=2, transcript=transcript,
        speech_features=features or {}, client=SimpleNamespace(models=models),
    ))


def test_only_public_context_and_known_speech_features_reach_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "configured-flash")
    models = FakeModels('{"judgment":"likely_bluff","explanation":"The claim sounded tentative."}')
    result = run_judgment(models, features={
        "pitch": 165, "pauses": 12, "lied": True, "hand": ["K"], "reveal": ["A"],
    })
    assert result.judgment == "likely_bluff"
    request, = models.requests
    assert request["model"] == "configured-flash"
    assert request["config"].response_mime_type == "application/json"
    assert json.loads(request["contents"]) == {
        "table_rank": "Q", "card_count": 2, "transcript": "Two queens.",
        "speech_features": {"pitch": 165, "pauses": 12},
    }


def test_missing_speech_skips_the_api():
    models = FakeModels()
    assert run_judgment(models, transcript=" ").judgment == "insufficient_evidence"
    assert not models.requests


@pytest.mark.parametrize("models", [
    FakeModels('{"judgment":"certain","explanation":"Invalid label."}'),
    FakeModels(error=errors.ClientError(429, {"error": {"message": "Quota exhausted"}})),
    FakeModels(error=RuntimeError("Transport failed")),
])
def test_invalid_output_and_api_failures_return_insufficient(models):
    assert run_judgment(models).judgment == "insufficient_evidence"


def test_api_failure_logs_status_without_response_body(caplog):
    error = errors.ClientError(429, {"error": {"message": "Private request details"}})
    assert run_judgment(FakeModels(error=error)).judgment == "insufficient_evidence"
    assert "HTTP 429" in caplog.text
    assert "Private request details" not in caplog.text


def test_timeout_returns_insufficient(monkeypatch):
    monkeypatch.setattr(gemini_judgment, "REQUEST_TIMEOUT", 0.01)

    class SlowModels:
        async def generate_content(self, **request):
            await asyncio.sleep(60)

    assert run_judgment(SlowModels()).judgment == "insufficient_evidence"


@pytest.mark.parametrize("advance", [None, "call", "play", "stale-speech", "insufficient"])
def test_play_reference_uses_current_play_and_speaker(monkeypatch, tmp_path, advance):
    models = FakeModels('{"judgment":"likely_truthful","explanation":"Two queens."}')
    if advance == "insufficient":
        models.reply = '{"judgment":"insufficient_evidence","explanation":"Too little speech."}'
    recorded = Event()
    requested = Event()
    release = asyncio.Event()
    results = []
    write = game_log.GameLog.write

    async def judge(**evidence):
        result = await judge_play(**evidence, client=SimpleNamespace(models=models))
        requested.set()
        if advance in ("call", "play"):
            await release.wait()
        return result

    def capture(log, event, **fields):
        write(log, event, **fields)
        if event == "play-judgment":
            results.append((log.judgments[fields["playSeq"]], fields))
            if fields["playSeq"] == 1:
                recorded.set()

    def receive(socket, predicate):
        while not predicate(message := socket.receive_json()):
            pass
        return message

    monkeypatch.setattr(game_log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(game_log.GameLog, "write", capture)
    monkeypatch.setattr(main, "judge_play", judge)
    monkeypatch.setattr(main, "transcribe", lambda audio: (
        "Two queens." if audio[0] == 1 else "Other player's speech."
    ))
    monkeypatch.setattr(main, "measure_voice", lambda *_: {"pitch": 170})
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/gemini-test?gemini_analysis=true") as speaker:
            with client.websocket_connect("/ws/gemini-test?gemini_analysis=true") as other:
                speaker.send_json({"type": "start"})
                state = receive(speaker, lambda msg: msg.get("game"))
                view = state["game"]
                marker = {
                    "type": "speech-start", "gameId": state["gameId"], "round": view["round"],
                    "previousPlaySeq": 99 if advance == "stale-speech" else None,
                }
                other.send_json(marker)
                other.send_bytes(b"\x02\x00" * main.MIN_SPEECH_BYTES)
                other.send_json({"type": "speech-end"})
                speaker.send_json(marker)
                speaker.send_bytes(b"\x01\x00" * main.MIN_SPEECH_BYTES)
                speaker.send_json({"type": "play", "cards": [view["hand"][0]["id"]]})
                receive(speaker, lambda msg: (msg.get("game") or {}).get("lastPlay"))
                speaker.send_json({"type": "speech-end"})
                if advance in ("call", "play"):
                    assert requested.wait(3)
                    next_view = receive(other, lambda msg: (msg.get("game") or {}).get("lastPlay"))["game"]
                    action = {"type": advance}
                    if advance == "play":
                        action["cards"] = [next_view["hand"][0]["id"]]
                    other.send_json(action)
                    receive(other, lambda msg: (game := msg.get("game")) and (
                        game["phase"] == "reveal" or game["lastPlay"]["seq"] == 2
                    ))
                    client.portal.call(release.set)
                assert recorded.wait(3)
                if advance is None:
                    reference = receive(other, lambda msg: msg["type"] == "play-reference")
                    assert reference == {
                        "type": "play-reference", "gameId": state["gameId"], "round": view["round"],
                        "playSeq": 1, "judgment": "likely_truthful", "explanation": "Two queens.",
                    }
                else:
                    other.send_json({"type": "start"})
                    message = receive(other, lambda msg: msg["type"] in ("error", "play-reference"))
                    assert message["type"] == "error"

    if advance == "stale-speech":
        assert not models.requests
        assert results[0][1]["judgment"] == "insufficient_evidence"
        return
    request = models.requests[0]
    assert json.loads(request["contents"]) == {
        "table_rank": view["tableRank"], "card_count": 1,
        "transcript": "Two queens.", "speech_features": {"pitch": 170},
    }
    result, fields = next(entry for entry in results if entry[1]["playSeq"] == 1)
    assert fields["playSeq"] == 1
    assert fields["judgment"] == (
        "insufficient_evidence" if advance == "insufficient" else "likely_truthful"
    )
    assert result.explanation
    assert fields["published"] == (advance is None)
    assert "Two queens." not in next(tmp_path.glob("*.jsonl")).read_text()
