import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from jollyrating.cli import main
from jollyrating.config import ConfigError, parse_config

BASE_MS = 1_725_000_000_000


def rank_json(cid, begin_ms, participants, submissions, title=None):
    return {
        "id": cid, "title": title or f"Contest {cid}", "begin": begin_ms, "length": 3_600_000,
        "participants": participants, "submissions": submissions,
    }


def load(text, path="/tmp/x/jollyrating.toml"):
    return parse_config(tomllib.loads(text), Path(path))


class ConfigTests(unittest.TestCase):
    def test_minimal(self):
        cfg = load('[[contests]]\nid = 123\n')
        self.assertEqual(cfg.contests[0].vjudge_id, 123)
        self.assertEqual(cfg.season_names(), ["All contests"])
        self.assertEqual(cfg.season_for(datetime(2020, 1, 1, tzinfo=timezone.utc)), "All contests")

    def test_seasons_by_date(self):
        cfg = load(
            'timezone = "Asia/Manila"\n'
            '[[seasons]]\nname = "S1"\nstart = 2024-08-01\n'
            '[[seasons]]\nname = "S2"\nstart = 2025-08-01\n'
            '[[contests]]\nid = 1\n[[contests]]\nid = 2\nseason = "S1"\n'
        )
        self.assertEqual(cfg.season_for(datetime(2024, 8, 1, tzinfo=timezone.utc)), "S1")
        self.assertEqual(cfg.season_for(datetime(2025, 8, 1, 12, tzinfo=timezone.utc)), "S2")
        # 2025-07-31T23:00 UTC is already Aug 1 in Manila (UTC+8).
        self.assertEqual(cfg.season_for(datetime(2025, 7, 31, 23, tzinfo=timezone.utc)), "S2")
        self.assertEqual(cfg.season_for(datetime(2025, 7, 31, 10, tzinfo=timezone.utc)), "S1")
        self.assertEqual(cfg.season_for(datetime(2026, 1, 1, tzinfo=timezone.utc), "S1"), "S1")
        with self.assertRaises(ConfigError):
            cfg.season_for(datetime(2024, 1, 1, tzinfo=timezone.utc))
        with self.assertRaises(ConfigError):
            cfg.season_for(datetime(2026, 1, 1, tzinfo=timezone.utc), "S3")

    def test_validation_errors(self):
        bad = [
            '[[contests]]\nid = "week-1"\n',  # non-numeric id without file
            '[[contests]]\nid = 1\n[[contests]]\nid = 1\n',  # duplicate
            '[[seasons]]\nname = "S1"\n[[seasons]]\nname = "S2"\n',  # second season without start
            '[[seasons]]\nname = "S2"\nstart = 2025-01-01\n[[seasons]]\nname = "S1"\nstart = 2024-01-01\n',
            '[[contests]]\nid = 1\n[[external]]\nuser = "a"\ncontest = 2\n',  # unknown contest
            '[[external]]\nuser = "a"\n',  # neither contest nor season
            'timezone = "Mars/Olympus"\n',
        ]
        for text in bad:
            with self.assertRaises(ConfigError, msg=text):
                load(text)

    def test_modes(self):
        cfg = load('[rating]\ndefault_mode = "ioi"\n[[contests]]\nid = 1\n[[contests]]\nid = 2\nmode = "icpc"\n')
        self.assertEqual(cfg.default_mode, "ioi")
        self.assertIsNone(cfg.contests[0].mode)
        self.assertEqual(cfg.contests[1].mode, "icpc")
        with self.assertRaises(ConfigError):
            load('[rating]\ndefault_mode = "oi"\n')
        with self.assertRaises(ConfigError):
            load('[[contests]]\nid = 1\nmode = "oi"\n')

    def test_vjudge_section(self):
        cfg = load('[vjudge]\nbase_url = "http://x/"\nuser_agent = " UA/1 "\ncookie_file = ".cookie"\n')
        self.assertEqual(cfg.vjudge_base_url, "http://x")
        self.assertEqual(cfg.user_agent, "UA/1")
        self.assertEqual(str(cfg.cookie_file), ".cookie")
        self.assertIsNone(load("").user_agent)

    def test_users_and_externals(self):
        cfg = load(
            '[[contests]]\nid = 5\n'
            '[[external]]\nuser = "alice"\ncontest = 5\nnote = "ICPC"\n'
            '[users]\nexclude = ["x"]\naliases = { old = "new" }\ndisplay_names = { new = "New Name" }\n'
        )
        self.assertEqual(cfg.externals[0].contest, "5")
        self.assertEqual(cfg.aliases, {"old": "new"})
        self.assertEqual(cfg.exclude_users, ["x"])


class CliEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "data" / "contests").mkdir(parents=True)
        (root / "data" / "manual").mkdir(parents=True)
        parts = {"1": ["alice", "Alice"], "2": ["bob", "Bob"], "3": ["carol", ""]}
        c1 = rank_json(101, BASE_MS, parts, [[1, 0, 1, 100], [2, 0, 1, 200], [2, 1, 1, 300], [3, 0, 0, 50]])
        c2 = rank_json(102, BASE_MS + 7 * 86400_000, parts, [[1, 0, 1, 100], [1, 1, 1, 200], [2, 0, 1, 90]])
        (root / "data" / "contests" / "101.json").write_text(json.dumps(c1))
        (root / "data" / "contests" / "102.json").write_text(json.dumps(c2))
        (root / "data" / "manual" / "ioi.json").write_text(json.dumps({
            "title": "IOI style", "begin": "2025-09-01T00:00:00Z",
            "standings": [{"user": "alice", "score": 100}, {"user": "carol", "score": 300}],
        }))
        (root / "jollyrating.toml").write_text(
            'title = "Test rating"\n'
            '[[seasons]]\nname = "2024"\nstart = 2024-01-01\n'
            '[[seasons]]\nname = "2025"\nstart = 2025-08-01\n'
            '[[contests]]\nid = 101\n'
            '[[contests]]\nid = 102\n'
            '[[contests]]\nid = "ioi-1"\nmode = "ioi"\nfile = "data/manual/ioi.json"\n'
            '[[contests]]\nid = 999\nskip = true\n'
            '[[external]]\nuser = "carol"\ncontest = 102\nnote = "regional"\n'
        )
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["-c", str(self.root / "jollyrating.toml"), *args])
        return code, out.getvalue()

    def test_validate_compute_explain(self):
        code, out = self.run_cli("validate")
        self.assertEqual(code, 0)
        self.assertIn("999", out)

        code, out = self.run_cli("compute")
        self.assertEqual(code, 0, out)
        self.assertIn("Alice", out)
        for name in ("ratings.json", "ratings.csv", "ratings.md", "index.html"):
            self.assertTrue((self.root / "out" / name).exists(), name)
        payload = json.loads((self.root / "out" / "ratings.json").read_text())
        self.assertEqual(payload["seasons"], ["2024", "2025"])
        self.assertEqual([c["id"] for c in payload["contests"]], ["101", "102", "ioi-1"])
        users = {u["user"]: u for u in payload["users"]}
        self.assertEqual(set(users), {"alice", "bob", "carol"})
        carol_2024 = users["carol"]["seasons"][0]
        self.assertEqual(len(carol_2024["externals"]), 1)
        self.assertEqual(carol_2024["externals"][0]["note"], "regional")
        # Carol is last in contest 101 (0 solved) -> perf 0; the external copies P1 = 0.
        self.assertEqual(carol_2024["rating"], 0)
        # Carol wins the IOI contest in 2025: 4000 * 0.1 = 400.
        self.assertAlmostEqual(users["carol"]["seasons"][1]["rating"], 400.0)
        # Bob wins contest 101 and 102 (2 solves vs 1 solve in 101, faster in 102? no: alice has 2 in 102).
        self.assertEqual(payload["users"][0]["rank"], 1)
        html = (self.root / "out" / "index.html").read_text()
        self.assertIn("Test rating", html)
        data_block = html.split('id="data" type="application/json">')[1].split("</script>")[0]
        self.assertNotIn("</", data_block)
        self.assertEqual(json.loads(data_block.replace("<\\/", "</"))["title"], "Test rating")

        code, out = self.run_cli("explain", "carol")
        self.assertEqual(code, 0)
        self.assertIn("external #1", out)

    def test_missing_data_is_an_error_unless_ignored(self):
        (self.root / "data" / "contests" / "102.json").unlink()
        code, _ = self.run_cli("compute")
        self.assertEqual(code, 2)
        code, _ = self.run_cli("compute", "--ignore-missing")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
