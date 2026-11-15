import numpy as np
import pytest

from captions import SAMPLE_RATE
from voice_features import MIN_BASELINE_UTTERANCES, VoiceBaseline, measure_voice


def tone(hz: float, seconds: float = 1.0) -> bytes:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * hz * t) * 32767).astype(np.int16).tobytes()


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


@pytest.mark.parametrize("hz", [110, 200, 320])
def test_measures_the_pitch_of_a_tone(hz):
    assert measure_voice(tone(hz))["pitch"] == pytest.approx(hz, rel=0.01)


def test_silence_has_nothing_to_measure():
    assert measure_voice(silence(1)) == {"fillers": 0}


def test_too_short_a_sound_has_nothing_to_measure():
    assert measure_voice(tone(200, seconds=0.1)) == {"fillers": 0}


def test_speed_is_syllables_per_second():
    # 1 + 2 + 1 syllables in 2 seconds.
    assert measure_voice(tone(200, 2), "Two jokers, played.")["speed"] == pytest.approx(2.0)


def test_a_couple_of_words_are_too_few_to_time():
    assert "speed" not in measure_voice(tone(200, 2), "Two kings.")


def test_pauses_are_the_share_of_time_spent_silent():
    hesitant = tone(200, 1) + silence(0.5) + tone(200, 0.5)
    assert measure_voice(hesitant)["pauses"] == pytest.approx(25, abs=1)


def test_pauses_do_not_count_as_talking_slowly():
    hesitant = tone(200, 1) + silence(0.5) + tone(200, 1)
    assert measure_voice(hesitant, "two jokers played")["speed"] == pytest.approx(2.0, rel=0.02)


def test_short_gaps_and_silence_around_the_speech_are_not_pauses():
    # The gaps inside words, and the quiet the browser sends before and after.
    clipped = silence(0.3) + tone(200, 0.5) + silence(0.1) + tone(200, 0.5) + silence(0.7)
    assert measure_voice(clipped)["pauses"] == 0


def test_counts_filler_words():
    assert measure_voice(silence(1), "Um, I played, uh, two Queens. Umbrella.")["fillers"] == 2


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


def test_notices_more_pausing_in_percentage_points():
    # Most people's usual is no pauses at all, which a ratio could not handle.
    reading = calibrated(pauses=0).compare({"pauses": 20})["pauses"]
    assert reading == {"value": 20, "change": 20, "trend": "higher"}


def test_each_feature_calibrates_separately():
    # Short remarks have a pitch but are too short to time.
    baseline = calibrated(pitch=150)
    readings = baseline.compare({"pitch": 150, "speed": 4.0})
    assert readings["pitch"]["trend"] == "steady"
    assert readings["speed"]["trend"] == "calibrating"


def test_what_was_not_measured_has_no_reading():
    assert calibrated(pitch=150).compare({})["pitch"] is None


def test_fillers_are_passed_on_as_a_count():
    baseline = VoiceBaseline()
    assert baseline.compare({"fillers": 2})["fillers"] == 2
    assert baseline.compare({})["fillers"] == 0
