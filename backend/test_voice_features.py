import numpy as np
import pytest

from captions import SAMPLE_RATE, Word
from voice_features import (
    MIN_BASELINE_UTTERANCES,
    VoiceBaseline,
    measure_delivery,
    measure_voice,
)


def tone(hz: float, seconds: float = 1.0) -> bytes:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * hz * t) * 32767).astype(np.int16).tobytes()


def words(*timed: tuple[str, float, float]) -> list[Word]:
    return [Word(*word) for word in timed]


@pytest.mark.parametrize("hz", [110, 200, 320])
def test_measures_the_pitch_of_a_tone(hz):
    assert measure_voice(tone(hz))["pitch"] == pytest.approx(hz, rel=0.01)


def test_silence_has_nothing_to_measure():
    assert measure_voice(b"\x00\x00" * SAMPLE_RATE) == {}


def test_too_short_a_sound_has_no_pitch():
    assert "pitch" not in measure_voice(tone(200, seconds=0.1))


def test_speed_is_syllables_per_second():
    # 1 + 2 + 1 syllables in 2 seconds.
    said = words(("two", 0.0, 0.5), ("jokers", 0.5, 1.5), ("played", 1.5, 2.0))
    assert measure_delivery(said)["speed"] == pytest.approx(2.0)


def test_pauses_do_not_count_as_talking_slowly():
    said = words(("two", 0.0, 0.5), ("jokers", 1.0, 2.0), ("played", 2.0, 2.5))
    assert measure_delivery(said)["speed"] == pytest.approx(2.0)


def test_a_couple_of_words_are_too_few_to_time():
    assert "speed" not in measure_delivery(words(("two", 0.0, 0.5), ("kings", 0.5, 1.0)))
    assert measure_delivery([]) == {}


def calibrated(**usual: float) -> VoiceBaseline:
    baseline = VoiceBaseline()
    for _ in range(MIN_BASELINE_UTTERANCES):
        baseline.compare(usual)
    return baseline


def test_calibrates_before_comparing():
    baseline = VoiceBaseline()
    for _ in range(MIN_BASELINE_UTTERANCES):
        reading = baseline.compare({"pitch": 150})["pitch"]
        assert reading == {"value": 150, "change": None, "trend": "calibrating"}


def test_notices_a_raised_pitch_in_semitones():
    reading = calibrated(pitch=150).compare({"pitch": 180})["pitch"]
    assert reading == {"value": 180, "change": 3.2, "trend": "higher"}


def test_notices_a_lowered_pitch():
    assert calibrated(pitch=150).compare({"pitch": 125})["pitch"]["trend"] == "lower"


def test_small_changes_are_steady():
    assert calibrated(pitch=150).compare({"pitch": 155})["pitch"]["trend"] == "steady"


def test_one_odd_utterance_barely_moves_the_baseline():
    baseline = calibrated(pitch=150)
    baseline.compare({"pitch": 300})
    assert baseline.compare({"pitch": 150})["pitch"]["trend"] == "steady"


def test_notices_faster_talking_in_percent():
    reading = calibrated(speed=4.0).compare({"speed": 5.0})["speed"]
    assert reading == {"value": 5.0, "change": 25.0, "trend": "higher"}


def test_each_feature_calibrates_separately():
    # Short remarks have a pitch but are too short to time.
    baseline = calibrated(pitch=150)
    readings = baseline.compare({"pitch": 150, "speed": 4.0})
    assert readings["pitch"]["trend"] == "steady"
    assert readings["speed"]["trend"] == "calibrating"


def test_what_was_not_measured_has_no_reading():
    assert calibrated(pitch=150).compare({})["pitch"] is None
