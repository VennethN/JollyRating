"""Command line interface: ``python -m jollyrating <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from jollyrating import __version__
from jollyrating.config import Config, ConfigError, ContestConfig, load_config
from jollyrating.models import ContestStandings
from jollyrating.rating import ExternalParticipation, UserRating, compute_ratings
from jollyrating.report import build_payload, write_all
from jollyrating.standings import StandingsError, UserFilter, from_manual_file, from_vjudge_rank
from jollyrating.vjudge import VJudgeAuthError, VJudgeClient, VJudgeError, load_cookie

log = logging.getLogger("jollyrating")


class CommandError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Helpers shared by commands
# --------------------------------------------------------------------------- #


def make_client(cfg: Config) -> VJudgeClient:
    cookie_file = cfg.resolve(cfg.cookie_file) if cfg.cookie_file else None
    cookie = load_cookie(cfg.cookie_env, cookie_file)
    if cookie:
        log.info("using vjudge cookie from %s", cfg.cookie_env if os.environ.get(cfg.cookie_env) else cookie_file)
    else:
        log.info("no vjudge cookie configured; only public contests can be fetched")
    return VJudgeClient(cfg.vjudge_base_url, cookie, delay=cfg.request_delay, user_agent=cfg.user_agent)


def cache_path(cfg: Config, contest: ContestConfig) -> Path:
    return cfg.contests_dir / f"{contest.id}.json"


def contest_url(cfg: Config, contest: ContestConfig) -> str | None:
    return f"{cfg.vjudge_base_url}/contest/{contest.vjudge_id}" if contest.vjudge_id else None


def user_filter(cfg: Config) -> UserFilter:
    return UserFilter(
        aliases=dict(cfg.aliases),
        exclude=set(cfg.exclude_users),
        only=set(cfg.only_users),
        display_names=dict(cfg.display_names),
    )


def load_standings(cfg: Config, contest: ContestConfig, users: UserFilter) -> ContestStandings:
    penalty = (contest.penalty_minutes if contest.penalty_minutes is not None else cfg.penalty_minutes) * 60
    if contest.file is not None:
        return from_manual_file(
            cfg.resolve(contest.file), contest.id, mode=contest.mode, title=contest.title,
            begin=contest.begin, users=users, url=contest_url(cfg, contest),
        )
    path = cache_path(cfg, contest)
    if not path.exists():
        raise CommandError(
            f"no data for contest {contest.id} ({path}); run `jollyrating fetch` or save "
            f"{cfg.vjudge_base_url}/contest/rank/single/{contest.vjudge_id} from your browser to that path"
        )
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return from_vjudge_rank(
        data, contest.id, penalty_seconds=penalty,
        include_zero_submission_participants=cfg.include_zero_submission_participants,
        users=users, title=contest.title, begin=contest.begin, url=contest_url(cfg, contest),
    )


def load_all(cfg: Config, *, ignore_missing: bool = False) -> tuple[list[tuple[str, ContestStandings]], list[ExternalParticipation]]:
    users = user_filter(cfg)
    contests: list[tuple[str, ContestStandings]] = []
    missing: list[str] = []
    for contest in cfg.contests:
        if contest.skip:
            log.info("contest %s skipped (skip = true)", contest.id)
            continue
        try:
            standings = load_standings(cfg, contest, users)
        except CommandError as exc:
            if ignore_missing:
                log.warning("%s", exc)
                missing.append(contest.id)
                continue
            raise
        if standings.n_participants == 0:
            log.warning("contest %s (%s) has no participants; skipping", contest.id, standings.title)
            continue
        season = cfg.season_for(standings.begin, contest.season)
        contests.append((season, standings))
        log.info("contest %s: %s – %d participants, season %s", contest.id, standings.title, standings.n_participants, season)
    loaded_ids = {c.id for _, c in contests}
    externals: list[ExternalParticipation] = []
    for e in cfg.externals:
        if e.contest is not None and e.contest not in loaded_ids:
            log.warning("external participation of %s refers to contest %s which has no data; ignored", e.user, e.contest)
            continue
        season = e.season
        if season is None:
            season = next(s for s, c in contests if c.id == e.contest)
        externals.append(ExternalParticipation(users.canonical(e.user), season, e.contest, e.note, e.at))
    return contests, externals


def compute(cfg: Config, *, ignore_missing: bool = False) -> tuple[dict, list[UserRating]]:
    contests, externals = load_all(cfg, ignore_missing=ignore_missing)
    if not contests:
        raise CommandError("no contest data to rate; add [[contests]] to the config and run `jollyrating fetch`")
    ratings = compute_ratings(cfg.season_names(), contests, externals, cfg.display_names)
    payload = build_payload(cfg.title, cfg.season_names(), contests, ratings)
    return payload, ratings


def print_leaderboard(ratings: list[UserRating], limit: int = 25) -> None:
    width = max((len(r.display) for r in ratings[:limit]), default=4)
    print(f"{'#':>3}  {'User':<{width}}  {'Rating':>8}  {'Contests':>8}  {'Best':>7}")
    for i, r in enumerate(ratings[:limit], start=1):
        print(f"{i:>3}  {r.display:<{width}}  {r.rating:>8.1f}  {r.contests:>8}  {r.best_perf:>7.1f}")
    if len(ratings) > limit:
        print(f"... and {len(ratings) - limit} more")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_validate(cfg: Config, args: argparse.Namespace) -> int:
    print(f"config: {cfg.path}")
    print(f"title: {cfg.title}   timezone: {cfg.timezone.key}")
    print(f"vjudge: {cfg.vjudge_base_url}  group: {cfg.vjudge_group or '-'}  cookie env: {cfg.cookie_env}")
    print(f"seasons ({len(cfg.seasons)}):")
    for s in cfg.seasons or []:
        print(f"  - {s.name}: from {s.start.date() if s.start else '(beginning)'}" + (f" to {s.end.date()}" if s.end else ""))
    if not cfg.seasons:
        print("  (none configured: everything counts as one season)")
    print(f"contests ({len(cfg.contests)}):")
    problems = 0
    for c in cfg.contests:
        if c.file is not None:
            path = cfg.resolve(c.file)
            status = "ok" if path.exists() else "MISSING FILE"
        else:
            status = "cached" if cache_path(cfg, c).exists() else "not fetched"
        if c.skip:
            status = "skipped"
        if status.startswith(("MISSING", "not")):
            problems += 1
        print(f"  - {c.id}: {c.title or ''} [{c.mode}] {status}")
    print(f"external participations: {len(cfg.externals)}")
    if problems:
        print(f"{problems} contest(s) have no data yet; run `jollyrating fetch`")
    return 0


def cmd_discover(cfg: Config, args: argparse.Namespace) -> int:
    group = args.group or cfg.vjudge_group
    if not group:
        raise CommandError("no group: pass --group <name> or set [vjudge] group = \"<name>\" in the config")
    client = make_client(cfg)
    found = client.discover_group_contests(group)
    known = {c.id for c in cfg.contests}
    if not found:
        print(
            f"No contests found on {cfg.vjudge_base_url}/group/{group}.\n"
            "If the group is private, set the VJUDGE_COOKIE environment variable (see README).\n"
            "Otherwise add the contest ids by hand: every contest has an id in its URL, "
            "https://vjudge.net/contest/<id>."
        )
        return 1
    new = [c for c in found if str(c.id) not in known]
    print(f"{len(found)} contest(s) on group {group} ({len(new)} not in the config):")
    for c in found:
        flag = " " if str(c.id) in known else "*"
        print(f" {flag} {c.id:>8}  {c.title or '(title unknown)'}   [{c.source}]")
    if args.write and new:
        with cfg.path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n# added by `jollyrating discover` on {datetime.now(timezone.utc).date()}\n")
            for c in new:
                fh.write(f"\n[[contests]]\nid = {c.id}\n")
                if c.title:
                    fh.write(f"# {c.title}\n")
        print(f"appended {len(new)} contest(s) to {cfg.path}; review them, then run `jollyrating fetch`")
    elif new:
        print("re-run with --write to append the new contests to the config")
    return 0


def cmd_fetch(cfg: Config, args: argparse.Namespace) -> int:
    client = make_client(cfg)
    cfg.contests_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(str(i) for i in args.ids) if args.ids else None
    fetched = skipped = failed = 0
    now = datetime.now(timezone.utc)
    for contest in cfg.contests:
        if contest.skip or contest.file is not None or contest.vjudge_id is None:
            continue
        if wanted is not None and contest.id not in wanted:
            continue
        path = cache_path(cfg, contest)
        if path.exists() and not args.force:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                end_ms = (cached.get("begin") or 0) + (cached.get("length") or 0)
                fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                ended = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc) if end_ms else None
            except (ValueError, OSError):
                ended, fetched_at = None, now
            if ended is not None and fetched_at >= ended:
                log.info("contest %s: cached (contest ended %s)", contest.id, ended.date())
                skipped += 1
                continue
            log.info("contest %s: cached copy was taken before the contest ended; refreshing", contest.id)
        try:
            data = client.contest_rank(contest.vjudge_id)
        except VJudgeAuthError as exc:
            log.error("contest %s: %s", contest.id, exc)
            failed += 1
            continue
        except VJudgeError as exc:
            log.error("contest %s: %s", contest.id, exc)
            failed += 1
            continue
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        begin = datetime.fromtimestamp((data.get("begin") or 0) / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(((data.get("begin") or 0) + (data.get("length") or 0)) / 1000, tz=timezone.utc)
        running = "" if end <= now else "  (still running – fetch again after it ends)"
        log.info("contest %s: %s – %d participants, %d submissions, %s%s",
                 contest.id, data.get("title"), len(data.get("participants") or {}), len(data.get("submissions") or []), begin.date(), running)
        fetched += 1
    print(f"fetched {fetched}, cached {skipped}, failed {failed}")
    if failed:
        print("some contests could not be fetched; for private contests set VJUDGE_COOKIE (see README)")
    return 1 if failed and not fetched and not skipped else 0


def cmd_compute(cfg: Config, args: argparse.Namespace) -> int:
    payload, ratings = compute(cfg, ignore_missing=args.ignore_missing)
    out_dir = cfg.resolve(Path(args.out) if args.out else cfg.out_dir)
    written = write_all(payload, out_dir)
    print_leaderboard(ratings, args.top)
    print()
    print("written: " + ", ".join(str(p) for p in written))
    return 0


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    cmd_fetch(cfg, args)
    return cmd_compute(cfg, args)


def cmd_explain(cfg: Config, args: argparse.Namespace) -> int:
    _payload, ratings = compute(cfg, ignore_missing=True)
    users = user_filter(cfg)
    name = users.canonical(args.user)
    match = next((r for r in ratings if r.user == name or r.user.lower() == name.lower()), None)
    if match is None:
        raise CommandError(f"user {args.user!r} has no rated contests (known: {', '.join(r.user for r in ratings[:20])}...)")
    rank = ratings.index(match) + 1
    print(f"{match.display} ({match.user}): rating {match.rating:.2f}, rank {rank}/{len(ratings)}")
    for s in match.seasons:
        if args.season and s.season != args.season:
            continue
        if not s.actual and not s.externals and s.base == 0:
            continue
        print()
        print(f"== {s.season} ==")
        print(f"carried over: seasonReset({s.previous_rating:.2f}) = {s.base:.2f}")
        for p in sorted(s.actual, key=lambda p: p.begin):
            print(f"  {p.begin.date()}  {p.title[:40]:<40}  rank {p.rank:>3}/{p.n_participants:<3}  score {p.score:g}/{p.max_score:g}  perf {p.perf:8.2f}")
        for e in s.externals:
            print(f"  external #{e.index} ({e.note or 'no note'}): P{e.source_index} = {e.perf:.2f}")
        print("  weighted (sorted, best first):")
        for w in s.weighted:
            print(f"    {w.position:>3}. {w.perf:8.2f} x {w.weight:.4f} = {w.contribution:7.2f}   {w.kind} {w.ref}")
        print(f"  gained {s.gained:.2f} -> rating at end of season {s.rating:.2f}")
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jollyrating", description="Season-based rating for a vjudge group.")
    p.add_argument("-c", "--config", default="jollyrating.toml", help="config file (default: jollyrating.toml)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--version", action="version", version=f"jollyrating {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="check the config and report which contests still need data").set_defaults(fn=cmd_validate)

    d = sub.add_parser("discover", help="list the contests of the vjudge group")
    d.add_argument("--group", help="group name (last part of https://vjudge.net/group/<name>)")
    d.add_argument("--write", action="store_true", help="append unknown contests to the config")
    d.set_defaults(fn=cmd_discover)

    f = sub.add_parser("fetch", help="download standings of the configured vjudge contests")
    f.add_argument("--force", action="store_true", help="re-download contests that are already cached")
    f.add_argument("--ids", nargs="*", help="only these contest ids")
    f.set_defaults(fn=cmd_fetch)

    def add_compute_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--out", help="output directory (default: [output] out_dir)")
        sp.add_argument("--top", type=int, default=25, help="rows to print (default 25)")
        sp.add_argument("--ignore-missing", action="store_true", help="rate with whatever data exists")

    c = sub.add_parser("compute", help="compute ratings from cached data and write the reports")
    add_compute_args(c)
    c.set_defaults(fn=cmd_compute)

    r = sub.add_parser("run", help="fetch then compute")
    r.add_argument("--force", action="store_true", help="re-download contests that are already cached")
    r.add_argument("--ids", nargs="*", help=argparse.SUPPRESS)
    add_compute_args(r)
    r.set_defaults(fn=cmd_run)

    e = sub.add_parser("explain", help="show how one user's rating was computed")
    e.add_argument("user")
    e.add_argument("--season")
    e.set_defaults(fn=cmd_explain)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s" if not args.verbose else "%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        cfg = load_config(args.config)
        return args.fn(cfg, args)
    except (ConfigError, StandingsError, CommandError, VJudgeError) as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
