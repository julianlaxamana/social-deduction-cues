import pytest
from fastapi.testclient import TestClient

import main
from captions import BYTES_PER_SECOND, Transcription


@pytest.fixture
def spoken(monkeypatch):
    """Replaces Whisper, recording the audio it is given."""
    received = []

    def fake_transcribe(pcm):
        received.append(pcm)
        return Transcription("three kings", True)

    monkeypatch.setattr(main, "transcribe", fake_transcribe)
    return received


def receive_until(socket, message_type):
    while True:
        message = socket.receive_json()
        if message["type"] == message_type:
            return message


def test_speech_is_captioned_for_everyone_at_the_table(spoken):
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/caption-table?name=Ann") as ann:
            ann_id = receive_until(ann, "welcome")["id"]
            with client.websocket_connect("/ws/caption-table?name=Bo") as bo:
                audio = b"\x01\x00" * BYTES_PER_SECOND
                ann.send_bytes(audio[:1000])
                ann.send_bytes(audio[1000:])
                ann.send_json({"type": "speech-end"})

                for socket in (ann, bo):
                    caption = receive_until(socket, "caption")
                    assert caption["playerId"] == ann_id
                    assert caption["name"] == "Ann"
                    assert caption["text"] == "three kings"
    assert spoken == [audio]


def calibrating(value):
    return {"value": value, "change": None, "trend": "calibrating"}


def test_captions_say_how_the_speaker_sounded(spoken, monkeypatch):
    heard = []

    def fake_measure(pcm, text):
        heard.append((pcm, text))
        return {"pitch": 180.0, "speed": 4.26, "fillers": 1}

    monkeypatch.setattr(main, "measure_voice", fake_measure)
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/voice-table?name=Ann") as ann:
            with client.websocket_connect("/ws/voice-table?name=Bo") as bo:
                audio = b"\x01\x00" * BYTES_PER_SECOND
                ann.send_bytes(audio)
                ann.send_json({"type": "speech-end"})
                for socket in (ann, bo):
                    voice = receive_until(socket, "caption")["voice"]
                    assert voice["pitch"] == calibrating(180)
                    assert voice["speed"] == calibrating(4.3)
                    assert voice["fillers"] == 1
    assert heard == [(audio, "three kings")]


def test_what_cannot_be_measured_is_left_out(spoken):
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/flat-table?name=Ann") as ann:
            # A constant signal has no pitch, and two words are too few to time.
            ann.send_bytes(b"\x01\x00" * BYTES_PER_SECOND)
            ann.send_json({"type": "speech-end"})
            voice = receive_until(ann, "caption")["voice"]
            assert voice["pitch"] is None
            assert voice["speed"] is None
            assert voice["fillers"] == 0


def test_sounds_without_words_are_not_measured(monkeypatch):
    measured = []
    monkeypatch.setattr(main, "transcribe", lambda _pcm: Transcription("", False))
    monkeypatch.setattr(main, "measure_voice", lambda *args: measured.append(args) or {})
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/cough-table?name=Ann") as ann:
            ann.send_bytes(b"\x01\x00" * BYTES_PER_SECOND)
            ann.send_json({"type": "speech-end"})
            # A later message still works, proving the speech was dealt with.
            ann.send_json({"type": "start"})
            assert receive_until(ann, "error")["message"].startswith("Need at least")
    assert measured == []


def test_measuring_failure_does_not_stop_captions(spoken, monkeypatch):
    def broken(_pcm, _text):
        raise RuntimeError("praat failed")

    monkeypatch.setattr(main, "measure_voice", broken)
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/unmeasured-table?name=Ann") as ann:
            ann.send_bytes(b"\x01\x00" * BYTES_PER_SECOND)
            ann.send_json({"type": "speech-end"})
            caption = receive_until(ann, "caption")
            assert caption["text"] == "three kings"
            assert caption["voice"]["pitch"] is None


def test_too_little_audio_is_ignored(spoken):
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/short-table?name=Ann") as ann:
            ann.send_bytes(b"\x00\x00" * 10)
            ann.send_json({"type": "speech-end"})
            # A later message still works, proving nothing is stuck or queued.
            ann.send_json({"type": "start"})
            assert receive_until(ann, "error")["message"].startswith("Need at least")
    assert spoken == []


def test_endless_speech_is_cut_off(spoken):
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/long-table?name=Ann") as ann:
            ann.send_bytes(b"\x01\x00" * (main.MAX_SPEECH_BYTES // 2))
            assert receive_until(ann, "caption")["text"] == "three kings"
    assert len(spoken[0]) == main.MAX_SPEECH_BYTES


def test_transcription_failure_tells_the_speaker(monkeypatch):
    def broken(_pcm):
        raise RuntimeError("no model")

    monkeypatch.setattr(main, "transcribe", broken)
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/broken-table?name=Ann") as ann:
            ann.send_bytes(b"\x01\x00" * BYTES_PER_SECOND)
            ann.send_json({"type": "speech-end"})
            assert receive_until(ann, "error")["message"] == "Captions are unavailable"
