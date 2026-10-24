import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import compare  # noqa: E402


def row(r_final, stage, risk=60.0, cost=3.0):
    return {"risk_usd": risk, "r_multiple": r_final, "stage_reached": stage, "cost_usd": cost}


class Scaled(unittest.TestCase):
    def test_full_stop_is_the_same_as_all_in(self):
        self.assertAlmostEqual(compare.scaled_net(row(-1.0, 0)), -60.0 - 3.0)

    def test_stopped_at_breakeven_plus_after_tp1(self):
        # 1/3 at +1R, 2/3 at +0.2R  ->  (1 + 0.2 + 0.2) / 3 = 0.4667R
        self.assertAlmostEqual(compare.scaled_net(row(0.2, 1)), 60 * (1 + 0.2 + 0.2) / 3 - 3.0)

    def test_stopped_at_tp1_after_tp2(self):
        # legs: +1, +2, +1 -> 1.3333R
        self.assertAlmostEqual(compare.scaled_net(row(1.0, 2)), 60 * 4 / 3 - 3.0)

    def test_final_target_is_the_same_as_all_in_average(self):
        # +1, +2, +3 -> 2R (all-in would be +3R: scaling gives up upside, keeps it safe)
        self.assertAlmostEqual(compare.scaled_net(row(3.0, 2)), 60 * 2 - 3.0)

    def test_session_exit_after_tp1_only(self):
        # TP1 reached, then closed at session end at +0.5R: legs +1, +0.5, +0.5
        self.assertAlmostEqual(compare.scaled_net(row(0.5, 1)), 60 * (1 + 0.5 + 0.5) / 3 - 3.0)


if __name__ == "__main__":
    unittest.main()
