"""Rules of the game, free of any networking.

Players take turns playing cards face down while claiming they all match the
table card, or calling the previous player a liar. Whoever is wrong about the
call points their own revolver at their head. Last player alive wins.

Everything secret lives here, and `view_for` decides what each player may see.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

TABLE_RANKS = ("Q", "K", "A")
JOKER = "J"
DECK = TABLE_RANKS * 6 + (JOKER,) * 2
HAND_SIZE = 5
MAX_CARDS_PER_PLAY = 3
CHAMBERS = 6


class Phase(StrEnum):
    PLAYING = "playing"
    REVEAL = "reveal"
    ROULETTE = "roulette"
    SHOT = "shot"
    OVER = "over"


class IllegalMove(Exception):
    """The move breaks the rules; the message is shown to the player."""


@dataclass(frozen=True)
class Card:
    id: int
    rank: str


@dataclass
class Player:
    id: str
    name: str
    bullet: int
    hand: list[Card]
    shots: int = 0
    alive: bool = True


@dataclass(frozen=True)
class Play:
    player_id: str
    cards: tuple[Card, ...]
    seq: int


@dataclass(frozen=True)
class Reveal:
    caller_id: str
    accused_id: str
    ranks: tuple[str, ...]
    lied: bool
    loser_id: str


@dataclass(frozen=True)
class Shot:
    player_id: str
    fired: bool


class Game:
    def __init__(
        self,
        players: list[tuple[str, str]],
        rng: random.Random | None = None,
        on_event: Callable[..., None] | None = None,
    ) -> None:
        # Called with an event name and its details whenever something happens.
        self.on_event = on_event or (lambda event, **fields: None)
        self.auto_move = False
        # Card shuffles and bullets are the stakes, so use an unguessable source.
        self.rng = rng or random.SystemRandom()
        self.players = [
            Player(id, name, self.rng.randrange(CHAMBERS), [])
            for id, name in players
        ]
        self.round = 0
        self.play_seq = 0
        self.winner_id: str | None = None
        self.on_event(
            "game-start",
            players=[{"id": player.id, "name": player.name} for player in self.players],
        )
        self._start_round(0)

    @property
    def current(self) -> Player:
        return self.players[self.current_index]

    @property
    def alive(self) -> list[Player]:
        return [player for player in self.players if player.alive]

    @property
    def must_call(self) -> bool:
        """Nobody else could respond to another play, so it has to be called."""
        return self.last_play is not None and not any(
            player.hand for player in self.alive if player is not self.current
        )

    def play(self, player_id: str, card_ids: object) -> None:
        self._require_turn(player_id)
        if self.must_call:
            raise IllegalMove("Everyone else is out of cards: you must call Liar!")
        # Exact int check: JSON `true` would otherwise count as card 1.
        if not isinstance(card_ids, list) or not all(type(id) is int for id in card_ids):
            raise IllegalMove("You don't have those cards")
        if not 1 <= len(set(card_ids)) == len(card_ids) <= MAX_CARDS_PER_PLAY:
            raise IllegalMove(f"Play between 1 and {MAX_CARDS_PER_PLAY} cards")

        hand = {card.id: card for card in self.current.hand}
        if not all(id in hand for id in card_ids):
            raise IllegalMove("You don't have those cards")

        self.current.hand = [card for card in self.current.hand if card.id not in card_ids]
        self.play_seq += 1
        self.last_play = Play(
            player_id, tuple(hand[id] for id in card_ids), self.play_seq
        )
        self._pass_turn()
        self.on_event(
            "play",
            round=self.round,
            playerId=player_id,
            ranks=[card.rank for card in self.last_play.cards],
            lied=self._is_lie(self.last_play),
            cardsLeft=len(hand) - len(card_ids),
            nextPlayerId=self.current.id,
            auto=self.auto_move,
        )

    def call_liar(self, player_id: str) -> None:
        self._require_turn(player_id)
        if self.last_play is None:
            raise IllegalMove("There is nothing to call yet")

        forced = self.must_call
        accused_id = self.last_play.player_id
        lied = self._is_lie(self.last_play)
        self.reveal = Reveal(
            caller_id=player_id,
            accused_id=accused_id,
            ranks=tuple(card.rank for card in self.last_play.cards),
            lied=lied,
            loser_id=accused_id if lied else player_id,
        )
        self.phase = Phase.REVEAL
        self.on_event(
            "call",
            round=self.round,
            callerId=player_id,
            accusedId=accused_id,
            ranks=list(self.reveal.ranks),
            lied=lied,
            loserId=self.reveal.loser_id,
            forced=forced,
            auto=self.auto_move,
        )

    def timeout(self) -> None:
        """The current player ran out of time: make the least committal move."""
        self.auto_move = True
        if self.must_call:
            self.call_liar(self.current.id)
        else:
            self.play(self.current.id, [self.rng.choice(self.current.hand).id])
        self.auto_move = False

    def advance(self) -> None:
        """Moves on from a phase that only exists to be watched."""
        if self.phase is Phase.REVEAL:
            self.phase = Phase.ROULETTE
        elif self.phase is Phase.ROULETTE:
            self._pull_trigger()
        elif self.phase is Phase.SHOT:
            self._next_round_after(self._player_index(self.shot.player_id))

    def remove_player(self, player_id: str) -> None:
        """A player left. A leaver forfeits, and the round in progress is void
        whenever it depended on them."""
        index = self._player_index(player_id)
        self.players[index].alive = False
        self.players[index].hand = []
        self.on_event("player-left", round=self.round, playerId=player_id)

        if self.phase is Phase.OVER:
            return
        if len(self.alive) <= 1:
            self._finish()
        elif self.phase is Phase.PLAYING or (
            self.phase in (Phase.REVEAL, Phase.ROULETTE)
            and self.reveal.loser_id == player_id
        ):
            self._next_round_after(index)

    def view_for(self, player_id: str) -> dict:
        """Game state as seen by one player. Never includes other players'
        hands, unrevealed plays or bullet positions."""
        playing = self.phase is Phase.PLAYING
        my_turn = playing and self.current.id == player_id
        hand = next((p.hand for p in self.players if p.id == player_id), [])
        return {
            "phase": self.phase.value,
            "round": self.round,
            "tableRank": self.table_rank,
            "currentPlayerId": self.current.id if playing else None,
            "hand": [{"id": card.id, "rank": card.rank} for card in hand],
            "players": [
                {
                    "id": player.id,
                    "name": player.name,
                    "alive": player.alive,
                    "cards": len(player.hand),
                    "shots": player.shots,
                }
                for player in self.players
            ],
            "lastPlay": self.last_play
            and {
                "playerId": self.last_play.player_id,
                "count": len(self.last_play.cards),
                "seq": self.last_play.seq,
            },
            "canCall": my_turn and self.last_play is not None,
            "mustCall": my_turn and self.must_call,
            "reveal": self.reveal
            and {
                "callerId": self.reveal.caller_id,
                "accusedId": self.reveal.accused_id,
                "ranks": list(self.reveal.ranks),
                "lied": self.reveal.lied,
                "loserId": self.reveal.loser_id,
            },
            "shot": self.shot
            and {"playerId": self.shot.player_id, "fired": self.shot.fired},
            "winnerId": self.winner_id,
        }

    def _is_lie(self, play: Play) -> bool:
        return any(card.rank not in (self.table_rank, JOKER) for card in play.cards)

    def _require_turn(self, player_id: str) -> None:
        if self.phase is not Phase.PLAYING or self.current.id != player_id:
            raise IllegalMove("It isn't your turn")

    def _player_index(self, player_id: str) -> int:
        return next(i for i, p in enumerate(self.players) if p.id == player_id)

    def _start_round(self, first_index: int) -> None:
        self.round += 1
        self.table_rank = self.rng.choice(TABLE_RANKS)
        self.current_index = first_index
        self.last_play: Play | None = None
        self.reveal: Reveal | None = None
        self.shot: Shot | None = None
        self.phase = Phase.PLAYING

        ranks = list(DECK)
        self.rng.shuffle(ranks)
        deck = iter(Card(id, rank) for id, rank in enumerate(ranks))
        for player in self.players:
            player.hand = [next(deck) for _ in range(HAND_SIZE)] if player.alive else []
        self.on_event(
            "round-start",
            round=self.round,
            tableRank=self.table_rank,
            firstPlayerId=self.current.id,
            hands={
                player.id: [card.rank for card in player.hand]
                for player in self.alive
            },
        )

    def _next_index(self, index: int, condition) -> int:
        count = len(self.players)
        return next(
            i % count
            for i in range(index + 1, index + count + 1)
            if condition(self.players[i % count])
        )

    def _pass_turn(self) -> None:
        self.current_index = self._next_index(
            self.current_index, lambda player: player.alive and player.hand
        )

    def _pull_trigger(self) -> None:
        loser = self.players[self._player_index(self.reveal.loser_id)]
        fired = loser.shots == loser.bullet
        loser.shots += 1
        if fired:
            loser.alive = False
            loser.hand = []
        self.shot = Shot(loser.id, fired)
        self.phase = Phase.SHOT
        self.on_event(
            "shot", round=self.round, playerId=loser.id, fired=fired, pull=loser.shots
        )

    def _next_round_after(self, index: int) -> None:
        """The player at `index` starts the next round, or the next player
        along if they are gone."""
        if len(self.alive) <= 1:
            self._finish()
            return
        first = index
        if not self.players[index].alive:
            first = self._next_index(index, lambda player: player.alive)
        self._start_round(first)

    def _finish(self) -> None:
        self.winner_id = self.alive[0].id if self.alive else None
        self.phase = Phase.OVER
        self.on_event("game-over", round=self.round, winnerId=self.winner_id)
