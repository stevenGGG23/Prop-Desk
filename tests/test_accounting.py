import unittest

from engine.accounting import apply_daily_result


class ApplyDailyResultTests(unittest.TestCase):
    def test_eod_floor_uses_final_daily_balance(self):
        result = apply_daily_result(
            current_balance=100000,
            peak_balance=100000,
            max_loss_limit=95000,
            daily_pnl=1000,
            drawdown_amount=5000,
            lock_threshold=100100,
        )

        self.assertEqual(result["balance"], 101000)
        self.assertEqual(result["peak_balance"], 101000)
        self.assertEqual(result["max_loss_limit"], 96000)

    def test_losses_do_not_move_eod_peak_or_floor(self):
        result = apply_daily_result(
            current_balance=100000,
            peak_balance=102000,
            max_loss_limit=97000,
            daily_pnl=-500,
            drawdown_amount=5000,
            lock_threshold=100100,
        )

        self.assertEqual(result["balance"], 99500)
        self.assertEqual(result["peak_balance"], 102000)
        self.assertEqual(result["max_loss_limit"], 97000)

    def test_locked_floor_stays_fixed(self):
        result = apply_daily_result(
            current_balance=105000,
            peak_balance=105000,
            max_loss_limit=100100,
            daily_pnl=2000,
            drawdown_amount=5000,
            lock_threshold=100100,
        )

        self.assertEqual(result["max_loss_limit"], 100100)


if __name__ == "__main__":
    unittest.main()