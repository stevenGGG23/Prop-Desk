import unittest

from engine.montecarlo import simulate_account


class AlwaysWinRng:
    def random(self):
        return 0.0

    def choice(self, values):
        return values[0]


class TradingDaySimulationTests(unittest.TestCase):
    def test_time_to_pass_counts_weekdays_and_calendar_days_separately(self):
        result = simulate_account(
            current_balance=10000,
            max_loss_limit=5000,
            peak_balance=10000,
            drawdown_amount=5000,
            lock_threshold=10100,
            profit_target=100,
            risk=10,
            win_multiples=[1],
            loss_multiples=[-1],
            starting_balance=10000,
            signal_frequency=1,
            n_runs=1,
            rng=AlwaysWinRng(),
        )

        self.assertEqual(result["pass_pct"], 100.0)
        self.assertEqual(result["median_trading_days"], 10)
        self.assertGreaterEqual(result["median_calendar_days"], 10)


if __name__ == "__main__":
    unittest.main()