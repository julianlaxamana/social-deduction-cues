
import pytest
from fastapi.testclient import TestClient

import game
import game_log
import main
from game import Phase
from recap import build_recap

ANN = {"id": "a", "name": "Ann"}
BO = {"id": "b", "name": "Bo"}


def events_of(*events, players=(ANN, BO), winner="a"):
    return [
        {"t": 0, "event": "log-start", "startedAt": "2026-01-01T00:00:00"},
        {"t": 0, "event": "game-start", "players": list(players)},
        {"t": 0, "event": "round-start", "round": 1},
        *events,
        {"t": 999, "event": "game-over", "round": 1, "winnerId": winner},
    ]


def play(t, player, lied, auto=False):
    return {"t": t, "event": "play", "playerId": player, "lied": lied, "auto": auto}


def call(t, caller, accused, lied, forced=False, auto=False):
    return {
        "t": t, "event": "call", "callerId": caller, "accusedId": accused,
        "lied": lied, "forced": forced, "auto": auto,
    }


def shot(t, player, fired):
    return {"t": t, "event": "shot", "playerId": player, "fired": fired}


def speech(t, end, player, text):
    return {"t": t, "end": end, "event": "speech", "playerId": player, "text": text}


def stats(recap, player_id):
    return next(player for player in recap["players"] if player["id"] == player_id)


def highlight(recap, title):
    return next((h["text"] for h in recap["highlights"] if h["title"] == title), None)


def test_counts_lies_including_ones_nobody_called():
    recap = build_recap(events_of(play(1, "a", True), play(2, "b", False), play(3, "a", True),
                             call(4, "b", "a", True)))
    ann = stats(recap, "a")
    assert (ann["plays"], ann["lies"], ann["caught"]) == (2, 2, 1)
    assert highlight(recap, "Biggest liar") == "Ann told 2 lies."
    assert highlight(recap, "Smoothest") == "Ann got away with 1 lie."
    assert highlight(recap, "Sharpest eye") == "Bo caught 1 liar."


def test_wrong_calls_count_against_the_caller():
    recap = build_recap(events_of(play(1, "a", False), call(2, "b", "a", False)))
    assert stats(recap, "b")["calls"] == 1
    assert stats(recap, "b")["correctCalls"] == 0
    assert highlight(recap, "Worst accuser") == "Bo wrongly called out 1 honest play."


def test_timer_moves_and_forced_calls_are_not_held_against_anyone():
    recap = build_recap(events_of(play(1, "a", True, auto=True), call(2, "b", "a", True, forced=True)))
    ann, bo = stats(recap, "a"), stats(recap, "b")
    assert (ann["plays"], ann["lies"], ann["caught"]) == (0, 0, 0)
    assert bo["calls"] == 0
    assert recap["highlights"] == []


def test_ties_share_a_highlight():
    recap = build_recap(events_of(play(1, "a", True), play(2, "b", True)))
    assert highlight(recap, "Biggest liar") == "Ann and Bo told 1 lie."


def test_luck_ignores_the_shot_that_killed_you():
    recap = build_recap(events_of(shot(1, "b", False), shot(2, "b", False), shot(3, "b", True)))
    assert stats(recap, "b")["pulls"] == 3
    assert highlight(recap, "Luckiest") == "Bo survived 2 trigger pulls."


def test_players_are_ordered_winner_first_then_by_who_lasted_longest():
    cy = {"id": "c", "name": "Cy"}
    recap = build_recap(
        events_of(shot(1, "b", True), {"t": 2, "event": "player-left", "playerId": "c"},
             players=(ANN, BO, cy))
    )
    assert [(p["id"], p["outcome"]) for p in recap["players"]] == [
        ("a", "won"), ("c", "left"), ("b", "shot"),
    ]


def test_dying_then_leaving_still_counts_as_shot():
    recap = build_recap(events_of(shot(1, "b", True), {"t": 2, "event": "player-left", "playerId": "b"}))
    assert stats(recap, "b")["outcome"] == "shot"


def test_reports_thinking_time_tell_with_its_evidence():
    recap = build_recap(events_of(
        play(10, "a", True), play(11, "b", False), play(21, "a", True), play(22, "b", False),
        play(23, "a", False), play(24, "b", False), play(25, "a", False),
    ))
    assert recap["tells"] == [
        "Ann took 9s longer to decide when lying, over 2 lies and 2 honest plays."
    ]


def test_one_example_is_not_a_pattern():
    recap = build_recap(events_of(play(30, "a", True), play(31, "b", False), play(32, "a", False)))
    assert recap["tells"] == []


def test_small_differences_are_not_reported():
    recap = build_recap(events_of(
        play(3, "a", True), play(3.5, "b", False), play(6.5, "a", True), play(7, "b", False),
        play(9, "a", False), play(9.5, "b", False), play(11.5, "a", False),
    ))
    assert recap["tells"] == []


def test_reports_talking_tell_using_only_words_said_on_that_turn():
    ten_words = "one two three four five six seven eight nine ten"
    recap = build_recap(events_of(
        speech(0, 1, "a", ten_words),
        play(2, "a", True),
        speech(2.2, 2.4, "a", "said on bo's turn so it does not count"),
        play(3, "b", False),
        speech(3, 4, "a", ten_words),
        play(5, "a", True),
        play(6, "b", False),
        play(7, "a", False),
        play(8, "b", False),
        play(9, "a", False),
    ))
    assert recap["tells"] == [
        "Ann said about 10 more words per turn when lying, over 2 lies and 2 honest plays."
    ]


def test_no_talking_tell_for_players_whose_speech_was_not_saved():
    recap = build_recap(events_of(
        play(1, "a", True), play(2, "b", False), play(3, "a", True), play(4, "b", False),
        play(5, "a", False), play(6, "b", False), play(7, "a", False),
    ))
    assert recap["tells"] == []


def test_chattiest_needs_more_than_one_player_who_opted_in():
    alone = build_recap(events_of(speech(1, 2, "a", "hello there")))
    assert highlight(alone, "Chattiest") is None
    both = build_recap(events_of(speech(1, 2, "a", "hello there"), speech(3, 4, "b", "hi")))
    assert highlight(both, "Chattiest") == "Ann said 2 words."


def receive_until(socket, check):
    while True:
        message = socket.receive_json()
        if message["type"] == "state" and check(message):
            return message


def test_server_sends_recap_at_game_over_and_clears_it_on_the_next_deal(tmp_path, monkeypatch):
    monkeypatch.setattr(game_log, "LOG_DIR", tmp_path)
    # Every revolver fires on its first pull, so the first call ends the game.
    monkeypatch.setattr(game, "CHAMBERS", 1)
    for phase in (Phase.REVEAL, Phase.ROULETTE, Phase.SHOT, Phase.OVER):
        monkeypatch.setitem(main.PHASE_SECONDS, phase, 0.05)

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/recap-table?name=Ann") as ann:
            with client.websocket_connect("/ws/recap-table?name=Bo") as bo:
                ann.send_json({"type": "start"})
                dealt = receive_until(ann, lambda m: m["game"])
                assert dealt["recap"] is None
                # The game starts with Ann, who joined first.
                card = dealt["game"]["hand"][0]["id"]
                ann.send_json({"type": "play", "cards": [card]})
                receive_until(bo, lambda m: m["game"] and m["game"]["canCall"])
                bo.send_json({"type": "call"})

                over = receive_until(ann, lambda m: m["recap"])
                assert over["game"]["phase"] == "over"
                assert over["recap"]["winnerId"] == over["game"]["winnerId"]
                assert over["recap"]["players"][0]["outcome"] == "won"
                assert over["recap"]["players"][1]["outcome"] == "shot"

                lobby = receive_until(ann, lambda m: m["game"] is None)
                assert lobby["recap"]["id"] == over["recap"]["id"]

                ann.send_json({"type": "start"})
                next_game = receive_until(ann, lambda m: m["game"])
                assert next_game["recap"] is None
