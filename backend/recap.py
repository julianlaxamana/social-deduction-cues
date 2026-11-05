"""Works out post-game stats from a game's logged events.

A move made by the turn timer is a random card rather than a decision, and a
forced call isn't a choice, so neither counts for or against a player.
"""

from dataclasses import dataclass, field
from statistics import mean

# A pattern needs at least this many examples of both lying and honest turns.
MIN_TURNS_FOR_TELL = 2
MIN_SECONDS_DIFFERENCE = 2.0
MIN_WORDS_DIFFERENCE = 3.0


@dataclass
class PlayerStats:
    id: str
    name: str
    outcome: str = "survived"
    eliminated_at: int = -1
    plays: int = 0
    lies: int = 0
    caught: int = 0
    calls: int = 0
    correct_calls: int = 0
    pulls: int = 0
    words: int | None = None
    # (seconds to decide, words said) for each of the player's plays.
    lying_turns: list[tuple[float, int]] = field(default_factory=list)
    honest_turns: list[tuple[float, int]] = field(default_factory=list)

    @property
    def survived_pulls(self) -> int:
        return self.pulls - (self.outcome == "shot")


def _words_between(speech: list[dict], player_id: str, start: float, end: float) -> int:
    """Words the player said during a turn, judged by the middle of each remark."""
    return sum(
        len(event["text"].split())
        for event in speech
        if event["playerId"] == player_id and start <= (event["t"] + event["end"]) / 2 <= end
    )


def _collect_stats(events: list[dict]) -> tuple[dict[str, PlayerStats], int]:
    speech = [event for event in events if event["event"] == "speech"]
    players: dict[str, PlayerStats] = {}
    rounds = 0
    turn_start = 0.0
    last_play_was_auto = False

    # Speech is written after transcribing, but it is filtered out above, and
    # everything else is written in the order that it happened.
    for order, event in enumerate(events):
        match event["event"]:
            case "game-start":
                players = {p["id"]: PlayerStats(p["id"], p["name"]) for p in event["players"]}
            case "round-start":
                rounds = event["round"]
                turn_start = event["t"]
            case "play":
                player = players[event["playerId"]]
                last_play_was_auto = event["auto"]
                if not event["auto"]:
                    turn = (
                        event["t"] - turn_start,
                        _words_between(speech, player.id, turn_start, event["t"]),
                    )
                    player.plays += 1
                    player.lies += event["lied"]
                    (player.lying_turns if event["lied"] else player.honest_turns).append(turn)
                turn_start = event["t"]
            case "call":
                if event["lied"] and not last_play_was_auto:
                    players[event["accusedId"]].caught += 1
                if not event["auto"] and not event["forced"]:
                    caller = players[event["callerId"]]
                    caller.calls += 1
                    caller.correct_calls += event["lied"]
            case "shot":
                player = players[event["playerId"]]
                player.pulls += 1
                if event["fired"]:
                    player.outcome = "shot"
                    player.eliminated_at = order
            case "player-left":
                player = players[event["playerId"]]
                if player.outcome == "survived":
                    player.outcome = "left"
                    player.eliminated_at = order
            case "game-over":
                if event["winnerId"] in players:
                    players[event["winnerId"]].outcome = "won"

    for event in speech:
        player = players[event["playerId"]]
        player.words = (player.words or 0) + len(event["text"].split())
    return players, rounds


def _join_names(players: list[PlayerStats]) -> str:
    names = [player.name for player in players]
    return " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + f" and {names[-1]}"


def _leaders(players: list[PlayerStats], score) -> list[PlayerStats]:
    best = max((score(player) for player in players), default=0)
    return [player for player in players if score(player) == best] if best > 0 else []


def _times(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _highlights(players: list[PlayerStats]) -> list[dict[str, str]]:
    highlights = []

    def add(title, score, describe):
        leaders = _leaders(players, score)
        if leaders:
            highlights.append(
                {"title": title, "text": f"{_join_names(leaders)} {describe(score(leaders[0]))}"}
            )

    add("Biggest liar", lambda p: p.lies, lambda n: f"told {_times(n, 'lie')}.")
    add("Smoothest", lambda p: p.lies - p.caught, lambda n: f"got away with {_times(n, 'lie')}.")
    add("Sharpest eye", lambda p: p.correct_calls, lambda n: f"caught {_times(n, 'liar')}.")
    add(
        "Worst accuser",
        lambda p: p.calls - p.correct_calls,
        lambda n: f"wrongly called out {_times(n, 'honest play')}.",
    )
    add("Luckiest", lambda p: p.survived_pulls, lambda n: f"survived {_times(n, 'trigger pull')}.")

    # Comparing talkers only means something if more than one chose to be counted.
    talkers = [player for player in players if player.words]
    if len(talkers) >= 2:
        best = max(player.words for player in talkers)
        chattiest = [player for player in talkers if player.words == best]
        highlights.append(
            {"title": "Chattiest", "text": f"{_join_names(chattiest)} said {best} words."}
        )
    return highlights


def _tells(player: PlayerStats) -> list[str]:
    """Differences between this player's lies and honest plays in this game."""
    lying, honest = player.lying_turns, player.honest_turns
    if len(lying) < MIN_TURNS_FOR_TELL or len(honest) < MIN_TURNS_FOR_TELL:
        return []

    basis = f"over {_times(len(lying), 'lie')} and {_times(len(honest), 'honest play')}"
    tells = []
    seconds = mean(t for t, _ in lying) - mean(t for t, _ in honest)
    if abs(seconds) >= MIN_SECONDS_DIFFERENCE:
        speed = "longer" if seconds > 0 else "less time"
        tells.append(f"{player.name} took {abs(seconds):.0f}s {speed} to decide when lying, {basis}.")

    # Without saved speech, every turn counts as zero words, which says nothing.
    if player.words:
        words = mean(w for _, w in lying) - mean(w for _, w in honest)
        if abs(words) >= MIN_WORDS_DIFFERENCE:
            amount = "more" if words > 0 else "fewer"
            tells.append(
                f"{player.name} said about {abs(words):.0f} {amount} words per turn "
                f"when lying, {basis}."
            )
    return tells


def build_recap(events: list[dict]) -> dict:
    players, rounds = _collect_stats(events)
    stats = list(players.values())
    # The winner first, then whoever lasted longest.
    placed = sorted(
        stats, key=lambda p: (p.outcome == "won", p.eliminated_at == -1, p.eliminated_at), reverse=True
    )
    winner = next((p for p in stats if p.outcome == "won"), None)
    return {
        # Identifies this game, so that a closed recap stays closed.
        "id": next((e["startedAt"] for e in events if e["event"] == "log-start"), ""),
        "rounds": rounds,
        "winnerId": winner and winner.id,
        "players": [
            {
                "id": player.id,
                "name": player.name,
                "outcome": player.outcome,
                "plays": player.plays,
                "lies": player.lies,
                "caught": player.caught,
                "calls": player.calls,
                "correctCalls": player.correct_calls,
                "pulls": player.pulls,
            }
            for player in placed
        ],
        "highlights": _highlights(stats),
        "tells": [tell for player in placed for tell in _tells(player)],
    }
