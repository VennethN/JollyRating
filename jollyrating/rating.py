"""The JollyBee rating rules.

Everything in this module is a pure function of the standings, so it can be
checked by hand.  Summary of the rules (see README.md for the full text):

* ``perf(u, c) = 4000 * (|U(c)| - rank(u, c) + 240 + 1) / (|U(c)| + 240) * score(u, c) / max_v score(v, c)``
* The i-th external participation (i >= 1) in a season is worth ``P[i(i+1)/2]``
  where ``P`` is the user's actual performances of that season sorted in
  non-increasing order (1-indexed), or 0 when ``i(i+1)/2 > len(P)``.
* Weights: ``W_i = max(0.01, 0.1 * 0.95 ** (i - 1))`` applied to the season's
  performances (actual + external) sorted in non-increasing order.
* ``rating_s(u) = seasonReset(rating_{s-1}(u)) + sum_i W_i * P'_i`` with
  ``rating_0 = 0`` and ``seasonReset(x) = x ** log_4000(400)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Sequence

from jollyrating.models import ContestStandings

PERF_MAX = 4000.0
RANK_OFFSET = 240
WEIGHT_BASE = 0.1
WEIGHT_RATIO = 0.95
WEIGHT_FLOOR = 0.01
SEASON_RESET_EXPONENT = math.log(400) / math.log(4000)  # ~0.722381
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Elementary formulas
# --------------------------------------------------------------------------- #


def performance(n_participants: int, rank: int, score: float, max_score: float) -> float:
    """Performance of a participant in one contest, in ``[0, 4000]``.

    ``max_score <= 0`` (nobody scored) yields 0 for everyone: rewarding a
    contest in which nothing was solved would let a group of participants farm
    rating by showing up and doing nothing.
    """
    if n_participants <= 0:
        raise ValueError("a contest needs at least one participant")
    if not 1 <= rank <= n_participants:
        raise ValueError(f"rank {rank} is outside 1..{n_participants}")
    if score < 0:
        raise ValueError("score must be non-negative")
    if max_score <= 0:
        return 0.0
    rank_factor = (n_participants - rank + RANK_OFFSET + 1) / (n_participants + RANK_OFFSET)
    score_factor = min(score / max_score, 1.0)
    return PERF_MAX * rank_factor * score_factor


def weight(position: int) -> float:
    """Weight of the ``position``-th greatest performance (1-indexed)."""
    if position < 1:
        raise ValueError("position is 1-indexed")
    return max(WEIGHT_FLOOR, WEIGHT_BASE * WEIGHT_RATIO ** (position - 1))


def external_source_index(i: int) -> int:
    """1-indexed position in ``P`` that the i-th external participation copies."""
    return i * (i + 1) // 2


def external_performances(actual: Sequence[float], count: int) -> list[float]:
    """Performances credited for ``count`` external participations."""
    ordered = sorted(actual, reverse=True)
    result: list[float] = []
    for i in range(1, count + 1):
        j = external_source_index(i)
        result.append(ordered[j - 1] if j <= len(ordered) else 0.0)
    return result


def season_reset(rating: float) -> float:
    """Additive bonus carried into the next season: ``rating ** log_4000(400)``."""
    if rating <= 0:
        return 0.0
    return rating ** SEASON_RESET_EXPONENT


def weighted_sum(perfs: Iterable[float]) -> float:
    ordered = sorted(perfs, reverse=True)
    return sum(weight(i) * p for i, p in enumerate(ordered, start=1))


def season_rating(previous_rating: float, actual: Sequence[float], external_count: int = 0) -> float:
    """Rating at the end of a season, directly from the definition."""
    perfs = list(actual) + external_performances(actual, external_count)
    return season_reset(previous_rating) + weighted_sum(perfs)


# --------------------------------------------------------------------------- #
# Full computation with per-user breakdowns
# --------------------------------------------------------------------------- #


@dataclass
class ExternalParticipation:
    """A contest the user missed because of an external training/competition."""

    user: str
    season: str
    contest_id: str | None = None
    """Contest of this season that was missed (used for dating the history)."""
    note: str = ""
    at: datetime | None = None
    """When the external participation happened; filled from the contest if omitted."""


@dataclass
class ContestPerformance:
    contest_id: str
    title: str
    begin: datetime
    season: str
    rank: int
    score: float
    penalty: float
    n_participants: int
    max_score: float
    perf: float
    url: str | None = None


@dataclass
class ExternalPerformance:
    index: int
    """i, the ordinal of this external participation within the season."""
    source_index: int
    """i(i+1)/2, the 1-indexed position in P it copies."""
    perf: float
    contest_id: str | None
    note: str
    at: datetime | None


@dataclass
class WeightedItem:
    position: int
    perf: float
    weight: float
    contribution: float
    kind: str
    """``"actual"`` or ``"external"``."""
    ref: str
    """Contest id for actual performances, the note for external ones."""


@dataclass
class SeasonRating:
    season: str
    previous_rating: float
    base: float
    """``seasonReset(previous_rating)``."""
    actual: list[ContestPerformance] = field(default_factory=list)
    externals: list[ExternalPerformance] = field(default_factory=list)
    weighted: list[WeightedItem] = field(default_factory=list)
    rating: float = 0.0
    """Rating at the end of the season."""

    @property
    def gained(self) -> float:
        return self.rating - self.base


@dataclass
class HistoryPoint:
    at: datetime
    season: str
    rating: float
    label: str
    contest_id: str | None = None
    delta: float = 0.0


@dataclass
class UserRating:
    user: str
    display: str
    rating: float
    seasons: list[SeasonRating]
    history: list[HistoryPoint]

    @property
    def contests(self) -> int:
        return sum(len(s.actual) for s in self.seasons)

    @property
    def best_perf(self) -> float:
        return max((p.perf for s in self.seasons for p in s.actual), default=0.0)

    @property
    def last_season(self) -> SeasonRating | None:
        for s in reversed(self.seasons):
            if s.actual or s.externals:
                return s
        return None


def _weighted_items(actual: list[ContestPerformance], externals: list[ExternalPerformance]) -> list[WeightedItem]:
    items: list[tuple[float, str, str]] = [(p.perf, "actual", p.contest_id) for p in actual]
    items += [(e.perf, "external", e.note or f"external #{e.index}") for e in externals]
    # Stable ordering: greatest performance first, actual before external on ties.
    items.sort(key=lambda t: (-t[0], 0 if t[1] == "actual" else 1))
    weighted: list[WeightedItem] = []
    for position, (perf, kind, ref) in enumerate(items, start=1):
        w = weight(position)
        weighted.append(WeightedItem(position, perf, w, w * perf, kind, ref))
    return weighted


def _externals_with_perfs(actual: list[ContestPerformance], externals: list[ExternalParticipation]) -> list[ExternalPerformance]:
    perfs = external_performances([p.perf for p in actual], len(externals))
    result = []
    for i, (spec, perf) in enumerate(zip(externals, perfs), start=1):
        result.append(ExternalPerformance(i, external_source_index(i), perf, spec.contest_id, spec.note, spec.at))
    return result


def compute_ratings(
    season_names: Sequence[str],
    contests: Sequence[tuple[str, ContestStandings]],
    externals: Sequence[ExternalParticipation] = (),
    display_names: dict[str, str] | None = None,
) -> list[UserRating]:
    """Compute every user's rating, season by season, with full breakdowns.

    ``contests`` pairs each contest with the name of the season it belongs to.
    Seasons are processed in the order of ``season_names``; contests inside a
    season are ordered by start time.  The returned list is sorted by rating,
    highest first.
    """
    display_names = dict(display_names or {})
    by_season: dict[str, list[ContestStandings]] = {name: [] for name in season_names}
    for season, standings in contests:
        if season not in by_season:
            raise ValueError(f"contest {standings.id} is assigned to unknown season {season!r}")
        by_season[season].append(standings)
    for lst in by_season.values():
        lst.sort(key=lambda c: (c.begin, c.id))

    contest_by_id = {c.id: c for lst in by_season.values() for c in lst}
    for ext in externals:
        if ext.season not in by_season:
            raise ValueError(f"external participation of {ext.user} refers to unknown season {ext.season!r}")
        if ext.contest_id is not None:
            if ext.contest_id not in contest_by_id:
                raise ValueError(
                    f"external participation of {ext.user} refers to unknown contest {ext.contest_id!r}"
                )
            if ext.at is None:
                ext.at = contest_by_id[ext.contest_id].begin

    users: dict[str, str] = {}
    for lst in by_season.values():
        for c in lst:
            for e in c.entries:
                users.setdefault(e.user, e.user)
                if e.display and e.display != e.user:
                    users[e.user] = e.display  # latest nickname wins
    for ext in externals:
        users.setdefault(ext.user, ext.user)
    for user, name in display_names.items():
        if user in users:
            users[user] = name

    results: list[UserRating] = []
    for user in sorted(users):
        previous = 0.0
        seasons: list[SeasonRating] = []
        history: list[HistoryPoint] = []
        for season in season_names:
            base = season_reset(previous)
            season_contests = by_season[season]
            # Dated externals are applied chronologically; undated ones at the end of the season.
            pending = [e for e in externals if e.user == user and e.season == season]
            pending.sort(key=lambda e: (e.at is None, e.at or _EPOCH))
            applied: list[ExternalParticipation] = []
            actual: list[ContestPerformance] = []
            current = base
            if season_contests and previous > 0:
                history.append(
                    HistoryPoint(season_contests[0].begin, season, base, "season reset", None, base - previous)
                )

            for c in season_contests:
                entry = c.entry(user)
                newly_applied = [e for e in pending if e.at is not None and e.at <= c.begin]
                if entry is None and not newly_applied:
                    continue
                for e in newly_applied:
                    pending.remove(e)
                    applied.append(e)
                if entry is not None:
                    perf = performance(c.n_participants, entry.rank, entry.score, c.max_score)
                    actual.append(
                        ContestPerformance(
                            c.id, c.title, c.begin, season, entry.rank, entry.score, entry.penalty,
                            c.n_participants, c.max_score, perf, c.url,
                        )
                    )
                ext_perfs = _externals_with_perfs(actual, applied)
                new_rating = base + sum(i.contribution for i in _weighted_items(actual, ext_perfs))
                if entry is not None:
                    label = c.title
                else:
                    label = "external: " + (newly_applied[-1].note or "external participation")
                history.append(HistoryPoint(c.begin, season, new_rating, label, c.id, new_rating - current))
                current = new_rating

            applied.extend(pending)
            ext_perfs = _externals_with_perfs(actual, applied)
            weighted = _weighted_items(actual, ext_perfs)
            rating = base + sum(i.contribution for i in weighted)
            if abs(rating - current) > 1e-9:
                at = season_contests[-1].begin if season_contests else datetime.now(timezone.utc)
                history.append(HistoryPoint(at, season, rating, "external participation", None, rating - current))
            seasons.append(SeasonRating(season, previous, base, actual, ext_perfs, weighted, rating))
            previous = rating

        results.append(UserRating(user, users[user], previous, seasons, history))

    results.sort(key=lambda r: (-r.rating, r.user.lower()))
    return results
