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
GAME_WORDS = "Queens, Kings, Aces, Joker, liar, bluff, revolver"
MAX_NO_SPEECH_PROBABILITY = 0.6


@dataclass(frozen=True)
class Word:
    text: str
    # Seconds from the start of the audio.
    start: float
    end: float


@dataclass(frozen=True)
class Transcript:
    text: str
    # When each word was said, for measuring talking speed and pauses.
    words: tuple[Word, ...] = ()


@lru_cache(maxsize=1)
def load_model():
    """Loads the model, downloading it the first time it is ever used."""
    from faster_whisper import WhisperModel

    return WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")


def transcribe(pcm: bytes) -> Transcript:
    """Returns the words spoken in the audio; the text is empty if none were."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    segments, _ = load_model().transcribe(
        samples,
        language="en" if MODEL_NAME.endswith(".en") else None,
        beam_size=1,
        # Whisper invents words when given noise, so only show it actual speech.
        vad_filter=True,
        condition_on_previous_text=False,
        hotwords=GAME_WORDS,
        word_timestamps=True,
    )
    spoken = [
        segment for segment in segments if segment.no_speech_prob < MAX_NO_SPEECH_PROBABILITY
    ]
    return Transcript(
        " ".join(segment.text.strip() for segment in spoken).strip(),
        tuple(
            Word(word.word.strip(), word.start, word.end)
            for segment in spoken
            for word in segment.words or ()
        ),
    )
