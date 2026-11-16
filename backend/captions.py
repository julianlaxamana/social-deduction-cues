"""Turns players' speech into text with Whisper. Audio is never stored."""

import os
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

# Speech arrives as 16-bit mono PCM, which is what Whisper expects.
SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2

# `.en` models are smaller and more accurate, but only understand English.
# Set WHISPER_MODEL to e.g. `base` for other languages, or `small.en` for accuracy.
MODEL_NAME = os.environ.get("WHISPER_MODEL", "base.en")
GAME_WORDS = "Queen, Queens, King, Kings, Ace, Aces, Joker, liar, bluff, revolver"
MAX_NO_SPEECH_PROBABILITY = 0.6
MAX_CLAIM_NO_SPEECH_PROBABILITY = 0.45
MIN_CLAIM_LOG_PROBABILITY = -0.85


@dataclass(frozen=True)
class Transcription:
    text: str
    reliable: bool


@lru_cache(maxsize=1)
def load_model():
    """Loads the model, downloading it the first time it is ever used."""
    from faster_whisper import WhisperModel

    return WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")


def transcribe(pcm: bytes) -> Transcription:
    """Returns caption text and whether Whisper supports using it as a claim."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    segments, _ = load_model().transcribe(
        samples,
        language="en" if MODEL_NAME.endswith(".en") else None,
        beam_size=1,
        # Whisper invents words when given noise, so only show it actual speech.
        vad_filter=True,
        vad_parameters={"min_speech_duration_ms": 100},
        condition_on_previous_text=False,
        hotwords=GAME_WORDS,
    )
    heard = [
        segment
        for segment in segments
        if segment.text.strip() and segment.no_speech_prob < MAX_NO_SPEECH_PROBABILITY
    ]
    return Transcription(
        " ".join(segment.text.strip() for segment in heard),
        bool(heard) and all(
            segment.avg_logprob >= MIN_CLAIM_LOG_PROBABILITY
            and segment.no_speech_prob < MAX_CLAIM_NO_SPEECH_PROBABILITY
            for segment in heard
        ),
    )
