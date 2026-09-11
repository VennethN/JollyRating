"""Turn raw contest data into :class:`ContestStandings`.

Two sources are supported:

* vjudge's ``/contest/rank/single/<id>`` JSON (ICPC-style scoring), and
* a manual JSON/CSV file for contests vjudge cannot express (IOI-style
  scoring, or contests held elsewhere).
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jollyrating.models import ContestStandings, Entry, compute_ranks

log = logging.getLogger(__name__)

DEFAULT_PENALTY_SECONDS = 20 * 60


class StandingsError(ValueError):
    pass


def problem_label(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA, ..."""
    if index < 0:
        return str(index)
    label = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


@dataclass
class UserFilter:
    """Identity and inclusion rules applied to every contest."""

    aliases: dict[str, str] = field(default_factory=dict)
    exclude: set[str] = field(default_factory=set)
    only: set[str] = field(default_factory=set)
    display_names: dict[str, str] = field(default_factory=dict)

    def canonical(self, username: str) -> str:
        seen = {username}
        while username in self.aliases:
            username = self.aliases[username]
            if username in seen:
                raise StandingsError(f"alias cycle involving {username!r}")
            seen.add(username)
        return username

    def allowed(self, user: str) -> bool:
        if user in self.exclude:
            return False
        if self.only and user not in self.only:
            return False
        return True


def _finalize(entries: Iterable[Entry], users: UserFilter | None) -> list[Entry]:
    """Apply aliases/filters, merge duplicates, then rank."""
    users = users or UserFilter()
    merged: dict[str, Entry] = {}
    for e in entries:
        canonical = users.canonical(e.user)
        if not users.allowed(canonical):
            continue
        e.user = canonical
        if canonical in users.display_names:
            e.display = users.display_names[canonical]
        if canonical in merged:
            other = merged[canonical]
            log.warning("user %s appears twice in a contest (aliases?); keeping the better result", canonical)
            if (e.score, -e.penalty) > (other.score, -other.penalty):
                merged[canonical] = e
        else:
            merged[canonical] = e
    result = list(merged.values())
    compute_ranks(result)
    return result


# --------------------------------------------------------------------------- #
# vjudge
# --------------------------------------------------------------------------- #


@dataclass
class _ProblemState:
    ac_time: float | None = None
    wrong: int = 0
    attempts: int = 0
    best: float = 0.0


def _participant_names(pid: str, info: Any) -> tuple[str, str]:
    """vjudge lists participants as ``{"name": username, "nickname": ...}`` (older
    payloads: ``[username, nickname, avatar]``)."""
    if isinstance(info, dict):
        username = str(info.get("name") or info.get("username") or f"uid:{pid}")
        nickname = str(info.get("nickname") or info.get("nick") or "")
    elif isinstance(info, (list, tuple)):
        username = str(info[0]) if info and info[0] else f"uid:{pid}"
        nickname = str(info[1]) if len(info) > 1 and info[1] else ""
    else:
        username = str(info) if info else f"uid:{pid}"
        nickname = ""
    return username, nickname or username


def _score_column(sub: Any, index: int) -> float | None:
    try:
        value = sub[index]
    except (IndexError, TypeError):
        return None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def from_vjudge_rank(
    data: dict[str, Any],
    contest_id: str,
    *,
    mode: str = "icpc",
    penalty_seconds: float = DEFAULT_PENALTY_SECONDS,
    include_zero_submission_participants: bool = True,
    users: UserFilter | None = None,
    title: str | None = None,
    begin: datetime | None = None,
    url: str | None = None,
) -> ContestStandings:
    """Build standings from ``/contest/rank/single/<id>`` JSON.

    The payload looks like::

        {"id": 123, "title": "...", "begin": <ms epoch>, "length": <ms>, "isReplay": false,
         "participants": {"<uid>": {"type": "user", "name": "username", "nickname": "Nick", ...}, ...},
         "submissions": [[<uid>, <problem index>, <1 if accepted else 0>, <seconds since start>,
                          <score>, <full score>], ...]}

    The two score columns are optional (older payloads and some judges omit
    them) and participants may also be ``[username, nickname, avatar]`` lists.

    ``mode="icpc"``: the score is the number of solved problems and the
    penalty is, over solved problems, the time of the first accepted
    submission plus ``penalty_seconds`` for every rejected submission before
    it.  ``mode="ioi"``: the score is the sum over problems of the best score
    of any submission (partial scores count, the penalty is 0); an accepted
    submission without a score column counts as the problem's full score.
    Submissions outside the contest window are ignored in both modes.
    """
    mode = mode.lower()
    if mode not in ("icpc", "ioi"):
        raise StandingsError(f"contest {contest_id}: mode must be 'icpc' or 'ioi', got {mode!r}")
    if not isinstance(data, dict) or "participants" not in data or "submissions" not in data:
        raise StandingsError(f"contest {contest_id}: payload has no participants/submissions (not a rank JSON?)")

    participants = data.get("participants") or {}
    submissions = data.get("submissions") or []
    length_ms = data.get("length")
    length_s = float(length_ms) / 1000.0 if isinstance(length_ms, (int, float)) and length_ms > 0 else None
    if begin is None:
        begin_ms = data.get("begin")
        if not isinstance(begin_ms, (int, float)):
            raise StandingsError(f"contest {contest_id}: no 'begin' timestamp; set begin = ... in the config")
        begin = datetime.fromtimestamp(begin_ms / 1000.0, tz=timezone.utc)
    elif begin.tzinfo is None:
        begin = begin.replace(tzinfo=timezone.utc)
    title = title or str(data.get("title") or f"Contest {contest_id}")

    names: dict[str, tuple[str, str]] = {}
    for pid, info in participants.items():
        names[str(pid)] = _participant_names(str(pid), info)

    # Full score per problem, from any submission that carries the score columns
    # (also post-contest ones: they still document the problem's full score).
    problem_full: dict[int, float] = {}
    best_seen: dict[int, float] = {}
    for sub in submissions:
        try:
            prob = int(sub[1])
        except (TypeError, ValueError, IndexError):
            continue
        full = _score_column(sub, 5)
        if full is not None and full > 0:
            problem_full[prob] = max(problem_full.get(prob, 0.0), full)
        got = _score_column(sub, 4)
        if got is not None:
            best_seen[prob] = max(best_seen.get(prob, 0.0), got)

    state: dict[str, dict[int, _ProblemState]] = {pid: {} for pid in names}
    valid: list[tuple[float, int, str, int, bool, float | None]] = []
    skipped = 0
    for order, sub in enumerate(submissions):
        try:
            pid = str(sub[0])
            prob = int(sub[1])
            accepted = int(sub[2]) == 1
            t = float(sub[3])
        except (TypeError, ValueError, IndexError):
            skipped += 1
            continue
        if t < 0 or (length_s is not None and t > length_s):
            continue  # practice submissions after the contest (or clock glitches)
        valid.append((t, order, pid, prob, accepted, _score_column(sub, 4)))
    if skipped:
        log.warning("contest %s: skipped %d malformed submissions", contest_id, skipped)

    guessed_full: set[int] = set()
    valid.sort()
    for t, _order, pid, prob, accepted, score in valid:
        if pid not in names:
            log.warning("contest %s: submission by unknown participant %s; adding as uid:%s", contest_id, pid, pid)
            names[pid] = (f"uid:{pid}", f"uid:{pid}")
            state[pid] = {}
        ps = state[pid].setdefault(prob, _ProblemState())
        if mode == "icpc":
            if ps.ac_time is not None:
                continue
            ps.attempts += 1
            if accepted:
                ps.ac_time = t
            else:
                ps.wrong += 1
            continue
        ps.attempts += 1
        if score is None:
            if accepted:
                if prob in problem_full:
                    score = problem_full[prob]
                else:
                    score = best_seen.get(prob) or 100.0
                    guessed_full.add(prob)
            else:
                score = 0.0
        ps.best = max(ps.best, score)
        if accepted and ps.ac_time is None:
            ps.ac_time = t
    if guessed_full:
        log.warning(
            "contest %s: accepted submissions without a score for problem(s) %s and no full score known; "
            "counted as %s",
            contest_id, ", ".join(problem_label(p) for p in sorted(guessed_full)),
            "the best score seen (or 100)",
        )

    problems = 0
    entries: list[Entry] = []
    for pid, (username, nickname) in names.items():
        solved: list[str] = []
        penalty = 0.0
        attempts = 0
        total = 0.0
        for prob, ps in sorted(state[pid].items()):
            problems = max(problems, prob + 1)
            attempts += ps.attempts
            if mode == "icpc":
                if ps.ac_time is not None:
                    solved.append(problem_label(prob))
                    penalty += ps.ac_time + penalty_seconds * ps.wrong
            else:
                total += ps.best
                if ps.ac_time is not None or (prob in problem_full and ps.best >= problem_full[prob]):
                    solved.append(problem_label(prob))
        if attempts == 0 and not include_zero_submission_participants:
            continue
        if mode == "icpc":
            entries.append(Entry(username, nickname, float(len(solved)), penalty, solved, attempts))
        else:
            entries.append(Entry(username, nickname, total, 0.0, solved, attempts))

    return ContestStandings(
        id=str(contest_id),
        title=title,
        begin=begin,
        mode=mode,
        entries=_finalize(entries, users),
        length_seconds=length_s,
        problems=problems or None,
        source="vjudge",
        url=url,
    )


# --------------------------------------------------------------------------- #
# Manual standings (IOI-style or off-platform contests)
# --------------------------------------------------------------------------- #


def _parse_datetime(value: Any, what: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value / (1000.0 if value > 1e11 else 1.0), tz=timezone.utc)
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise StandingsError(f"{what}: cannot parse date {value!r} (use ISO 8601)") from exc
    else:
        raise StandingsError(f"{what}: missing or invalid date")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def from_manual_file(
    path: str | Path,
    contest_id: str,
    *,
    mode: str = "ioi",
    title: str | None = None,
    begin: datetime | None = None,
    users: UserFilter | None = None,
    url: str | None = None,
) -> ContestStandings:
    """Load standings from a JSON or CSV file.

    JSON::

        {"title": "Week 4 (IOI style)", "begin": "2025-01-10T09:00:00+08:00", "mode": "ioi",
         "standings": [{"user": "alice", "score": 250, "penalty": 0, "display": "Alice"}, ...]}

    CSV: columns ``user, score[, penalty][, display]`` with a header row.
    ``title``/``begin`` for CSV files come from the config.
    """
    path = Path(path)
    if not path.exists():
        raise StandingsError(f"contest {contest_id}: standings file {path} does not exist")
    rows: list[dict[str, Any]]
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            rows = [dict(r) for r in csv.DictReader(fh)]
        meta: dict[str, Any] = {}
    else:
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, list):
            rows, meta = payload, {}
        elif isinstance(payload, dict):
            rows = payload.get("standings") or payload.get("entries") or []
            meta = payload
        else:
            raise StandingsError(f"contest {contest_id}: {path} must contain an object or a list")

    mode = str(meta.get("mode") or mode).lower()
    if mode not in ("icpc", "ioi"):
        raise StandingsError(f"contest {contest_id}: mode must be 'icpc' or 'ioi', got {mode!r}")
    title = title or meta.get("title") or f"Contest {contest_id}"
    if begin is None:
        if "begin" not in meta:
            raise StandingsError(f"contest {contest_id}: no begin date (set it in {path.name} or in the config)")
        begin = _parse_datetime(meta["begin"], f"contest {contest_id}")
    elif begin.tzinfo is None:
        begin = begin.replace(tzinfo=timezone.utc)

    entries: list[Entry] = []
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise StandingsError(f"contest {contest_id}: row {i} is not an object")
        user = str(row.get("user") or row.get("username") or "").strip()
        if not user:
            raise StandingsError(f"contest {contest_id}: row {i} has no user")
        try:
            score = float(row.get("score", 0) or 0)
            penalty = float(row.get("penalty", 0) or 0) if mode == "icpc" else 0.0
        except (TypeError, ValueError) as exc:
            raise StandingsError(f"contest {contest_id}: row {i} has a non-numeric score/penalty") from exc
        if score < 0:
            raise StandingsError(f"contest {contest_id}: row {i} has a negative score")
        display = str(row.get("display") or row.get("name") or user)
        entries.append(Entry(user, display, score, penalty))

    return ContestStandings(
        id=str(contest_id),
        title=str(title),
        begin=begin,
        mode=mode,
        entries=_finalize(entries, users),
        source="manual",
        url=url or (str(meta.get("url")) if meta.get("url") else None),
    )
