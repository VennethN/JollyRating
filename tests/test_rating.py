import math
import unittest
from datetime import datetime, timezone

from jollyrating.models import ContestStandings, Entry, compute_ranks
from jollyrating.rating import (
    ExternalParticipation,
    SEASON_RESET_EXPONENT,
    compute_ratings,
    external_performances,
    performance,
    season_rating,
    season_reset,
    weight,
    weighted_sum,
)


def utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def contest(cid, begin, results, title=None):
    """results: list of (user, score, penalty)."""
    entries = [Entry(u, u.title(), float(s), float(p)) for u, s, p in results]
    compute_ranks(entries)
    return ContestStandings(cid, title or f"Contest {cid}", begin, "icpc", entries)


class PerformanceTests(unittest.TestCase):
    def test_best_participant_gets_4000(self):
        for n in (1, 2, 10, 500):
            self.assertAlmostEqual(performance(n, 1, 3, 3), 4000.0)

    def test_formula_matches_hand_computation(self):
        # 10 participants, last place, one of three problems solved:
        # 4000 * (10 - 10 + 240 + 1) / (10 + 240) * 1/3
        self.assertAlmostEqual(performance(10, 10, 1, 3), 4000 * 241 / 250 / 3)
        # 30 participants, rank 4, 5 of 7 problems
        self.assertAlmostEqual(performance(30, 4, 5, 7), 4000 * (30 - 4 + 241) / 270 * 5 / 7)

    def test_zero_top_score_gives_zero(self):
        self.assertEqual(performance(5, 1, 0, 0), 0.0)

    def test_zero_score_gives_zero(self):
        self.assertEqual(performance(5, 5, 0, 4), 0.0)

    def test_range_checks(self):
        with self.assertRaises(ValueError):
            performance(5, 0, 1, 1)
        with self.assertRaises(ValueError):
            performance(5, 6, 1, 1)
        with self.assertRaises(ValueError):
            performance(0, 1, 1, 1)


class WeightTests(unittest.TestCase):
    def test_geometric_then_capped(self):
        self.assertAlmostEqual(weight(1), 0.1)
        self.assertAlmostEqual(weight(2), 0.095)
        self.assertAlmostEqual(weight(45), 0.1 * 0.95 ** 44)
        self.assertGreater(weight(45), 0.01)
        self.assertEqual(weight(46), 0.01)
        self.assertEqual(weight(1000), 0.01)

    def test_weighted_sum_sorts_descending(self):
        self.assertAlmostEqual(weighted_sum([1000, 4000]), 0.1 * 4000 + 0.095 * 1000)


class ExternalTests(unittest.TestCase):
    def test_triangular_indices(self):
        actual = [3000, 2500, 2000, 1500, 1000, 500]
        # i=1 -> P1, i=2 -> P3, i=3 -> P6, i=4 -> P10 (missing -> 0)
        self.assertEqual(external_performances(actual, 4), [3000, 2000, 500, 0.0])

    def test_unsorted_input(self):
        self.assertEqual(external_performances([500, 3000, 2000], 2), [3000, 500])

    def test_no_actual_performances(self):
        self.assertEqual(external_performances([], 2), [0.0, 0.0])


class SeasonResetTests(unittest.TestCase):
    def test_constant(self):
        self.assertAlmostEqual(SEASON_RESET_EXPONENT, math.log(400) / math.log(4000))
        self.assertAlmostEqual(SEASON_RESET_EXPONENT, 0.722381, places=6)

    def test_4000_becomes_400(self):
        self.assertAlmostEqual(season_reset(4000), 400.0)
        self.assertEqual(season_reset(0), 0.0)

    def test_concave(self):
        # Doubling the rating less than doubles the bonus.
        self.assertLess(season_reset(2000) * 2, season_reset(4000) * 2)
        self.assertLess(season_reset(4000), 2 * season_reset(2000))


class SeasonRatingTests(unittest.TestCase):
    def test_two_perfect_contests(self):
        self.assertAlmostEqual(season_rating(0, [4000, 4000]), 780.0)

    def test_reset_plus_external(self):
        # reset(4000)=400; P=[2000], one external copies P1 -> [2000, 2000]
        self.assertAlmostEqual(season_rating(4000, [2000], 1), 400 + 0.1 * 2000 + 0.095 * 2000)


class ComputeRatingsTests(unittest.TestCase):
    def setUp(self):
        self.c1 = contest("1", utc(2024, 9, 1), [("alice", 3, 100), ("bob", 3, 200), ("carol", 1, 50), ("dave", 0, 0)])
        self.c2 = contest("2", utc(2024, 10, 1), [("alice", 2, 100), ("bob", 4, 300)])
        self.c3 = contest("3", utc(2025, 9, 1), [("alice", 1, 10), ("carol", 1, 10)])

    def ratings(self, externals=()):
        return {
            r.user: r
            for r in compute_ratings(["S1", "S2"], [("S1", self.c1), ("S1", self.c2), ("S2", self.c3)], list(externals))
        }

    def test_ranks_and_ties(self):
        by_user = {e.user: e for e in self.c1.entries}
        self.assertEqual(by_user["alice"].rank, 1)
        self.assertEqual(by_user["bob"].rank, 2)
        self.assertEqual(by_user["carol"].rank, 3)
        self.assertEqual(by_user["dave"].rank, 4)
        tie = {e.user: e for e in self.c3.entries}
        self.assertEqual((tie["alice"].rank, tie["carol"].rank), (1, 1))

    def test_season_one_values(self):
        r = self.ratings()
        p1 = performance(4, 1, 3, 3)  # 4000
        p2 = performance(2, 2, 2, 4)  # alice second of two with half the score
        alice_s1 = r["alice"].seasons[0]
        self.assertAlmostEqual(alice_s1.rating, 0.1 * p1 + 0.095 * p2)
        self.assertEqual([p.perf for p in alice_s1.actual], [p1, p2])
        bob_s1 = r["bob"].seasons[0]
        self.assertAlmostEqual(bob_s1.rating, 0.1 * 4000 + 0.095 * performance(4, 2, 3, 3))
        self.assertEqual(r["dave"].seasons[0].rating, 0.0)

    def test_season_two_applies_reset(self):
        r = self.ratings()
        alice = r["alice"]
        s1, s2 = alice.seasons
        self.assertAlmostEqual(s2.base, season_reset(s1.rating))
        self.assertAlmostEqual(s2.rating, s2.base + 0.1 * 4000)  # tied first in c3
        self.assertAlmostEqual(alice.rating, s2.rating)
        # Bob did not play in S2: only the reset remains.
        bob = r["bob"]
        self.assertAlmostEqual(bob.rating, season_reset(bob.seasons[0].rating))

    def test_rating_never_decreases_within_a_season(self):
        r = self.ratings()
        for user in r.values():
            for season in ("S1", "S2"):
                points = [h.rating for h in user.history if h.season == season and h.label != "season reset"]
                self.assertEqual(points, sorted(points), user.user)

    def test_external_participation(self):
        ext = ExternalParticipation("carol", "S1", contest_id="2", note="ICPC regional")
        r = self.ratings([ext])
        carol_s1 = r["carol"].seasons[0]
        p = performance(4, 3, 1, 3)
        self.assertEqual(len(carol_s1.externals), 1)
        self.assertAlmostEqual(carol_s1.externals[0].perf, p)  # first external copies P1
        self.assertEqual(carol_s1.externals[0].source_index, 1)
        self.assertAlmostEqual(carol_s1.rating, 0.1 * p + 0.095 * p)
        self.assertEqual(carol_s1.externals[0].at, utc(2024, 10, 1))
        labels = [h.label for h in r["carol"].history]
        self.assertIn("external: ICPC regional", labels)

    def test_external_beyond_actual_count_is_zero(self):
        exts = [ExternalParticipation("dave", "S1", contest_id="2", note=str(i)) for i in range(3)]
        r = self.ratings(exts)
        self.assertEqual([e.perf for e in r["dave"].seasons[0].externals], [0.0, 0.0, 0.0])

    def test_unknown_season_rejected(self):
        with self.assertRaises(ValueError):
            compute_ratings(["S1"], [("S9", self.c1)])

    def test_sorted_by_rating(self):
        ordered = compute_ratings(["S1", "S2"], [("S1", self.c1), ("S1", self.c2), ("S2", self.c3)])
        self.assertEqual([r.rating for r in ordered], sorted((r.rating for r in ordered), reverse=True))


if __name__ == "__main__":
    unittest.main()
