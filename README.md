# Prop Desk

A private portfolio dashboard for tracking prop firm futures accounts, sizing trades, and answering the two questions that actually matter every day: **how many losses can this account take right now**, and **how long until it passes**.

Built for accounts running an automated MNQ strategy through TradingView → TradersPost, across Lucid Trading, Tradeify, and any firm added later.

**Live:** [prop-desk-nb7p.onrender.com](https://prop-desk-nb7p.onrender.com/)

---

## Screenshots

### Portfolio
![Portfolio dashboard](docs/screenshots/portfolio.png)

### Daily Advisor
![Advisor page showing risk ladder per account](docs/screenshots/advisor.png)

### Stats
![Stats page with per-account win rate and weekday breakdown](docs/screenshots/stats.png)

### Calendar
![Trading calendar with daily P&L](docs/screenshots/calendar.png)

---

## Why this exists

Prop accounts live and die on one number: the gap between your balance and your max loss limit. That number moves every day, and the correct position size moves with it. Doing this by hand across five accounts at four different firms does not scale, and getting it wrong once ends an account.

This app keeps every account's state in one place, recomputes the right risk setting after every trade, and shows what each account is actually worth per month once funded.

---

## Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python 3.11 + Flask | Matches existing project experience |
| ORM | SQLAlchemy | Same |
| Database | **PostgreSQL** (Render managed) | Render's filesystem is ephemeral. SQLite would wipe on every redeploy. This is not optional |
| Migrations | Alembic | Schema will change as firms are added |
| Auth | Flask-Login + Werkzeug password hashing | |
| Frontend | Jinja2 templates + vanilla JS + Chart.js | No build step, no framework churn |
| Math | NumPy | Monte Carlo simulations |
| Scheduler | APScheduler | Daily email reports at 6 AM ET Mon–Fri |
| Host | Render (web service + Postgres) | |

No React, no Tailwind CDN, no component library. Hand-written CSS with a real type scale, dark/light mode.

---

## Core concepts

### Account state

Every account is defined by:

```
starting_balance      e.g. 150000
current_balance       updated after each trade
max_loss_limit        the floor. Account dies if balance touches it
drawdown_type         EOD_TRAILING | INTRADAY_TRAILING | STATIC
lock_threshold        floor stops trailing here (usually start + 100)
profit_target         eval only
daily_loss_limit      nullable. soft (stops the day) or hard (kills account)
consistency_pct       nullable. max share of profit one day may be
contract_cap          max micros
phase                 EVAL | FUNDED | LIVE | BREACHED | PASSED
```

### The four derived numbers

Every account card shows these, recomputed on every trade:

**1. Room**
```
room = current_balance - max_loss_limit
```

**2. Losses survivable**
```
losses = floor(room / (risk_per_trade + friction))
```
`friction` defaults to 25 per trade and is configurable. It covers commission and market-order slippage, which no backtest includes.

**3. Max risk for a target loss count**
```
max_risk(n) = (room / n) - friction
```
This is the headline number. If the user wants to survive 3 losses, this is the largest risk setting that still does it. The UI shows the **whole ladder** (2, 3, 4, 5, 6 losses), because the difference between 1,400 and 1,798 is the same safety with meaningfully more speed. Never show only one recommendation.

**4. EOD trailing floor update**
```
if balance > peak_balance:
    peak_balance = balance
    max_loss_limit = min(peak_balance - drawdown_amount, lock_threshold)
```
Once the floor reaches `lock_threshold` it never moves again. Flag this on the card, because it changes the whole risk picture.

---

## The simulation engine

`engine/montecarlo.py`. This is the heart of the app.

Input: a **trade distribution** (win multiples and loss multiples expressed as fractions of 1R, imported from a TradingView strategy export), plus an account's current state and a candidate risk setting.

Output, from 20,000+ bootstrap runs:

- probability of passing the eval
- probability of breaching
- median and 75th percentile trades to pass
- median calendar days to pass (using observed signal frequency, not 1 trade per day)

### Rules the engine must model

These are not optional. Each one changed a real answer during manual analysis:

**Wins are not full R.** Average win is around 0.97R and the smallest observed is 0.70R. When remaining profit needed is close to one win, compute `P(single win clears target)` explicitly. A 9,050 risk setting on a 9,000 target only clears in 47% of wins, which turns a "75% one-shot" into 68%.

**Contract caps reject orders.** Estimate order size as `round(max_qty_in_source × risk / source_risk)`. If it exceeds `contract_cap`, the signal is rejected: no trade, no P&L, advance the day. Show the rejection rate alongside the odds.

**Consistency rules gate the pass.** Hitting the profit target is not enough if `best_day / total_profit > consistency_pct`. Keep trading until it clears.

**Daily loss limits cap a day's loss** rather than ending the account, unless the firm marks them hard.

**Signal frequency.** The source strategy trades roughly 68% of weekdays. Trades and calendar days are different units and the UI must never conflate them.

**Correlation.** All accounts run the same signal, so they are one bet repeated. Portfolio-level simulation must use a **single shared trade sequence** across accounts, not independent draws. Independent draws understate joint-wipeout risk by roughly 500x.

### Funded-phase simulation

Separate model. Given payout rules, project monthly income:

```
payout_frequency        DAILY | EVERY_N_DAYS | N_QUALIFYING_DAYS
qualifying_day_min      minimum profit for a day to count
payout_cap              per request, may be a ladder that rises per payout
payout_formula          FLAT_CAP | PCT_OF_PROFIT | MULTIPLE_OF_RECENT_GAIN
min_balance_to_withdraw buffer
profit_split            0.90 etc
max_payouts             null = unlimited
```

The single most important output here is the **withdrawal discipline curve**: monthly income and breach probability as a function of how much cushion you leave in the account. Withdrawing to the firm's minimum versus holding an extra few thousand is routinely the difference between 3,300/month at 10% breach and 1,800/month at 90% breach. Plot it. Make the recommended floor impossible to miss.

---

## Features

### Portfolio dashboard
- One card per account: phase, balance, room, losses survivable, progress to target, current risk setting, estimated days to pass
- TradingView-style sparkline equity curve per card
- Green/red border stripes show net account performance
- 3-dot menu on each card → View details · Edit · Delete
- **Portfolio health score** and joint-wipeout probability across all accounts
- Light/dark mode toggle (persisted in localStorage)

### Webhook integration
- One master inbound URL per user — connect all accounts through a single TradingView / TradersPost webhook
- Route trades to accounts by including `"account": "nickname"` in the JSON payload
- Parses action, sentiment, price, pnl, balance, quantity from standard TradersPost/TV format
- Account balance updates automatically on each fill

### Daily email report
- Sent at **6 AM ET, Mon–Fri** automatically via APScheduler
- Covers balance, room, losses survivable, and risk setting for all accounts
- No button required — fires on schedule even when the app is sleeping

### Account detail
- Full trade log with TradingView-style two-row layout (Exit row / Entry row per trade)
- Equity curve with the trailing floor drawn underneath it
- Risk ladder table (2 through 6 losses survivable, with pass odds and expected trades for each)
- Live warning when current risk exceeds the 3-loss threshold

### Daily advisor
- Per-account risk ladder with Speed vs Conservative column
- After-a-win and after-a-loss recommended risk shown separately
- Floor lock status and when it triggers
- One-click "Set risk" to update the account from the advisor page

### Stats
- Overall win rate and per-account win rate
- **Win rate by weekday**, with sample size shown next to every figure. A 56% Thursday over 9 trades is noise, and the UI makes that obvious
- Slippage tracker: signal price versus actual fill, averaged over time

### Calendar
- Monthly view grouped by trading day
- Per-account filter
- Month P&L, trade count, and trading day count in the header

### Projections
- Funded-phase payout projection with editable payout assumptions
- Withdrawal discipline curve showing monthly income vs breach risk at different cushion levels

### Activity log
Append-only. Every trade, setting change, payout, phase change, and breach, timestamped. Never editable.

### Multi-user
Separate login per person, fully isolated data. No shared views, no cross-account visibility.

---

## TradingView alert setup

1. Open a chart with your strategy → **Alerts** → **+ Alert**
2. Set **Condition** to your strategy, **Interval** to match your timeframe
3. Under **Notifications**, enable **Webhook URL** and paste your URL from the Portfolio page → *Webhook setup*
4. In the **Message** box, paste pure JSON — no text before or after it:

```json
{
  "account": "Flex 50K",
  "ticker": "{{ticker}}",
  "action": "{{strategy.order.action}}",
  "sentiment": "{{strategy.market_position}}",
  "price": {{close}},
  "pnl": {{strategy.netprofit}}
}
```

Replace `"Flex 50K"` with your account's exact nickname. The `{{...}}` placeholders are TradingView variables — leave them as-is. One webhook URL handles all accounts; just change the `"account"` field per alert.

| Field | What it does | Required? |
|---|---|---|
| `account` | Routes to the matching account (case-insensitive) | Yes |
| `action` | `buy` or `sell` | No |
| `pnl` | Cumulative strategy P&L — adjusts balance by the delta | No |
| `balance` | Absolute balance — overrides directly | No |
| `price` | Fill price | No |
| `quantity` | Contract count | No |

---

## Data model

```
User            id, username, password_hash, display_name, inbound_token, created_at
Firm            id, name, logo_filename, default_rules_json
Account         id, user_id, firm_id, nickname, external_id, phase,
                starting_balance, current_balance, peak_balance,
                max_loss_limit, drawdown_amount, drawdown_type, lock_threshold,
                profit_target, daily_loss_limit, dll_is_hard,
                consistency_pct, contract_cap, current_risk, cost_paid,
                best_day_so_far, distribution_id, opened_at, closed_at
Trade           id, account_id, opened_at, closed_at, direction,
                signal_price, fill_price, exit_signal_price, exit_fill_price,
                quantity, pnl, r_multiple, was_rejected, rejection_reason,
                bot_id, import_hash
DailyResult     id, account_id, bot_id, trade_date, pnl, source
Payout          id, account_id, requested_at, gross, net, balance_after
Distribution    id, name, source, win_multiples_json, loss_multiples_json,
                qty_at_base_risk_json, base_risk, signal_frequency
ActivityLog     id, user_id, account_id, kind, message, payload_json, created_at
WebhookReceiver id, user_id, account_id, token
```

---

## Routes

```
GET  /login
POST /login
GET  /logout

GET  /                               portfolio dashboard
GET  /accounts/new
POST /accounts/new
GET  /accounts/<id>
GET  /accounts/<id>/edit
POST /accounts/<id>/edit
POST /accounts/<id>/delete
POST /accounts/<id>/trades
POST /accounts/<id>/daily-result
POST /accounts/<id>/close-day
POST /accounts/<id>/payouts
POST /accounts/<id>/risk

GET  /advisor
GET  /stats
GET  /log
GET  /trades                         TradingView-style trade list (filterable by account)
GET  /calendar
GET  /projections

POST /api/inbound/<token>            master inbound webhook
GET  /api/simulate                   ?account_id&risk[] → odds for each candidate
GET  /api/portfolio-risk             joint simulation across all accounts
POST /api/import-csv
POST /api/import-distribution
```

---

## Design

- **Dark, but not black.** Background `#0b0e13`, cards a step lighter with a 1px border
- **One accent color**, used only for the number that matters on each card
- **Tabular figures** (`font-variant-numeric: tabular-nums`) on all financial data
- **Light/dark mode** toggle in the nav, with theme-before-paint flash prevention
- **Semantic color only.** Green = profit, red = loss, nothing else
- Density over whitespace — five accounts above the fold

---

## Security

- Passwords hashed with Werkzeug, never stored or committed in plain text
- Session cookies: `Secure`, `HttpOnly`, `SameSite=Lax`
- Login route is rate limited
- No registration route — users are seeded by management command
- `.env` in `.gitignore` from the first commit

---

## Deployment (Render)

1. Create a **PostgreSQL** instance, copy the internal connection string
2. Create a **Web Service** from this repo
   - Build: `pip install -r requirements.txt && flask db upgrade`
   - Start: `gunicorn --workers 1 app:app`
3. Environment variables:

```
DATABASE_URL       from the Render Postgres instance
SECRET_KEY         long random string
FLASK_ENV          production
MAIL_FROM          sender address for daily reports
MAIL_TO            recipient address
MAIL_HOST          SMTP host
MAIL_PORT          587
MAIL_USER          SMTP username
MAIL_PASS          SMTP password
```

4. Run `flask seed-users` once from the Render shell

**Use `--workers 1` with gunicorn.** APScheduler runs in-process; multiple workers cause the daily email to fire multiple times.

**Do not use SQLite.** The free tier wipes the filesystem on every deploy. The entire requirement of "stays updated every visit" depends on Postgres.

---

## Running locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in DATABASE_URL and SECRET_KEY
flask db upgrade
flask seed-accounts            # optional: seed preset accounts
flask run
```

Tests:
```bash
python -m pytest tests/
```

---

## Known limits

Every projection assumes the imported distribution keeps describing the future. Backtest exports carry zero commission and zero slippage — treat imported win rates as an optimistic ceiling and let the slippage tracker replace them with measured numbers as real fills accumulate.

This is a record-keeping and decision-support tool. It does not place orders, and it is not financial advice.
