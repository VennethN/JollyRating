"""Generate a small, deterministic demo group so `jollyrating compute` can be
tried without touching vjudge.

Run from the repository root::

    python examples/demo/make_fixtures.py
    python -m jollyrating -c examples/demo/jollyrating.toml compute

The contest files follow the exact shape of vjudge's
``/contest/rank/single/<id>`` response, so they double as format documentation.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
USERS = [
    # uid, username, nickname, skill (0..1)
    (1001, "amara", "Amara", 0.92),
    (1002, "bayu", "Bayu P.", 0.80),
    (1003, "chen", "", 0.74),
    (1004, "dewi", "Dewi", 0.66),
    (1005, "eko", "Eko S.", 0.58),
    (1006, "farah", "Farah", 0.52),
    (1007, "gilang", "", 0.45),
    (1008, "hana", "Hana", 0.38),
    (1009, "irfan", "Irfan", 0.30),
    (1010, "joko", "Joko", 0.22),
]
CONTESTS = [
    # id, title, start (UTC), problems, absent users
    (700001, "JollyBee Weekly #1", datetime(2024, 9, 7, 6, 0, tzinfo=timezone.utc), 6, []),
    (700002, "JollyBee Weekly #2", datetime(2024, 9, 21, 6, 0, tzinfo=timezone.utc), 7, ["hana"]),
    (700003, "JollyBee Weekly #3", datetime(2024, 10, 12, 6, 0, tzinfo=timezone.utc), 6, ["amara"]),
    (700004, "JollyBee Mock ICPC", datetime(2024, 11, 2, 2, 0, tzinfo=timezone.utc), 10, ["joko"]),
    (700005, "JollyBee Weekly #4", datetime(2024, 12, 14, 6, 0, tzinfo=timezone.utc), 6, ["chen", "irfan"]),
    (700006, "JollyBee Weekly #5", datetime(2025, 9, 13, 6, 0, tzinfo=timezone.utc), 6, ["gilang"]),
    (700007, "JollyBee Weekly #6", datetime(2025, 10, 4, 6, 0, tzinfo=timezone.utc), 7, ["bayu"]),
]
LENGTH = timedelta(hours=5)


def make_contest(rng: random.Random, cid: int, title: str, begin: datetime, problems: int, absent: list[str]) -> dict:
    participants = {}
    submissions = []
    for uid, username, nick, skill in USERS:
        if username in absent:
            continue
        participants[str(uid)] = [username, nick, ""]
        for p in range(problems):
            difficulty = (p + 1) / (problems + 1)
            if rng.random() > skill - difficulty + 0.55:
                continue  # never solved
            wrong = rng.choice([0, 0, 0, 1, 1, 2, 3])
            t = int(LENGTH.total_seconds() * min(0.98, difficulty * 0.8 + rng.random() * 0.4))
            for k in range(wrong):
                submissions.append([uid, p, 0, max(60, t - (wrong - k) * rng.randint(300, 1500))])
            submissions.append([uid, p, 1, t])
    submissions.sort(key=lambda s: s[3])
    return {
        "id": cid,
        "title": title,
        "begin": int(begin.timestamp() * 1000),
        "length": int(LENGTH.total_seconds() * 1000),
        "isReplay": False,
        "participants": participants,
        "submissions": submissions,
    }


def main() -> None:
    rng = random.Random(20240907)
    out = HERE / "data" / "contests"
    out.mkdir(parents=True, exist_ok=True)
    for cid, title, begin, problems, absent in CONTESTS:
        (out / f"{cid}.json").write_text(json.dumps(make_contest(rng, cid, title, begin, problems, absent), indent=1))
    manual = HERE / "data" / "manual"
    manual.mkdir(parents=True, exist_ok=True)
    (manual / "ioi-practice-1.json").write_text(
        json.dumps(
            {
                "title": "IOI-style practice #1",
                "begin": "2025-11-08T09:00:00+08:00",
                "mode": "ioi",
                "standings": [
                    {"user": "amara", "score": 287.5},
                    {"user": "bayu", "score": 300},
                    {"user": "dewi", "score": 212},
                    {"user": "eko", "score": 212},
                    {"user": "farah", "score": 150},
                    {"user": "hana", "score": 40},
                    {"user": "joko", "score": 0},
                ],
            },
            indent=1,
        )
    )
    print(f"wrote {len(CONTESTS)} contests to {out} and 1 manual contest to {manual}")


if __name__ == "__main__":
    main()
