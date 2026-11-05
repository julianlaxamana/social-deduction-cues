import pytest
from fastapi.testclient import TestClient

import main
from captions import BYTES_PER_SECOND


@pytest.fixture
def spoken(monkeypatch):
    """Replaces Whisper, recording the audio it is given."""
    received = []

    def fake_transcribe(pcm):
        received.append(pcm)
        return "three kings"

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
