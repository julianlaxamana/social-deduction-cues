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
import captions
from captions import Transcription
from game import Phase
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


def run_judgment(models, transcript="Two queens.", features=None, **options):
    return asyncio.run(judge_play(
        table_rank="Q", card_count=2, transcript=transcript,
        speech_features=features or {}, client=SimpleNamespace(models=models), **options,
    ))


def test_only_public_context_and_known_speech_features_reach_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "configured-flash")
    models = FakeModels('{"judgment":"likely_bluff","explanation":"The claim sounded tentative."}')
    attempt = run_judgment(models, features={
        "pitch": 165, "pauses": 12, "lied": True, "hand": ["K"], "reveal": ["A"],
    }, baseline={"pitch": {"change": 2.5}, "pauses": {"change": None}})
    assert attempt.result.judgment == "likely_bluff"
    assert attempt.metrics == ("pitch", "pauses")
    request, = models.requests
    assert request["model"] == "configured-flash"
    assert request["config"].response_mime_type == "application/json"
    assert json.loads(request["contents"]) == {
        "table_rank": "Q", "card_count": 2, "automatic": False, "transcript": "Two queens.",
        "speech_features": {
            "pitch": {"value": 165, "unit": "Hz", "baseline_change": {"value": 2.5, "unit": "semitones"}},
            "pauses": {"value": 12, "unit": "percent of utterance", "baseline_change": None},
            "speed": None, "fillers": None,
        },
    }


def test_missing_speech_skips_the_api():
    models = FakeModels()
    assert run_judgment(models, transcript=" ").diagnostic == "no_matching_speech"
    assert not models.requests


def test_model_can_select_a_measured_baseline_change():
    models = FakeModels(
        '{"judgment":"insufficient_evidence","explanation":"Pitch rose but the claim is uncertain.",'
        '"voice_metric":"pitch"}'
    )
    attempt = run_judgment(models, features={"pitch": 180}, baseline={"pitch": {"change": 2.5}})
    assert attempt.result.voice_metric == "pitch"
    assert attempt.result.judgment == "insufficient_evidence"
    assert attempt.comparisons == ({"metric": "pitch", "value": 2.5, "unit": "semitones"},)


@pytest.mark.parametrize("change", [None, 0.0])
def test_model_cannot_cite_an_unavailable_or_unchanged_baseline(change):
    models = FakeModels(
        '{"judgment":"likely_bluff","explanation":"Pitch rose.","voice_metric":"pitch"}'
    )
    attempt = run_judgment(models, features={"pitch": 180}, baseline={"pitch": {"change": change}})
    assert attempt.diagnostic == "unsupported_voice_reference"
    assert attempt.result.judgment == "insufficient_evidence"
    assert attempt.result.voice_metric is None


@pytest.mark.parametrize("models", [
    FakeModels('{"judgment":"certain","explanation":"Invalid label."}'),
    FakeModels(error=errors.ClientError(429, {"error": {"message": "Quota exhausted"}})),
    FakeModels(error=RuntimeError("Transport failed")),
])
def test_invalid_output_and_api_failures_return_insufficient(models):
    assert run_judgment(models).diagnostic == "api_error"


def test_api_failure_logs_status_without_response_body(caplog):
    error = errors.ClientError(429, {"error": {"message": "Private request details"}})
    assert run_judgment(FakeModels(error=error)).diagnostic == "api_error"
    assert "HTTP 429" in caplog.text
    assert "Private request details" not in caplog.text


def test_timeout_returns_insufficient(monkeypatch):
    monkeypatch.setattr(gemini_judgment, "REQUEST_TIMEOUT", 0.01)

    class SlowModels:
        async def generate_content(self, **request):
            await asyncio.sleep(60)

    assert run_judgment(SlowModels()).diagnostic == "api_timeout"


@pytest.mark.parametrize("text,score,reliable", [
    ("two kings", -0.22, True), ("one ace", -0.3, True),
    ("one aces", -0.3, True), ("one S", -1.2, False),
])
def test_short_whisper_transcripts_keep_quality_separate(monkeypatch, text, score, reliable):
    def transcribe(_samples, **options):
        assert options["vad_parameters"]["min_speech_duration_ms"] == 100
        return [SimpleNamespace(text=text, avg_logprob=score, no_speech_prob=0.1)], None

    monkeypatch.setattr(captions, "load_model", lambda: SimpleNamespace(transcribe=transcribe))
    assert captions.transcribe(b"\x00\x00" * 4000) == Transcription(text, reliable)


def receive(socket, predicate):
    while not predicate(message := socket.receive_json()):
        pass
    return message


@pytest.mark.parametrize("advance", [
    None, "call", "play", "stale-speech", "insufficient", "uncertain", "timeout",
])
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
        if advance == "timeout":
            async def slow(**_request):
                await asyncio.sleep(60)
            monkeypatch.setattr(models, "generate_content", slow)
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

    monkeypatch.setattr(game_log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(game_log.GameLog, "write", capture)
    monkeypatch.setattr(main, "judge_play", judge)
    monkeypatch.setattr(gemini_judgment, "REQUEST_TIMEOUT", 0.05)
    monkeypatch.setattr(main, "transcribe", lambda audio: Transcription(
        "Two queens." if audio[0] == 1 else "Other player's speech.",
        advance != "uncertain",
    ))
    monkeypatch.setattr(main, "measure_voice", lambda audio, _: {
        "pitch": 150 if audio[0] == 3 else 170,
    })
    comparisons = []
    compare = main.VoiceBaseline.compare

    def track_comparison(baseline, measured):
        comparisons.append((baseline, measured))
        return compare(baseline, measured)

    monkeypatch.setattr(main.VoiceBaseline, "compare", track_comparison)
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/gemini-test?gemini_analysis=true") as speaker:
            with client.websocket_connect("/ws/gemini-test?gemini_analysis=true") as other:
                for _ in range(5):
                    speaker.send_bytes(b"\x03\x00" * main.MIN_SPEECH_BYTES)
                    speaker.send_json({"type": "speech-end"})
                    caption = receive(speaker, lambda msg: msg["type"] == "caption")
                    assert caption["voice"]["pitch"]["change"] is None
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
                analyzing = receive(speaker, lambda msg: (msg.get("game") or {}).get("lastPlay"))
                assert analyzing["playReference"]["status"] == "analyzing"
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
                if advance not in ("call", "play"):
                    reference = receive(other, lambda msg: (
                        (ref := msg.get("playReference")) and ref["status"] != "analyzing"
                    ))["playReference"]
                    expected_status = {
                        None: "likely_truthful", "timeout": "unavailable",
                    }.get(advance, "insufficient_evidence")
                    assert reference["status"] == expected_status
                    assert (reference["gameId"], reference["round"], reference["playSeq"]) == (
                        state["gameId"], view["round"], 1,
                    )
                    assert reference.get("explanation") == ("Two queens." if advance is None else None)
                    assert reference["voiceComparisons"] == (
                        [{"metric": "pitch", "value": 2.2, "unit": "semitones"}]
                        if advance in (None, "insufficient", "timeout") else []
                    )
                else:
                    other.send_json({"type": "start"})
                    message = receive(other, lambda msg: (
                        msg["type"] == "error"
                        or ((ref := msg.get("playReference")) and ref["playSeq"] == 1
                            and ref["status"] != "analyzing")
                    ))
                    assert message["type"] == "error"

    speaker_baseline = comparisons[0][0]
    assert [values["pitch"] for instance, values in comparisons if instance is speaker_baseline] == [150] * 5 + [170]
    if advance in ("stale-speech", "uncertain", "timeout"):
        assert not models.requests
        assert results[0][1]["judgment"] == "insufficient_evidence"
        assert results[0][1]["diagnostic"] == {
            "stale-speech": "no_matching_speech", "uncertain": "uncertain_transcript",
            "timeout": "api_timeout",
        }[advance]
        return
    request = models.requests[0]
    assert json.loads(request["contents"]) == {
        "table_rank": view["tableRank"], "card_count": 1, "automatic": False,
        "transcript": "Two queens.", "speech_features": {
            "pitch": {"value": 170, "unit": "Hz", "baseline_change": {"value": 2.2, "unit": "semitones"}},
            "speed": None, "pauses": None, "fillers": None,
        },
    }
    result, fields = next(entry for entry in results if entry[1]["playSeq"] == 1)
    assert fields["playSeq"] == 1
    assert fields["judgment"] == (
        "insufficient_evidence" if advance == "insufficient" else "likely_truthful"
    )
    assert result.explanation
    assert fields["published"] == (advance not in ("call", "play"))
    assert "Two queens." not in next(tmp_path.glob("*.jsonl")).read_text()


def test_automatic_play_sends_only_public_information_for_opted_out_speaker(monkeypatch, tmp_path):
    models = FakeModels('{"judgment":"insufficient_evidence","explanation":"No claim."}')
    monkeypatch.setitem(main.PHASE_SECONDS, Phase.PLAYING, 0.3)
    monkeypatch.setattr(game_log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(main, "judge_play", lambda **kwargs: judge_play(
        **kwargs, client=SimpleNamespace(models=models),
    ))
    spoken = []
    monkeypatch.setattr(main, "transcribe", lambda pcm: spoken.append(pcm))
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/auto?captions_enabled=false") as speaker:
            with client.websocket_connect("/ws/auto?gemini_analysis=true") as listener:
                speaker.send_json({"type": "start"})
                state = receive(speaker, lambda msg: msg.get("game"))
                speaker.send_json({
                    "type": "speech-start", "gameId": state["gameId"],
                    "round": state["game"]["round"], "previousPlaySeq": None,
                })
                speaker.send_bytes(b"\x01\x00" * main.MIN_SPEECH_BYTES)
                speaker.send_json({"type": "speech-end"})
                pending = receive(listener, lambda msg: msg.get("playReference"))
                done = receive(listener, lambda msg: (
                    (ref := msg.get("playReference")) and ref["status"] != "analyzing"
                ))
                assert pending["playReference"]["status"] == "analyzing"
                assert done["playReference"]["status"] == "insufficient_evidence"
                assert done["secondsLeft"] <= pending["secondsLeft"]
                for _ in range(2):
                    hidden = receive(speaker, lambda msg: (msg.get("game") or {}).get("lastPlay"))
                    assert hidden["playReference"] is None
    assert not spoken
    request, = models.requests
    assert json.loads(request["contents"]) == {
        "table_rank": state["game"]["tableRank"], "card_count": 1,
        "automatic": True, "transcript": None,
        "speech_features": dict.fromkeys(("pitch", "speed", "pauses", "fillers")),
    }
    events = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    assert next(e for e in events if e["event"] == "play")["auto"] is True


def test_opted_in_speaker_can_use_ai_with_captions_off(monkeypatch, tmp_path):
    models = FakeModels('{"judgment":"likely_truthful","explanation":"The claim matches the public rank."}')
    monkeypatch.setattr(game_log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(main, "judge_play", lambda **kwargs: judge_play(
        **kwargs, client=SimpleNamespace(models=models),
    ))
    monkeypatch.setattr(main, "transcribe", lambda _: Transcription("One aces", True))
    monkeypatch.setattr(main, "measure_voice", lambda *_: {"pitch": 180})
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/ai-only?captions_enabled=false&gemini_analysis=true") as speaker:
            with client.websocket_connect("/ws/ai-only") as listener:
                speaker.send_json({"type": "start"})
                state = receive(speaker, lambda msg: msg.get("game"))
                speaker.send_json({
                    "type": "speech-start", "gameId": state["gameId"],
                    "round": state["game"]["round"], "previousPlaySeq": None,
                })
                speaker.send_bytes(b"\x01\x00" * main.MIN_SPEECH_BYTES)
                speaker.send_json({"type": "speech-end"})
                speaker.send_json({"type": "play", "cards": [state["game"]["hand"][0]["id"]]})
                terminal = receive(speaker, lambda msg: msg["type"] == "caption" or (
                    (ref := msg.get("playReference")) and ref["status"] != "analyzing"
                ))
                assert terminal["playReference"]["status"] == "likely_truthful"
                for _ in range(2):
                    state = receive(listener, lambda msg: msg["type"] == "caption" or (
                        (msg.get("game") or {}).get("lastPlay")
                    ))
                    assert state["type"] == "state"
                    assert state["playReference"] is None
    request, = models.requests
    payload = json.loads(request["contents"])
    assert payload["transcript"] == "One aces"
    assert payload["speech_features"]["speed"] is None
    assert payload["speech_features"]["pitch"] == {
        "value": 180, "unit": "Hz", "baseline_change": None,
    }
