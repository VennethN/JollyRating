"""Plain data containers shared by the standings parser and the rating engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Entry:
    """One participant's result in one contest."""

    user: str
    """Canonical handle (the vjudge username, after alias resolution)."""
    display: str
    """Name to show (vjudge nickname when set, otherwise the username)."""
    score: float
    """ICPC-style: number of solved problems. IOI-style: raw score."""
    penalty: float = 0.0
    """ICPC-style: total penalty in seconds. Always 0 for IOI-style."""
    solved: list[str] = field(default_factory=list)
    """Problem labels solved (ICPC-style only)."""
    attempts: int = 0
    """Number of counted submissions (ICPC-style only)."""
    rank: int = 0
    """1 + number of participants with a larger score, or an equal score and a smaller penalty."""


@dataclass
class ContestStandings:
    """Final standings of one contest, in the form the rating engine consumes."""

    id: str
    title: str
    begin: datetime
    """Timezone-aware start time."""
    mode: str
    """``"icpc"`` or ``"ioi"``."""
    entries: list[Entry]
    length_seconds: float | None = None
    problems: int | None = None
    source: str = ""
    """Where the standings came from (``"vjudge"`` or ``"manual"``)."""
    url: str | None = None

    @property
    def n_participants(self) -> int:
        return len(self.entries)

    @property
    def max_score(self) -> float:
        return max((e.score for e in self.entries), default=0.0)

    def entry(self, user: str) -> Entry | None:
        for e in self.entries:
            if e.user == user:
                return e
        return None


def compute_ranks(entries: list[Entry]) -> None:
    """Fill ``Entry.rank`` in place.

    rank(u) = 1 + |{ v : score(v) > score(u) or (score(v) == score(u) and penalty(v) < penalty(u)) }|

    Participants with an identical (score, penalty) share the same rank.
    """
    for e in entries:
        better = 0
        for other in entries:
            if other is e:
                continue
            if other.score > e.score or (other.score == e.score and other.penalty < e.penalty):
                better += 1
        e.rank = 1 + better
    entries.sort(key=lambda e: (e.rank, e.user.lower()))
