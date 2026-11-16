"""Measures how players speak, to compare each utterance with their usual voice."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from math import log2

import numpy as np
import parselmouth as pm

from captions import SAMPLE_RATE, Word

MIN_VOICED_FRAMES = 30  # About 0.3 s of voiced speech at Praat's 10 ms time step.

# A gap between words at least this long is a pause rather than normal rhythm.
PAUSE_SECONDS = 0.3
# Fewer words than this are over too quickly to time.
MIN_WORDS_FOR_SPEED = 3

# Readings stay "calibrating" until a player has this many of them.
MIN_BASELINE_UTTERANCES = 5


def _semitones(value: float, usual: float) -> float:
    # Semitones mean the same change for deep and high voices; Hz don't.
    return 12 * log2(value / usual)


def _percent(value: float, usual: float) -> float:
    return (value / usual - 1) * 100 if usual > 0 else 0.0


@dataclass(frozen=True)
class Feature:
    """One measurement of an utterance, and how to compare two values of it."""

    # How far a value is from the usual one, in the unit players are shown.
    difference: Callable[[float, float], float]
    # The smallest difference that counts as a change.
    noticeable: float
    digits: int = 1


FEATURES = {
    # Median pitch in Hz, compared in semitones.
    "pitch": Feature(_semitones, noticeable=1.5, digits=0),
    # Syllables per second while talking, compared in percent.
    "speed": Feature(_percent, noticeable=15),
}


def _pitch(sound: pm.Sound) -> float | None:
    pitch = sound.to_pitch(pitch_floor=75, pitch_ceiling=500)
    frequencies = pitch.selected_array["frequency"]
    voiced = frequencies[frequencies > 0]  # Unvoiced frames are 0.
    if len(voiced) < MIN_VOICED_FRAMES:
        return None
    # The median ignores the odd frame where the tracker jumps an octave.
    return float(np.median(voiced))


def measure_voice(pcm: bytes) -> dict[str, float]:
    """
    Measures the sound of an utterance.

    Parameters:
    pcm (bytes): 16 kHz 16-bit mono PCM audio.

    Returns:
    dict: A value for each of FEATURES that could be measured from the sound.
    Nothing can be when too little of it was voiced.
    """
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768
    sound = pm.Sound(samples, sampling_frequency=SAMPLE_RATE)

    measured = {}
    hz = _pitch(sound)
    if hz is not None:
        measured["pitch"] = hz
    return measured


def _syllables(word: str) -> int:
    """A rough count, which is enough to stop long words looking like slow speech."""
    word = word.lower()
    count = len(re.findall(r"[aeiouy]+", word))
    if count > 1 and word.endswith("e") and not word.endswith("le"):
        count -= 1  # A final "e" is usually silent.
    return max(1, count)


def measure_delivery(words: Sequence[Word]) -> dict[str, float]:
    """Measures the timing of an utterance, from when each word was said."""
    measured = {}
    if len(words) < 2:
        return measured

    gaps = [after.start - before.end for before, after in zip(words, words[1:])]
    paused = sum(gap for gap in gaps if gap >= PAUSE_SECONDS)
    # Pauses are left out, so that hesitating doesn't also count as talking slowly.
    talking = words[-1].end - words[0].start - paused
    if len(words) >= MIN_WORDS_FOR_SPEED and talking > 0:
        measured["speed"] = sum(_syllables(word.text) for word in words) / talking
    return measured


@dataclass
class VoiceBaseline:
    """A player's usual voice, learned from everything they have said."""

    history: dict[str, list[float]] = field(default_factory=dict)

    def compare(self, measured: dict[str, float]) -> dict:
        """Compares an utterance with the player's earlier ones, then remembers it."""
        return {
            name: self._reading(name, measured[name]) if name in measured else None
            for name in FEATURES
        }

    def _reading(self, name: str, value: float) -> dict:
        feature = FEATURES[name]
        history = self.history.setdefault(name, [])
        reading = {
            "value": round(value, feature.digits),
            "change": None,
            "trend": "calibrating",
        }
        if len(history) >= MIN_BASELINE_UTTERANCES:
            change = feature.difference(value, float(np.median(history)))
            reading["change"] = round(change, 1)
            if change >= feature.noticeable:
                reading["trend"] = "higher"
            elif change <= -feature.noticeable:
                reading["trend"] = "lower"
            else:
                reading["trend"] = "steady"
        history.append(value)
        return reading
