import json
import random

import pytest

from game import CHAMBERS, HAND_SIZE, JOKER, Card, Game, IllegalMove, Phase


def make_game(count=3, seed=1):
    players = [(f"p{i}", f"Player {i}") for i in range(count)]
    return Game(players, random.Random(seed))


def set_hand(game, player_index, ranks):
    start = 100 * (player_index + 1)
    game.players[player_index].hand = [
        Card(start + i, rank) for i, rank in enumerate(ranks)
    ]
    return [card.id for card in game.players[player_index].hand]


def wrong_rank(game):
    return next(rank for rank in ("Q", "K") if rank != game.table_rank)


def run_to_shot(game):
    game.advance()
    assert game.phase is Phase.ROULETTE
    game.advance()
    assert game.phase is Phase.SHOT


def test_deal_gives_every_player_a_hand_of_unique_cards():
    game = make_game(4)
    ids = [card.id for player in game.players for card in player.hand]
    assert all(len(player.hand) == HAND_SIZE for player in game.players)
    assert len(ids) == len(set(ids))
    assert game.table_rank != JOKER


def test_cannot_call_before_any_card_is_played():
    game = make_game()
    with pytest.raises(IllegalMove):
        game.call_liar("p0")


def test_cannot_play_out_of_turn():
    game = make_game()
    with pytest.raises(IllegalMove):
        game.play("p1", [game.players[1].hand[0].id])


@pytest.mark.parametrize(
    "cards",
    [[], None, "abc", [9999], [0.5], [[1]], [True], {"a": 1}],
)
def test_rejects_malformed_plays(cards):
    game = make_game()
    with pytest.raises(IllegalMove):
        game.play("p0", cards)
    assert len(game.players[0].hand) == HAND_SIZE


def test_rejects_too_many_and_duplicate_cards():
    game = make_game()
    ids = [card.id for card in game.players[0].hand]
    with pytest.raises(IllegalMove):
        game.play("p0", ids[:4])
    with pytest.raises(IllegalMove):
        game.play("p0", [ids[0], ids[0]])


def test_rejects_the_same_card_disguised_as_a_boolean():
    game = make_game()
    game.players[0].hand = [Card(1, game.table_rank), Card(2, game.table_rank)]
    with pytest.raises(IllegalMove):
        game.play("p0", [1, True])


def test_rejects_cards_from_another_hand():
    game = make_game()
    with pytest.raises(IllegalMove):
        game.play("p0", [game.players[1].hand[0].id])


def test_caught_lie_makes_the_liar_lose():
    game = make_game()
    ids = set_hand(game, 0, [wrong_rank(game), game.table_rank])
    game.play("p0", ids)
    game.call_liar("p1")
    assert game.phase is Phase.REVEAL
    assert game.reveal.lied
    assert game.reveal.loser_id == "p0"


@pytest.mark.parametrize("use_joker", [False, True])
def test_wrong_call_makes_the_caller_lose(use_joker):
    game = make_game()
    ids = set_hand(game, 0, [game.table_rank, JOKER if use_joker else game.table_rank])
    game.play("p0", ids)
    game.call_liar("p1")
    assert not game.reveal.lied
    assert game.reveal.loser_id == "p1"


def test_players_without_cards_are_skipped():
    game = make_game()
    set_hand(game, 1, [])
    game.play("p0", [game.players[0].hand[0].id])
    assert game.current.id == "p2"


def test_last_player_with_cards_must_call():
    game = make_game(2)
    ids = set_hand(game, 0, [game.table_rank])
    game.play("p0", ids)
    assert game.current.id == "p1"
    assert game.must_call
    with pytest.raises(IllegalMove):
        game.play("p1", [game.players[1].hand[0].id])
    game.call_liar("p1")
    assert game.phase is Phase.REVEAL


def test_timeout_plays_a_card_or_calls_when_forced():
    game = make_game(2)
    game.timeout()
    assert len(game.players[0].hand) == HAND_SIZE - 1
    assert game.current.id == "p1"

    set_hand(game, 0, [])
    game.timeout()
    assert game.phase is Phase.REVEAL


def test_revolver_fires_exactly_on_the_bullet_chamber():
    game = make_game(3)
    loser = game.players[0]
    loser.bullet = 2
    for pull, expect_fire in enumerate([False, False, True]):
        ids = set_hand(game, 0, [wrong_rank(game)])
        game.play("p0", ids)
        game.call_liar("p1")
        run_to_shot(game)
        assert game.shot.fired is expect_fire
        assert loser.shots == pull + 1
        assert loser.alive is not expect_fire
        game.advance()


def test_bullets_are_always_in_a_real_chamber():
    for seed in range(50):
        game = make_game(2, seed)
        assert all(0 <= player.bullet < CHAMBERS for player in game.players)


def test_survivor_starts_the_next_round_with_a_new_deal():
    game = make_game(3)
    game.players[0].bullet = 5
    ids = set_hand(game, 0, [wrong_rank(game)])
    game.play("p0", ids)
    game.call_liar("p1")
    run_to_shot(game)
    game.advance()
    assert game.round == 2
    assert game.phase is Phase.PLAYING
    assert game.current.id == "p0"
    assert game.last_play is None
    assert all(len(player.hand) == HAND_SIZE for player in game.players)


def test_death_passes_the_start_on_and_skips_the_dead():
    game = make_game(3)
    game.players[0].bullet = 0
    ids = set_hand(game, 0, [wrong_rank(game)])
    game.play("p0", ids)
    game.call_liar("p1")
    run_to_shot(game)
    assert game.shot.fired
    game.advance()
    assert game.current.id == "p1"
    assert game.players[0].hand == []


def test_last_player_alive_wins():
    game = make_game(2)
    game.players[0].bullet = 0
    ids = set_hand(game, 0, [wrong_rank(game)])
    game.play("p0", ids)
    game.call_liar("p1")
    run_to_shot(game)
    game.advance()
    assert game.phase is Phase.OVER
    assert game.winner_id == "p1"


def test_full_random_games_always_finish_with_one_winner():
    for seed in range(200):
        rng = random.Random(seed)
        game = make_game(rng.randint(2, 4), seed)
        for _ in range(10_000):
            if game.phase is Phase.OVER:
                break
            if game.phase is Phase.PLAYING:
                player = game.current
                if game.must_call or (game.last_play and rng.random() < 0.3):
                    game.call_liar(player.id)
                else:
                    count = rng.randint(1, min(3, len(player.hand)))
                    game.play(player.id, [c.id for c in rng.sample(player.hand, count)])
            else:
                game.advance()
        assert game.phase is Phase.OVER
        assert len(game.alive) == 1
        assert game.winner_id == game.alive[0].id


def test_leaving_on_your_turn_voids_the_round():
    game = make_game(3)
    game.play("p0", [game.players[0].hand[0].id])
    game.remove_player("p1")
    assert game.round == 2
    assert game.current.id == "p2"
    assert game.last_play is None


def test_leaving_during_your_own_punishment_skips_it():
    game = make_game(3)
    ids = set_hand(game, 0, [wrong_rank(game)])
    game.play("p0", ids)
    game.call_liar("p1")
    game.remove_player("p0")
    assert game.phase is Phase.PLAYING
    assert game.current.id == "p1"


def test_bystander_leaving_does_not_interrupt_a_punishment():
    game = make_game(3)
    ids = set_hand(game, 0, [wrong_rank(game)])
    game.play("p0", ids)
    game.call_liar("p1")
    game.remove_player("p2")
    assert game.phase is Phase.REVEAL
    assert game.reveal.loser_id == "p0"


def test_opponent_leaving_a_two_player_game_hands_over_the_win():
    game = make_game(2)
    game.remove_player("p0")
    assert game.phase is Phase.OVER
    assert game.winner_id == "p1"


def test_view_hides_other_hands_plays_and_bullets():
    game = make_game(3)
    played = game.players[0].hand[:2]
    game.play("p0", [card.id for card in played])

    view = game.view_for("p1")
    assert [card["id"] for card in view["hand"]] == [c.id for c in game.players[1].hand]
    assert view["lastPlay"] == {"playerId": "p0", "count": 2, "seq": 1}
    assert view["reveal"] is None
    for player in view["players"]:
        assert set(player) == {"id", "name", "alive", "cards", "shots"}
    assert "bullet" not in json.dumps(view)


def test_view_only_offers_actions_to_the_current_player():
    game = make_game(3)
    game.play("p0", [game.players[0].hand[0].id])
    assert game.view_for("p1")["canCall"] is True
    assert game.view_for("p0")["canCall"] is False
    assert game.view_for("p2")["canCall"] is False
