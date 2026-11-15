"""Measures how players speak, to compare each utterance with their usual voice."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from math import log2

import numpy as np
import parselmouth as pm

from captions import SAMPLE_RATE

MIN_VOICED_FRAMES = 30  # About 0.3 s of voiced speech at Praat's 10 ms time step.

# Loudness is judged in slices this long, to find where the talking stops.
SLICE_SECONDS = 0.01
# A slice this much quieter than the loudest one is silence, as in Praat.
SILENCE_DB = 25.0
# Silence at least this long is a pause; the gaps inside words are shorter.
PAUSE_SECONDS = 0.3
# Less sound than this is too little to time.
MIN_SOUNDING_SECONDS = 0.3
# Fewer words than this are over too quickly for a speed to mean much.
MIN_WORDS_FOR_SPEED = 3

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm", "mm"}

# Readings stay "calibrating" until a player has this many of them.
MIN_BASELINE_UTTERANCES = 5


def _semitones(value: float, usual: float) -> float:
    # Semitones mean the same change for deep and high voices; Hz don't.
    return 12 * log2(value / usual)


def _percent(value: float, usual: float) -> float:
    return (value / usual - 1) * 100 if usual > 0 else 0.0


def _points(value: float, usual: float) -> float:
    return value - usual


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
    # Percent of the utterance spent silent, compared in percentage points.
    "pauses": Feature(_points, noticeable=15, digits=0),
}


def _pitch(sound: pm.Sound) -> float | None:
    pitch = sound.to_pitch(pitch_floor=75, pitch_ceiling=500)
    frequencies = pitch.selected_array["frequency"]
    voiced = frequencies[frequencies > 0]  # Unvoiced frames are 0.
    if len(voiced) < MIN_VOICED_FRAMES:
        return None
    # The median ignores the odd frame where the tracker jumps an octave.
    return float(np.median(voiced))


def _timing(samples: np.ndarray) -> tuple[float, float] | None:
    """
    Seconds spent talking and pausing, between the first and last sound.

    This listens for silence rather than using Whisper's word timings, which
    stretch the neighbouring words over a pause instead of leaving a gap.
    """
    size = int(SAMPLE_RATE * SLICE_SECONDS)
    slices = samples[: len(samples) // size * size].reshape(-1, size)
    if len(slices) == 0:
        return None
    power = (slices**2).mean(axis=1)
    if power.max() == 0:
        return None
    sounding = power > power.max() / 10 ** (SILENCE_DB / 10)
    (indexes,) = np.nonzero(sounding)
    if len(indexes) * SLICE_SECONDS < MIN_SOUNDING_SECONDS:
        return None

    paused = 0.0
    for gap in np.diff(indexes) - 1:
        if gap * SLICE_SECONDS >= PAUSE_SECONDS:
            paused += gap * SLICE_SECONDS
    span = float(indexes[-1] - indexes[0] + 1) * SLICE_SECONDS
    return span - paused, float(paused)


def _syllables(word: str) -> int:
    """A rough count, which is enough to stop long words looking like slow speech."""
    count = len(re.findall(r"[aeiouy]+", word))
    if count > 1 and word.endswith("e") and not word.endswith("le"):
        count -= 1  # A final "e" is usually silent.
    return max(1, count)


def measure_voice(pcm: bytes, text: str = "") -> dict[str, float]:
    """
    Measures how an utterance was spoken.

    Parameters:
    pcm (bytes): 16 kHz 16-bit mono PCM audio.
    text (str): The words Whisper heard in it.

    Returns:
    dict: A value for each of FEATURES that could be measured, and `fillers`,
    the number of times the player said "um" or similar.
    """
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768
    words = re.findall(r"[a-z0-9']+", text.lower())
    measured = {"fillers": sum(word in FILLERS for word in words)}

    hz = _pitch(pm.Sound(samples, sampling_frequency=SAMPLE_RATE))
    if hz is not None:
        measured["pitch"] = hz

    timing = _timing(samples)
    if timing:
        talking, paused = timing
        measured["pauses"] = paused / (talking + paused) * 100
        # Pauses are left out, so that hesitating doesn't also count as slow.
        if len(words) >= MIN_WORDS_FOR_SPEED:
            measured["speed"] = sum(map(_syllables, words)) / talking
    return measured


@dataclass
class VoiceBaseline:
    """A player's usual voice, learned from everything they have said."""

    history: dict[str, list[float]] = field(default_factory=dict)

    def compare(self, measured: dict[str, float]) -> dict:
        """Compares an utterance with the player's earlier ones, then remembers it."""
        readings = {
            name: self._reading(name, measured[name]) if name in measured else None
            for name in FEATURES
        }
        # Not compared with a baseline: most utterances have none at all.
        return readings | {"fillers": int(measured.get("fillers", 0))}

    def _reading(self, name: str, value: float) -> dict:
        feature = FEATURES[name]
        history = self.history.setdefault(name, [])
        reading = {
            # Rounding to no digits gives a whole number rather than e.g. 94.0.
            "value": round(value, feature.digits or None),
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
