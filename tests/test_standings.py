import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jollyrating.standings import StandingsError, UserFilter, from_manual_file, from_vjudge_rank, problem_label

BEGIN_MS = 1_725_000_000_000  # 2024-08-30T06:40:00Z
LENGTH_MS = 5 * 3600 * 1000


def payload(submissions, participants=None):
    return {
        "id": 111,
        "title": "Weekly #1",
        "begin": BEGIN_MS,
        "length": LENGTH_MS,
        "participants": participants or {
            "1": ["alice", "Alice A.", ""],
            "2": ["bob", "", ""],
            "3": ["carol", "Carol", ""],
            "4": ["dave", "Dave", ""],
        },
        "submissions": submissions,
    }


class VJudgeParserTests(unittest.TestCase):
    def test_icpc_scoring(self):
        subs = [
            [1, 0, 0, 600],  # alice A wrong
            [1, 0, 1, 1200],  # alice A ac -> 1200 + 1200 penalty
            [1, 0, 0, 1300],  # after AC: ignored
            [1, 1, 1, 3000],  # alice B ac
            [2, 0, 1, 900],  # bob A ac
            [2, 1, 0, 4000],  # bob B wrong, never solved -> no penalty
            [3, 0, 1, 900],  # carol A ac (same as bob -> tie)
            [3, 2, 1, LENGTH_MS / 1000 + 5],  # after the contest: ignored
            ["4", "0", "0", "100"],  # strings are accepted
        ]
        st = from_vjudge_rank(payload(subs), "111")
        self.assertEqual(st.title, "Weekly #1")
        self.assertEqual(st.begin, datetime.fromtimestamp(BEGIN_MS / 1000, tz=timezone.utc))
        self.assertEqual(st.mode, "icpc")
        self.assertEqual(st.n_participants, 4)
        self.assertEqual(st.max_score, 2)
        by = {e.user: e for e in st.entries}
        self.assertEqual(by["alice"].score, 2)
        self.assertEqual(by["alice"].penalty, 1200 + 1200 + 3000)
        self.assertEqual(by["alice"].solved, ["A", "B"])
        self.assertEqual(by["alice"].attempts, 3)
        self.assertEqual(by["alice"].display, "Alice A.")
        self.assertEqual(by["bob"].display, "bob")
        self.assertEqual((by["bob"].score, by["bob"].penalty), (1, 900))
        self.assertEqual((by["carol"].score, by["carol"].penalty), (1, 900))
        self.assertEqual((by["dave"].score, by["dave"].attempts), (0, 1))
        self.assertEqual([e.rank for e in st.entries], [1, 2, 2, 4])
        self.assertEqual([e.user for e in st.entries], ["alice", "bob", "carol", "dave"])

    def test_zero_submission_participants_can_be_dropped(self):
        st = from_vjudge_rank(payload([[1, 0, 1, 10]]), "111", include_zero_submission_participants=False)
        self.assertEqual([e.user for e in st.entries], ["alice"])
        st = from_vjudge_rank(payload([[1, 0, 1, 10]]), "111")
        self.assertEqual(st.n_participants, 4)

    def test_unknown_participant_is_added(self):
        st = from_vjudge_rank(payload([[99, 0, 1, 10]]), "111")
        self.assertIn("uid:99", [e.user for e in st.entries])

    def test_custom_penalty_and_filters(self):
        subs = [[1, 0, 0, 100], [1, 0, 1, 200], [2, 0, 1, 50]]
        users = UserFilter(aliases={"bob": "robert"}, exclude={"dave"}, display_names={"robert": "Rob"})
        st = from_vjudge_rank(payload(subs), "111", penalty_seconds=60, users=users)
        by = {e.user: e for e in st.entries}
        self.assertEqual(by["alice"].penalty, 260)
        self.assertNotIn("dave", by)
        self.assertNotIn("bob", by)
        self.assertEqual(by["robert"].display, "Rob")
        self.assertEqual(by["robert"].rank, 1)

    def test_only_filter(self):
        st = from_vjudge_rank(payload([]), "111", users=UserFilter(only={"alice", "bob"}))
        self.assertEqual(sorted(e.user for e in st.entries), ["alice", "bob"])

    def test_malformed_payload(self):
        with self.assertRaises(StandingsError):
            from_vjudge_rank({"error": "no such contest"}, "1")

    def test_missing_begin_needs_override(self):
        data = payload([])
        del data["begin"]
        with self.assertRaises(StandingsError):
            from_vjudge_rank(data, "111")
        st = from_vjudge_rank(data, "111", begin=datetime(2024, 1, 1))
        self.assertEqual(st.begin.tzinfo, timezone.utc)

    def test_problem_labels(self):
        self.assertEqual([problem_label(i) for i in (0, 1, 25, 26, 27)], ["A", "B", "Z", "AA", "AB"])


class ManualFileTests(unittest.TestCase):
    def test_json_ioi(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ioi.json"
            p.write_text(json.dumps({
                "title": "IOI practice",
                "begin": "2025-01-10T09:00:00+08:00",
                "mode": "ioi",
                "standings": [
                    {"user": "alice", "score": 250.5},
                    {"user": "bob", "score": 300, "display": "Bob"},
                    {"user": "carol", "score": 250.5, "penalty": 999},
                ],
            }))
            st = from_manual_file(p, "ioi-1")
            self.assertEqual(st.mode, "ioi")
            self.assertEqual(st.title, "IOI practice")
            self.assertEqual(st.begin.isoformat(), "2025-01-10T09:00:00+08:00")
            by = {e.user: e for e in st.entries}
            self.assertEqual(by["bob"].rank, 1)
            self.assertEqual(by["alice"].rank, 2)
            self.assertEqual(by["carol"].rank, 2)  # penalty ignored in IOI mode
            self.assertEqual(by["carol"].penalty, 0)
            self.assertEqual(st.max_score, 300)

    def test_csv_needs_begin_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.csv"
            p.write_text("user,score,display\nalice,10,Alice\nbob,20,\n")
            with self.assertRaises(StandingsError):
                from_manual_file(p, "csv-1")
            st = from_manual_file(p, "csv-1", begin=datetime(2025, 2, 1, tzinfo=timezone.utc), title="CSV one")
            self.assertEqual([e.user for e in st.entries], ["bob", "alice"])
            self.assertEqual(st.entries[1].display, "Alice")

    def test_missing_file(self):
        with self.assertRaises(StandingsError):
            from_manual_file("/nonexistent/x.json", "x")


if __name__ == "__main__":
    unittest.main()
