"""Measures how players speak, to compare each utterance with their usual voice."""

from dataclasses import dataclass, field
from math import log2

import numpy as np
import parselmouth as pm

from captions import SAMPLE_RATE

MIN_VOICED_FRAMES = 30  # About 0.3 s of voiced speech at Praat's 10 ms time step.

# Readings stay "calibrating" until a player has said this many things.
MIN_BASELINE_UTTERANCES = 5
# How far from their usual pitch a player must be for it to count as a change.
NOTICEABLE_SEMITONES = 1.5


def measure_pitch(pcm: bytes) -> float | None:
    """
    Measure the pitch of an utterance using parselmouth.

    Parameters:
    pcm (bytes): 16 kHz 16-bit mono PCM audio.

    Returns:
    float | None: The median pitch in Hz, or None if too little of it was voiced.
    """
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768

    sound = pm.Sound(samples, sampling_frequency=SAMPLE_RATE)
    pitch = sound.to_pitch(pitch_floor=75, pitch_ceiling=500)

    frequencies = pitch.selected_array["frequency"]
    voiced = frequencies[frequencies > 0]  # Unvoiced frames are 0.

    if len(voiced) < MIN_VOICED_FRAMES:
        return None

    # The median ignores the odd frame where the tracker jumps an octave.
    return float(np.median(voiced))


@dataclass
class PitchBaseline:
    """A player's usual pitch, learned from everything they have said."""

    history: list[float] = field(default_factory=list)

    def compare(self, hz: float) -> dict:
        """Compares an utterance with the player's earlier ones, then remembers it."""
        result = {"hz": round(hz), "semitones": None, "trend": "calibrating"}
        if len(self.history) >= MIN_BASELINE_UTTERANCES:
            usual = float(np.median(self.history))
            # Semitones mean the same change for deep and high voices; Hz don't.
            semitones = 12 * log2(hz / usual)
            result["semitones"] = round(semitones, 1)
            if semitones >= NOTICEABLE_SEMITONES:
                result["trend"] = "higher"
            elif semitones <= -NOTICEABLE_SEMITONES:
                result["trend"] = "lower"
            else:
                result["trend"] = "steady"
        self.history.append(hz)
        return result
