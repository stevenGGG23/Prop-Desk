import unittest

import numpy as np

from engine.funded import simulate_funded_monthly, withdrawal_discipline_curve


class FundedSimulationTests(unittest.TestCase):
    def test_distribution_requires_wins_and_losses(self):
        with self.assertRaisesRegex(ValueError, "at least one win and one loss"):
            simulate_funded_monthly(
                current_balance=10000,
                max_loss_limit=9000,
                drawdown_amount=1000,
                lock_threshold=9100,
                payout_cap=500,
                profit_split=0.9,
                min_balance_to_withdraw=9000,
                win_multiples=[1.0],
                loss_multiples=[],
                risk=100,
                n_runs=1,
            )

    def test_signal_frequency_must_be_a_fraction(self):
        with self.assertRaisesRegex(ValueError, "Signal frequency"):
            simulate_funded_monthly(
                current_balance=10000,
                max_loss_limit=9000,
                drawdown_amount=1000,
                lock_threshold=9100,
                payout_cap=500,
                profit_split=0.9,
                min_balance_to_withdraw=9000,
                win_multiples=[1.0],
                loss_multiples=[-1.0],
                risk=100,
                signal_frequency=1.5,
                n_runs=1,
            )

    def test_payout_curve_respects_selected_minimum_balance(self):
        curve = withdrawal_discipline_curve(
            current_balance=12000,
            max_loss_limit=9000,
            drawdown_amount=1000,
            lock_threshold=9100,
            payout_cap=500,
            profit_split=0.9,
            win_multiples=[1.5, 2.0],
            loss_multiples=[-1.0],
            risk=100,
            min_balance_to_withdraw=10500,
            cushion_values=[0, 100],
            n_runs=2,
        )

        self.assertEqual([row["min_balance"] for row in curve], [10500, 10600])
        self.assertTrue(all(0 <= row["breach_pct"] <= 100 for row in curve))

    def test_monthly_simulation_returns_percentiles_and_breach_rate(self):
        result = simulate_funded_monthly(
            current_balance=12000,
            max_loss_limit=9000,
            drawdown_amount=1000,
            lock_threshold=9100,
            payout_cap=500,
            profit_split=0.9,
            min_balance_to_withdraw=10500,
            win_multiples=[1.5, 2.0],
            loss_multiples=[-1.0],
            risk=100,
            signal_frequency=0.68,
            n_runs=20,
            rng=np.random.default_rng(4),
        )

        self.assertEqual(result["n_runs"], 20)
        self.assertLessEqual(result["p25_monthly"], result["p75_monthly"])
        self.assertTrue(0 <= result["breach_pct"] <= 100)


if __name__ == "__main__":
    unittest.main()