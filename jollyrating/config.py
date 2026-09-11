"""Load and validate ``jollyrating.toml``."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    pass


@dataclass
class SeasonConfig:
    name: str
    start: datetime | None
    end: datetime | None = None


@dataclass
class ContestConfig:
    id: str
    vjudge_id: int | None = None
    title: str | None = None
    season: str | None = None
    mode: str | None = None
    file: Path | None = None
    penalty_minutes: float | None = None
    begin: datetime | None = None
    skip: bool = False
    note: str = ""

    @property
    def is_vjudge(self) -> bool:
        return self.vjudge_id is not None and self.file is None


@dataclass
class ExternalConfig:
    user: str
    contest: str | None = None
    season: str | None = None
    note: str = ""
    at: datetime | None = None


@dataclass
class Config:
    path: Path
    title: str = "JollyRating"
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))
    vjudge_base_url: str = "https://vjudge.net"
    vjudge_group: str | None = None
    cookie_env: str = "VJUDGE_COOKIE"
    cookie_file: Path | None = None
    user_agent: str | None = None
    request_delay: float = 1.0
    penalty_minutes: float = 20.0
    include_zero_submission_participants: bool = True
    default_mode: str = "icpc"
    data_dir: Path = Path("data")
    out_dir: Path = Path("out")
    seasons: list[SeasonConfig] = field(default_factory=list)
    contests: list[ContestConfig] = field(default_factory=list)
    externals: list[ExternalConfig] = field(default_factory=list)
    exclude_users: list[str] = field(default_factory=list)
    only_users: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    display_names: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ #

    @property
    def root(self) -> Path:
        return self.path.parent

    def resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else self.root / p

    @property
    def contests_dir(self) -> Path:
        return self.resolve(self.data_dir) / "contests"

    def contest(self, contest_id: str) -> ContestConfig | None:
        for c in self.contests:
            if c.id == str(contest_id):
                return c
        return None

    def season_names(self) -> list[str]:
        if not self.seasons:
            return ["All contests"]
        return [s.name for s in self.seasons]

    def season_for(self, begin: datetime, override: str | None = None) -> str:
        """Name of the season a contest starting at ``begin`` belongs to."""
        if override is not None:
            if override not in self.season_names():
                raise ConfigError(f"unknown season {override!r}; known seasons: {', '.join(self.season_names())}")
            return override
        if not self.seasons:
            return "All contests"
        if begin.tzinfo is None:
            begin = begin.replace(tzinfo=timezone.utc)
        chosen: SeasonConfig | None = None
        for s in self.seasons:
            if s.start is None or s.start <= begin:
                chosen = s
        if chosen is None:
            raise ConfigError(
                f"a contest starting {begin.isoformat()} is before the first season "
                f"({self.seasons[0].name} starts {self.seasons[0].start}); adjust the season dates or set season = ... on the contest"
            )
        if chosen.end is not None and begin >= chosen.end:
            raise ConfigError(
                f"a contest starting {begin.isoformat()} falls after the end of season {chosen.name} "
                f"({chosen.end.isoformat()}) and before the next season; adjust the season dates"
            )
        return chosen.name


# ---------------------------------------------------------------------- #
# Parsing helpers
# ---------------------------------------------------------------------- #


def _to_datetime(value: Any, tz: ZoneInfo, what: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConfigError(f"{what}: cannot parse date {value!r}") from exc
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value / (1000.0 if value > 1e11 else 1.0), tz=timezone.utc)
    else:
        raise ConfigError(f"{what}: expected a date, got {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _expect(table: dict[str, Any], key: str, types: tuple[type, ...], what: str, default: Any = None) -> Any:
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, types) or (bool in types and isinstance(value, bool) and bool not in types):
        names = "/".join(t.__name__ for t in types)
        raise ConfigError(f"{what}: {key} must be {names}, got {value!r}")
    return value


def _str_list(table: dict[str, Any], key: str, what: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{what}: {key} must be a list of strings")
    return list(value)


def _str_map(table: dict[str, Any], key: str, what: str) -> dict[str, str]:
    value = table.get(key, {})
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        raise ConfigError(f"{what}: {key} must be a table of strings")
    return {str(k): v for k, v in value.items()}


def parse_config(raw: dict[str, Any], path: Path) -> Config:
    cfg = Config(path=path)
    cfg.title = str(raw.get("title", cfg.title))
    tz_name = str(raw.get("timezone", "UTC"))
    try:
        cfg.timezone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"unknown timezone {tz_name!r}") from exc

    vj = raw.get("vjudge", {}) or {}
    if not isinstance(vj, dict):
        raise ConfigError("[vjudge] must be a table")
    cfg.vjudge_base_url = str(vj.get("base_url", cfg.vjudge_base_url)).rstrip("/")
    group = vj.get("group")
    cfg.vjudge_group = str(group).strip() or None if group is not None else None
    cfg.cookie_env = str(vj.get("cookie_env", cfg.cookie_env))
    if vj.get("cookie_file"):
        cfg.cookie_file = Path(str(vj["cookie_file"]))
    cfg.request_delay = float(_expect(vj, "request_delay", (int, float), "[vjudge]", cfg.request_delay))
    if vj.get("user_agent"):
        cfg.user_agent = str(vj["user_agent"]).strip() or None

    rt = raw.get("rating", {}) or {}
    if not isinstance(rt, dict):
        raise ConfigError("[rating] must be a table")
    cfg.penalty_minutes = float(_expect(rt, "penalty_minutes", (int, float), "[rating]", cfg.penalty_minutes))
    cfg.include_zero_submission_participants = bool(
        _expect(rt, "include_zero_submission_participants", (bool,), "[rating]", True)
    )
    cfg.default_mode = str(rt.get("default_mode", "icpc")).lower()
    if cfg.default_mode not in ("icpc", "ioi"):
        raise ConfigError("[rating]: default_mode must be 'icpc' or 'ioi'")

    out = raw.get("output", {}) or {}
    if not isinstance(out, dict):
        raise ConfigError("[output] must be a table")
    cfg.data_dir = Path(str(out.get("data_dir", "data")))
    cfg.out_dir = Path(str(out.get("out_dir", "out")))

    # Seasons -------------------------------------------------------------
    seasons_raw = raw.get("seasons", []) or []
    if not isinstance(seasons_raw, list):
        raise ConfigError("[[seasons]] must be an array of tables")
    for i, s in enumerate(seasons_raw, start=1):
        what = f"[[seasons]] #{i}"
        if not isinstance(s, dict) or not s.get("name"):
            raise ConfigError(f"{what}: every season needs a name")
        start = _to_datetime(s["start"], cfg.timezone, what) if "start" in s else None
        end = _to_datetime(s["end"], cfg.timezone, what) if "end" in s else None
        if start is None and i > 1:
            raise ConfigError(f"{what}: only the first season may omit start")
        cfg.seasons.append(SeasonConfig(str(s["name"]), start, end))
    names = [s.name for s in cfg.seasons]
    if len(set(names)) != len(names):
        raise ConfigError("season names must be unique")
    for a, b in zip(cfg.seasons, cfg.seasons[1:]):
        if a.start is not None and b.start is not None and b.start <= a.start:
            raise ConfigError(f"seasons must be listed in chronological order ({b.name} starts before {a.name})")
        if a.end is not None and b.start is not None and a.end > b.start:
            raise ConfigError(f"season {a.name} ends after season {b.name} starts")

    # Contests ------------------------------------------------------------
    contests_raw = raw.get("contests", []) or []
    if not isinstance(contests_raw, list):
        raise ConfigError("[[contests]] must be an array of tables")
    seen_ids: set[str] = set()
    for i, c in enumerate(contests_raw, start=1):
        what = f"[[contests]] #{i}"
        if isinstance(c, int) and not isinstance(c, bool):
            c = {"id": c}
        if not isinstance(c, dict) or "id" not in c:
            raise ConfigError(f"{what}: every contest needs an id")
        cid = c["id"]
        if isinstance(cid, bool) or not isinstance(cid, (int, str)):
            raise ConfigError(f"{what}: id must be an integer (vjudge contest id) or a string")
        contest = ContestConfig(id=str(cid).strip())
        if not contest.id:
            raise ConfigError(f"{what}: id must not be empty")
        if contest.id in seen_ids:
            raise ConfigError(f"{what}: duplicate contest id {contest.id}")
        seen_ids.add(contest.id)
        if isinstance(cid, int) or contest.id.isdigit():
            contest.vjudge_id = int(contest.id)
        if "vjudge_id" in c:
            contest.vjudge_id = int(c["vjudge_id"])
        contest.title = str(c["title"]) if c.get("title") else None
        contest.season = str(c["season"]) if c.get("season") else None
        if c.get("mode"):
            contest.mode = str(c["mode"]).lower()
            if contest.mode not in ("icpc", "ioi"):
                raise ConfigError(f"{what}: mode must be 'icpc' or 'ioi'")
        if c.get("file"):
            contest.file = Path(str(c["file"]))
        if "penalty_minutes" in c:
            contest.penalty_minutes = float(_expect(c, "penalty_minutes", (int, float), what))
        if "begin" in c:
            contest.begin = _to_datetime(c["begin"], cfg.timezone, what)
        contest.skip = bool(_expect(c, "skip", (bool,), what, False))
        contest.note = str(c.get("note", ""))
        if contest.file is None and contest.vjudge_id is None:
            raise ConfigError(f"{what}: contest {contest.id!r} is not a vjudge id; give it a file = ... with its standings")
        cfg.contests.append(contest)

    # External participations --------------------------------------------
    ext_raw = raw.get("external", raw.get("externals", [])) or []
    if not isinstance(ext_raw, list):
        raise ConfigError("[[external]] must be an array of tables")
    for i, e in enumerate(ext_raw, start=1):
        what = f"[[external]] #{i}"
        if not isinstance(e, dict) or not e.get("user"):
            raise ConfigError(f"{what}: every external participation needs a user")
        ext = ExternalConfig(user=str(e["user"]).strip())
        if "contest" in e:
            ext.contest = str(e["contest"]).strip()
            if ext.contest not in seen_ids:
                raise ConfigError(f"{what}: contest {ext.contest!r} is not listed under [[contests]]")
        if e.get("season"):
            ext.season = str(e["season"])
            if ext.season not in cfg.season_names():
                raise ConfigError(f"{what}: unknown season {ext.season!r}")
        if ext.contest is None and ext.season is None:
            raise ConfigError(f"{what}: give either contest = <id> (preferred) or season = <name>")
        ext.note = str(e.get("note", ""))
        if "at" in e:
            ext.at = _to_datetime(e["at"], cfg.timezone, what)
        cfg.externals.append(ext)

    # Users ---------------------------------------------------------------
    users = raw.get("users", {}) or {}
    if not isinstance(users, dict):
        raise ConfigError("[users] must be a table")
    cfg.exclude_users = _str_list(users, "exclude", "[users]")
    cfg.only_users = _str_list(users, "only", "[users]")
    cfg.aliases = _str_map(users, "aliases", "[users]")
    cfg.display_names = _str_map(users, "display_names", "[users]")
    return cfg


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file {path} not found (see jollyrating.example.toml)")
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    return parse_config(raw, path.resolve())
