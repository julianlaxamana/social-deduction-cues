import numpy as np
import pytest

from captions import SAMPLE_RATE
from voice_features import MIN_BASELINE_UTTERANCES, PitchBaseline, measure_pitch


def tone(hz: float, seconds: float = 1.0) -> bytes:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * hz * t) * 32767).astype(np.int16).tobytes()


@pytest.mark.parametrize("hz", [110, 200, 320])
def test_measures_the_pitch_of_a_tone(hz):
    assert measure_pitch(tone(hz)) == pytest.approx(hz, rel=0.01)


def test_silence_has_no_pitch():
    assert measure_pitch(b"\x00\x00" * SAMPLE_RATE) is None


def test_too_short_a_sound_has_no_pitch():
    assert measure_pitch(tone(200, seconds=0.1)) is None


def test_calibrates_before_comparing():
    baseline = PitchBaseline()
    for _ in range(MIN_BASELINE_UTTERANCES):
        reading = baseline.compare(150)
        assert reading["trend"] == "calibrating"
        assert reading["semitones"] is None


def calibrated(hz: float) -> PitchBaseline:
    baseline = PitchBaseline()
    for _ in range(MIN_BASELINE_UTTERANCES):
        baseline.compare(hz)
    return baseline


def test_notices_a_raised_pitch():
    reading = calibrated(150).compare(180)
    assert reading == {"hz": 180, "semitones": 3.2, "trend": "higher"}


def test_notices_a_lowered_pitch():
    assert calibrated(150).compare(125)["trend"] == "lower"


def test_small_changes_are_steady():
    assert calibrated(150).compare(155)["trend"] == "steady"


def test_one_odd_utterance_barely_moves_the_baseline():
    baseline = calibrated(150)
    baseline.compare(300)
    assert baseline.compare(150)["trend"] == "steady"
