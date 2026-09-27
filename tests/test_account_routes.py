import io
import re
import unittest
from datetime import date
from unittest.mock import patch

import requests

from app import _bootstrap_user, create_app, db
from models import Account, ActivityLog, Bot, DailyResult, Distribution, DrawdownType, Firm, Phase, Trade, User, WebhookReceiver


class AccountRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            self.user = User(username="tester", display_name="Test User", password_hash="x")
            self.user.set_password("password123")
            self.firm = Firm(name="Test Firm", slug="test-firm")
            db.session.add_all([self.user, self.firm])
            db.session.flush()
            self.account = Account(
                user_id=self.user.id,
                firm_id=self.firm.id,
                nickname="Test 50k",
                phase=Phase.EVAL,
                starting_balance=50000,
                current_balance=50000,
                peak_balance=50000,
                max_loss_limit=48000,
                drawdown_amount=2000,
                drawdown_type=DrawdownType.EOD_TRAILING,
                lock_threshold=50100,
                profit_target=3000,
                current_risk=200,
                cost_paid=0,
            )
            self.bot = Bot(user_id=self.user.id, name="Test Bot", version="1")
            db.session.add_all([self.account, self.bot])
            db.session.commit()
            self.account_id = self.account.id
            self.bot_id = self.bot.id
            self.user_id = self.user.id

        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def test_settings_rejects_email_already_in_use(self):
        self.app.config["WTF_CSRF_ENABLED"] = True
        with self.app.app_context():
            other_user = User(
                username="other",
                display_name="Other User",
                password_hash="x",
                email="taken@example.com",
            )
            db.session.add(other_user)
            db.session.commit()

        page = self.client.get("/settings")
        self.assertIn(b'brand/logoPD-dark.png', page.data)
        self.assertIn(b'brand/logoPD-light.png', page.data)
        csrf_token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            page.get_data(as_text=True),
        ).group(1)
        form_data = {
            "csrf_token": csrf_token,
            "action": "profile",
            "display_name": "Test User",
            "username": "tester",
            "email": "taken@example.com",
            "timezone": "America/New_York",
        }
        rejected = self.client.post(
            "/settings",
            data={key: value for key, value in form_data.items() if key != "csrf_token"},
        )
        self.assertEqual(rejected.status_code, 400)

        response = self.client.post("/settings", data=form_data)

        self.assertEqual(response.status_code, 200)
        self.assertIn("already in use", response.get_data(as_text=True))

    def test_bootstrap_user_is_created_once_and_must_change_password(self):
        self.app.config.update({
            "BOOTSTRAP_USER_ENABLED": True,
            "BOOTSTRAP_USER_USERNAME": "martin",
            "BOOTSTRAP_USER_DISPLAY_NAME": "Martin",
            "BOOTSTRAP_USER_EMAIL": "martingobran1@gmail.com",
            "BOOTSTRAP_USER_PASSWORD": "temporary-password",
        })

        _bootstrap_user(self.app)
        _bootstrap_user(self.app)

        with self.app.app_context():
            user = User.query.filter_by(username="martin").one()
            self.assertEqual(user.email, "martingobran1@gmail.com")
            self.assertTrue(user.must_change_password)
            self.assertTrue(user.check_password("temporary-password"))

    def test_login_form_includes_csrf_token(self):
        with self.client.session_transaction() as session:
            session.pop("_user_id", None)
            session.pop("_fresh", None)
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="csrf_token"', response.data)
        self.assertIn(b'brand/logoPD-dark.png', response.data)
        for variant in ("dark", "light"):
            logo = self.client.get(f"/static/brand/logoPD-{variant}.png")
            self.assertEqual(logo.status_code, 200)
            self.assertEqual(logo.mimetype, "image/png")

    def test_inbound_pnl_requires_event_id_and_rejects_cumulative_totals(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.inbound_token = "test-inbound-token"
            db.session.commit()

        url = "/api/inbound/test-inbound-token"
        missing_id = self.client.post(url, json={
            "account": "Test 50k",
            "pnl": 100,
            "pnl_mode": "realized",
        })
        missing_mode = self.client.post(url, json={
            "account": "Test 50k",
            "event_id": "event-without-mode",
            "pnl": 100,
        })
        cumulative = self.client.post(url, json={
            "account": "Test 50k",
            "event_id": "strategy-run-1",
            "pnl": 100,
            "pnl_mode": "cumulative",
        })
        invalid_balance = self.client.post(url, json={
            "account": "Test 50k",
            "balance": float("nan"),
        })

        self.assertEqual(missing_id.status_code, 422)
        self.assertEqual(missing_mode.status_code, 422)
        self.assertEqual(cumulative.status_code, 422)
        self.assertEqual(invalid_balance.status_code, 422)
        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            self.assertEqual(float(account.current_balance), 50000)
            self.assertEqual(Trade.query.filter_by(account_id=self.account_id).count(), 0)

    def test_inbound_webhook_deduplicates_pnl_and_closes_eod_day(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.inbound_token = "test-inbound-token"
            db.session.commit()

        url = "/api/inbound/test-inbound-token"
        payload = {
            "account": "Test 50k",
            "event_id": "trade-2026-09-27-1",
            "action": "sell",
            "ticker": "MNQ",
            "price": 20000,
            "quantity": 1,
            "pnl_mode": "realized",
            "pnl": 500,
        }
        signal_payload = {
            key: value for key, value in payload.items()
            if key not in ("pnl", "pnl_mode")
        }
        signal = self.client.post(url, json=signal_payload)
        duplicate_signal = self.client.post(url, json=signal_payload)
        first = self.client.post(url, json=payload)
        duplicate = self.client.post(url, json=payload)

        self.assertEqual(signal.status_code, 200)
        self.assertTrue(duplicate_signal.json["duplicate"])
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json["duplicate"])
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json["duplicate"])
        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            self.assertEqual(float(account.current_balance), 50500)
            self.assertEqual(Trade.query.filter_by(account_id=self.account_id).count(), 1)

        closed = self.client.post("/accounts/{}/close-day".format(self.account_id))
        self.assertEqual(closed.status_code, 302)
        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            result = DailyResult.query.filter_by(account_id=self.account_id).one()
            self.assertEqual(float(result.pnl), 500)
            self.assertEqual(float(account.peak_balance), 50500)
            self.assertEqual(float(account.max_loss_limit), 48500)

    def test_account_webhook_deduplicates_fill_pnl(self):
        with self.app.app_context():
            receiver = WebhookReceiver(
                account_id=self.account_id,
                token="test-account-webhook-token",
                name="Test fills",
                source="traderspost",
            )
            db.session.add(receiver)
            db.session.commit()

        payload = {
            "event_id": "fill-123", "ticker": "MNQ",
            "pnl_mode": "realized", "pnl": 125,
        }
        first = self.client.post("/api/webhook/test-account-webhook-token", json=payload)
        duplicate = self.client.post("/api/webhook/test-account-webhook-token", json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json["duplicate"])
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json["duplicate"])
        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            self.assertEqual(float(account.current_balance), 50125)
            self.assertEqual(Trade.query.filter_by(account_id=self.account_id).count(), 1)

    def test_intraday_webhook_updates_floor_and_breach_phase(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.inbound_token = "test-inbound-token"
            account = db.session.get(Account, self.account_id)
            account.drawdown_type = DrawdownType.INTRADAY_TRAILING
            db.session.commit()

        url = "/api/inbound/test-inbound-token"
        win = self.client.post(url, json={
            "account": "Test 50k", "event_id": "intraday-win",
            "pnl_mode": "realized", "pnl": 500,
        })
        loss = self.client.post(url, json={
            "account": "Test 50k", "event_id": "intraday-loss",
            "pnl_mode": "realized", "pnl": -2500,
        })

        self.assertEqual(win.status_code, 200)
        self.assertEqual(loss.status_code, 200)
        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            self.assertEqual(float(account.current_balance), 48000)
            self.assertEqual(float(account.peak_balance), 50500)
            self.assertEqual(float(account.max_loss_limit), 48500)
            self.assertEqual(account.phase, Phase.BREACHED)

    def test_inbound_webhook_reports_forwarding_failure(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.inbound_token = "test-inbound-token"
            account = db.session.get(Account, self.account_id)
            account.forward_url = "https://example.test/webhook"
            db.session.commit()

        payload = {
            "account": "Test 50k",
            "event_id": "signal-123",
            "action": "buy",
            "ticker": "MNQ",
        }
        with patch("requests.post", side_effect=requests.Timeout("downstream timeout")) as sender:
            response = self.client.post("/api/inbound/test-inbound-token", json=payload)
            duplicate = self.client.post("/api/inbound/test-inbound-token", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["forwarded"])
        self.assertEqual(response.json["forward_error"], "Timeout")
        self.assertTrue(duplicate.json["duplicate"])
        self.assertEqual(sender.call_count, 1)
        with self.app.app_context():
            self.assertEqual(
                ActivityLog.query.filter(
                    ActivityLog.account_id == self.account_id,
                    ActivityLog.message.like("Webhook forwarding failed%"),
                ).count(),
                1,
            )

    def test_inbound_webhook_reports_forwarding_http_error(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.inbound_token = "test-inbound-token"
            account = db.session.get(Account, self.account_id)
            account.forward_url = "https://example.test/webhook"
            db.session.commit()

        upstream = requests.Response()
        upstream.status_code = 503
        upstream.url = "https://example.test/webhook"
        with patch("requests.post", return_value=upstream):
            response = self.client.post("/api/inbound/test-inbound-token", json={
                "account": "Test 50k", "event_id": "signal-http-1",
                "action": "buy", "ticker": "MNQ",
            })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["forwarded"])
        self.assertEqual(response.json["forward_error"], "HTTPError")
        with self.app.app_context():
            log_entry = ActivityLog.query.filter(
                ActivityLog.account_id == self.account_id,
                ActivityLog.message.like("Webhook forwarding failed%"),
            ).one()
            self.assertEqual(log_entry.payload["status_code"], 503)

    def test_inbound_fill_is_not_forwarded_to_execution_endpoint(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.inbound_token = "test-inbound-token"
            account = db.session.get(Account, self.account_id)
            account.forward_url = "https://example.test/webhook"
            db.session.commit()

        with patch("requests.post") as sender:
            response = self.client.post("/api/inbound/test-inbound-token", json={
                "account": "Test 50k", "event_id": "fill-no-loop",
                "pnl_mode": "realized", "pnl": 100, "ticker": "MNQ",
            })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["forwarded"])
        self.assertEqual(response.json["forward_reason"], "fill_or_balance_update")
        sender.assert_not_called()

    def test_dashboard_webhook_sample_is_signal_only(self):
        response = self.client.get("/")
        page = response.get_data(as_text=True)
        sample = re.search(
            r'<div id="webhook-panel".*?<pre[^>]*>(.*?)</pre>', page, re.DOTALL
        ).group(1)

        self.assertEqual(response.status_code, 200)
        self.assertIn('"event_id"', sample)
        self.assertNotIn('"pnl"', sample)
        self.assertNotIn("strategy.netprofit", sample)

    def test_daily_result_can_be_corrected_without_double_counting(self):
        url = "/accounts/{}/daily-result".format(self.account_id)
        first = self.client.post(url, data={
            "trade_date": date.today().isoformat(), "pnl": "500", "bot_id": str(self.bot_id),
        })
        self.assertEqual(first.status_code, 302)

        corrected = self.client.post(url, data={
            "trade_date": date.today().isoformat(), "pnl": "100", "bot_id": str(self.bot_id),
        })
        self.assertEqual(corrected.status_code, 302)

        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            result = DailyResult.query.filter_by(account_id=self.account_id).one()
            self.assertEqual(float(account.current_balance), 50100)
            self.assertEqual(float(account.peak_balance), 50100)
            self.assertEqual(float(account.max_loss_limit), 48100)
            self.assertEqual(float(account.best_day_so_far), 100)
            self.assertEqual(float(result.pnl), 100)
            self.assertEqual(ActivityLog.query.filter_by(account_id=self.account_id).count(), 2)

    def test_eod_floor_moves_only_when_day_is_closed(self):
        self.client.post("/accounts/{}/trades".format(self.account_id), data={
            "pnl": "500", "bot_id": str(self.bot_id),
        })
        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            self.assertEqual(float(account.current_balance), 50500)
            self.assertEqual(float(account.max_loss_limit), 48000)

        self.client.post("/accounts/{}/close-day".format(self.account_id), data={})
        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            self.assertEqual(float(account.max_loss_limit), 48500)
            self.assertEqual(DailyResult.query.filter_by(account_id=self.account_id).one().source, "TRADES")

    def test_csv_preview_then_import_is_idempotent(self):
        csv_data = b"Trade ID,Date/Time,Profit,Side,Qty,Fill Price\nA1,2026-09-25 09:30:00,100,Long,2,20000\nA2,2026-09-26 09:30:00,-50,Short,1,20010\n"
        form = {"account_id": str(self.account_id), "bot_id": str(self.bot_id)}
        preview = self.client.post("/api/import-csv", data={
            **form, "file": (io.BytesIO(csv_data), "fills.csv"),
        }, content_type="multipart/form-data")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json["count"], 2)
        self.assertTrue(preview.json["preview"])

        imported = self.client.post("/api/import-csv", data={
            **form, "confirm_import": "1", "finalize_days": "1",
            "file": (io.BytesIO(csv_data), "fills.csv"),
        }, content_type="multipart/form-data")
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json["imported"], 2)
        self.assertEqual(imported.json["closed_days"], 2)

        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            self.assertEqual(float(account.current_balance), 50050)
            self.assertEqual(Trade.query.filter_by(account_id=self.account_id).count(), 2)
            self.assertEqual(DailyResult.query.filter_by(account_id=self.account_id).count(), 2)

        duplicate_preview = self.client.post("/api/import-csv", data={
            **form, "file": (io.BytesIO(csv_data), "fills.csv"),
        }, content_type="multipart/form-data")
        self.assertEqual(duplicate_preview.json["count"], 0)
        self.assertEqual(duplicate_preview.json["duplicates"], 2)

    def test_csv_can_append_fills_to_an_open_day(self):
        first_csv = b"Trade ID,Date/Time,Profit\nA1,2026-09-26 09:30:00,100\n"
        second_csv = b"Trade ID,Date/Time,Profit\nA1,2026-09-26 09:30:00,100\nA2,2026-09-26 10:30:00,-25\n"
        form = {"account_id": str(self.account_id), "bot_id": str(self.bot_id)}

        for csv_data in (first_csv, second_csv):
            response = self.client.post("/api/import-csv", data={
                **form, "confirm_import": "1", "file": (io.BytesIO(csv_data), "partial.csv"),
            }, content_type="multipart/form-data")
            self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            self.assertEqual(float(account.current_balance), 50075)
            self.assertEqual(Trade.query.filter_by(account_id=self.account_id).count(), 2)
            self.assertEqual(DailyResult.query.filter_by(account_id=self.account_id).count(), 0)

    def test_tradingview_entry_exit_export_imports_one_trade_per_pair(self):
        csv_data = (
            b"Trade number,Type,Date and time,Signal,Price USD,Size (qty),Net PnL USD\n"
            b"1,Entry long,2026-09-26 09:30:00,Buy,20000,2,100\n"
            b"1,Exit long,2026-09-26 09:32:00,Take profit,20010,2,100\n"
            b"2,Entry short,2026-09-26 10:00:00,Sell,20020,1,-25\n"
            b"2,Exit short,2026-09-26 10:01:00,Stop,20025,1,-25\n"
        )
        imported = self.client.post("/api/import-csv", data={
            "account_id": str(self.account_id),
            "bot_id": str(self.bot_id),
            "confirm_import": "1",
            "finalize_days": "1",
            "file": (io.BytesIO(csv_data), "tradingview.csv"),
        }, content_type="multipart/form-data")

        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json["imported"], 2)
        with self.app.app_context():
            trades = Trade.query.filter_by(account_id=self.account_id).order_by(Trade.id).all()
            account = db.session.get(Account, self.account_id)
            self.assertEqual(float(account.current_balance), 50075)
            self.assertEqual([trade.direction for trade in trades], ["LONG", "SHORT"])
            self.assertEqual(trades[0].opened_at.hour, 9)
            self.assertEqual(trades[0].closed_at.minute, 32)
            self.assertEqual(float(trades[0].fill_price), 20000)
            self.assertEqual(float(trades[0].exit_fill_price), 20010)
            self.assertEqual(trades[0].signal_name, "Buy")
            self.assertEqual(db.session.get(Bot, self.bot_id).events.filter_by(event_type="FILL").count(), 2)

    def test_new_pages_render_with_account_data(self):
        for path in ("/", "/bots", "/api/imports", "/calendar", "/projections",
                 "/stats", "/advisor", "/log",
                     "/accounts/{}".format(self.account_id)):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_duplicate_bot_version_does_not_raise_server_error(self):
        response = self.client.post("/bots", data={
            "name": "Test Bot", "version": "1", "source": "TradingView",
        })

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(Bot.query.filter_by(user_id=self.user_id, name="Test Bot").count(), 1)

    def test_backtest_csv_creates_private_distribution_without_changing_balance(self):
        csv_data = b"Profit\n200\n-100\n0\n"
        form = {
            "name": "Test Bot v1 backtest",
            "account_id": str(self.account_id),
            "bot_id": str(self.bot_id),
            "base_risk": "100",
            "signal_frequency": "0.68",
        }
        preview = self.client.post("/api/import-distribution", data={
            **form, "file": (io.BytesIO(csv_data), "backtest.csv"),
        }, content_type="multipart/form-data")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json["wins"], 1)
        self.assertEqual(preview.json["losses"], 1)

        imported = self.client.post("/api/import-distribution", data={
            **form, "confirm_import": "1", "file": (io.BytesIO(csv_data), "backtest.csv"),
        }, content_type="multipart/form-data")
        self.assertEqual(imported.status_code, 200)

        with self.app.app_context():
            account = db.session.get(Account, self.account_id)
            distribution = Distribution.query.one()
            self.assertEqual(float(account.current_balance), 50000)
            self.assertEqual(account.distribution_id, distribution.id)
            self.assertEqual(distribution.user_id, self.user_id)
            self.assertEqual(distribution.bot_id, self.bot_id)
            self.assertEqual(distribution.win_multiples, [2.0])
            self.assertEqual(distribution.loss_multiples, [-1.0])

    def test_backtest_import_uses_exit_rows_only(self):
        csv_data = (
            b"Trade number,Type,Date and time,Net PnL USD\n"
            b"1,Entry long,2026-09-26 09:30:00,200\n"
            b"1,Exit long,2026-09-26 09:32:00,200\n"
            b"2,Entry short,2026-09-26 10:00:00,-100\n"
            b"2,Exit short,2026-09-26 10:01:00,-100\n"
        )
        form = {
            "name": "Paired export",
            "account_id": str(self.account_id),
            "base_risk": "100",
        }
        imported = self.client.post("/api/import-distribution", data={
            **form, "confirm_import": "1", "file": (io.BytesIO(csv_data), "backtest.csv"),
        }, content_type="multipart/form-data")

        self.assertEqual(imported.status_code, 200)
        with self.app.app_context():
            distribution = Distribution.query.one()
            self.assertEqual(distribution.win_multiples, [2.0])
            self.assertEqual(distribution.loss_multiples, [-1.0])


if __name__ == "__main__":
    unittest.main()