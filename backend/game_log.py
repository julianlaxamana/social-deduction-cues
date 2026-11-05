"""Records what happens in each game, for analyzing it afterwards.

Each game gets one JSON Lines file. Every line has `t`, the seconds since the
game started, and `event`. Speech is written once it has been transcribed, which
can be after later events, so sort by `t` to read a game in order. Audio is never
written, and speech is only written for players who opted in.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

LOG_DIR = Path(os.environ.get("GAME_LOG_DIR", Path(__file__).parent / "logs"))


class GameLog:
    def __init__(self, room_id: str) -> None:
        started = datetime.now(timezone.utc)
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        self.path = LOG_DIR / f"{stamp}-{room_id}-{uuid4().hex[:8]}.jsonl"
        self.started = time.monotonic()
        # Also kept in memory, so a recap works even if the file can't be written.
        self.events: list[dict] = []
        self.write("log-start", room=room_id, startedAt=started.isoformat())

    def now(self) -> float:
        return time.monotonic() - self.started

    def write(self, event: str, *, t: float | None = None, **fields) -> None:
        line = {"t": round(self.now() if t is None else t, 3), "event": event, **fields}
        self.events.append(line)
        # A full disk or bad permissions shouldn't stop people playing.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(line) + "\n")
        except OSError:
            logger.exception("Could not write to %s", self.path)

    def write_speech(self, player_id: str, start: float, end: float, text: str) -> None:
        self.write(
            "speech",
            t=start,
            playerId=player_id,
            end=round(end, 3),
            text=text,
        )
