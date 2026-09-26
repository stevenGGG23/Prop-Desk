# Prop Desk

A private portfolio dashboard for tracking prop firm futures accounts, sizing trades, and answering the two questions that actually matter every day: **how many losses can this account take right now**, and **how long until it passes**.

Built for accounts running an automated MNQ strategy through TradingView to TradersPost, across Lucid Trading, Tradeify, and any firm added later.

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
| Host | Render (web service + Postgres) | |

No React, no Tailwind CDN, no component library. Hand-written CSS with a real type scale.

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
This is the headline number. If the user wants to survive 3 losses, this is the largest risk setting that still does it. The UI must show the **whole ladder** (2, 3, 4, 5, 6 losses), because the difference between 1,400 and 1,798 is the same safety with meaningfully more speed. Never show only one recommendation.

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
- One card per account: phase, balance, room, losses survivable, progress to target, current risk setting, and whether that setting is still correct
- **Portfolio health score** driven by the weakest account, not the average
- Joint-wipeout probability across all accounts on the shared signal
- Total projected monthly income once all accounts are funded

### Account detail
- Full trade log
- Equity curve with the trailing floor drawn underneath it
- Risk ladder table (2 through 6 losses survivable, with pass odds and expected trades for each)
- Live warning when current risk exceeds the 3-loss threshold

### Trade log
- Add a trade: date, direction, entry, exit, size, P&L, which account
- Bulk import from a TradingView strategy CSV export
- Every entry recomputes account state, floor, win rate, and recommendations

### Settings advisor
The daily driver. For each account, given today's state:
- recommended risk
- what to change it to after a win
- what to change it to after a loss
- when the floor locks and the account can safely size up
- if the user proposes an aggressive or full-port size, show the real odds, including the single-win-clears-target check and the contract-cap rejection rate

Never silently accept a proposed size. Always show the ladder next to it.

### Stats
- Overall win rate and per-account win rate
- **Win rate by weekday**, with sample size shown next to every figure. A 56% Thursday over 9 trades is noise, and the UI must make that obvious rather than inviting the user to build a filter on it
- Live win rate versus source-backtest win rate, side by side. This gap is the single largest source of error in every projection
- Slippage tracker: signal price versus actual fill, entry and exit, averaged over time. This is the number everything else depends on

### Activity log
Append-only. Every trade, setting change, payout, phase change, and breach, timestamped. Never editable. This is the audit trail that makes the stats trustworthy.

### Multi-user
Separate login per person, fully isolated data. Steven, Boula, Martin. No shared views, no cross-account visibility.

---

## Data model

```
User            id, username, password_hash, display_name, created_at
Firm            id, name, default_rules_json
Account         id, user_id, firm_id, nickname, external_id, phase,
                starting_balance, current_balance, peak_balance,
                max_loss_limit, drawdown_amount, drawdown_type, lock_threshold,
                profit_target, daily_loss_limit, dll_is_hard,
                consistency_pct, contract_cap, current_risk, cost_paid,
                opened_at, closed_at
Trade           id, account_id, opened_at, closed_at, direction,
                signal_price, fill_price, exit_signal_price, exit_fill_price,
                quantity, pnl, r_multiple, was_rejected, rejection_reason
Payout          id, account_id, requested_at, gross, net, balance_after
Distribution    id, name, source, win_multiples_json, loss_multiples_json,
                qty_at_base_risk_json, base_risk, signal_frequency
ActivityLog     id, user_id, account_id, kind, message, payload_json, created_at
```

`Distribution` is what the simulator samples from. Ship with one seeded from a strategy CSV; allow re-import when the strategy changes. **A distribution with a different risk-reward ratio is a different distribution**, not a parameter tweak. A 1:1 setting and a 2.2 setting had win rates of 75.6% and 50.6% on the same strategy.

---

## Routes

```
GET  /login
POST /login
GET  /logout

GET  /                       portfolio dashboard
GET  /accounts/new
POST /accounts
GET  /accounts/<id>
POST /accounts/<id>/trades
POST /accounts/<id>/payouts
POST /accounts/<id>/risk     update current risk setting

GET  /advisor                cross-account daily recommendations
GET  /stats
GET  /log

GET  /api/simulate           ?account_id&risk[]  -> odds for each candidate
GET  /api/portfolio-risk     joint simulation across all accounts
POST /api/import-csv         TradingView strategy export
```

Simulation endpoints return JSON and are called from the client so risk sliders update live. Cache results by `(account_state_hash, risk)` for 15 minutes. Simulations are expensive and account state only changes on a trade.

---

## Design direction

The brief is "does not look vibe coded." Concretely:

- **Dark, but not black.** Background around `#0b0e13`, cards a step lighter with a visible 1px border. Pure black with neon accents reads as a template
- **One accent color**, used only for the number that matters on each card. Everything else is grayscale
- **Tabular figures** for all numbers (`font-variant-numeric: tabular-nums`). Financial data that shifts as it updates looks broken
- **A real type scale.** Three sizes on a card, maximum. Big number, label, supporting detail
- **Semantic color only.** Green and red mean profit and loss, nothing else. Do not color a heading green because it looks nice
- **Density over whitespace.** This is a working dashboard, not a landing page. Five accounts should fit above the fold
- **No gradients, no glass, no emoji, no animated counters**
- Loading states for every simulation call. They take a second and a frozen UI feels broken

Charts: Chart.js, grid lines at 10% opacity, no legends when a single series is obvious, no drop shadows.

---

## Security

The whole point of this app is that it holds a private record of real money. Treat it that way.

- Passwords are **hashed with Werkzeug**, never stored in plain text and never committed
- Initial passwords are set from environment variables at first boot and must be changed on first login. Do not hardcode credentials anywhere in the repo
- `Password123` is fine for local development and is not acceptable in production. Render sets `SECRET_KEY` and initial passwords as environment variables
- Session cookies: `Secure`, `HttpOnly`, `SameSite=Lax`
- Rate limit the login route
- No registration route. Users are seeded by a management command
- `.env` in `.gitignore` from the first commit

---

## Deployment (Render)

1. Create a **PostgreSQL** instance, copy the internal connection string
2. Create a **Web Service** from this repo
   - Build: `pip install -r requirements.txt && flask db upgrade`
   - Start: `gunicorn app:app`
3. Environment variables:

```
DATABASE_URL       from the Render Postgres instance
SECRET_KEY         long random string
FLASK_ENV          production
SEED_USERS         comma separated, e.g. "Steven Gobran,Boula Salib,Martin"
SEED_PASSWORD      temporary, must be changed on first login
```

4. Run `flask seed-users` once from the Render shell

**Do not use SQLite.** The free tier wipes the filesystem on every deploy and sleep cycle. The entire requirement of "stays updated and cached the same every visit" depends on Postgres.

---

## Build order

1. Auth, user model, seeded login, base layout
2. Account model and CRUD, one hardcoded firm ruleset
3. Trade entry, balance and trailing-floor recomputation
4. Room and risk ladder, displayed on the account card
5. Monte Carlo engine plus `/api/simulate`
6. Portfolio dashboard with joint simulation
7. CSV import and the distribution model
8. Funded-phase payout modelling and the withdrawal discipline curve
9. Stats page, weekday breakdown, slippage tracker
10. Activity log
11. Design pass

Ship 1 through 4 before writing any simulation code. An account card that correctly shows room and the risk ladder is already more useful than the spreadsheet it replaces.

---

## Acceptance criteria

- Adding a trade updates balance, peak, trailing floor, room, losses survivable, and win rate, and writes an activity log entry, in one action
- The risk ladder shows at least four loss counts with pass odds for each
- Proposing a risk setting above the 3-loss threshold triggers a visible warning with the real odds, not a silent accept
- Portfolio joint-wipeout probability uses a shared trade sequence
- Every win-rate figure displays its sample size
- No credentials in the repository
- Data survives a redeploy

---

## Known limits

Every projection assumes the imported distribution keeps describing the future. It probably does not, exactly. Backtest exports typically carry **zero commission and zero slippage**, so treat imported win rates as an optimistic ceiling and let the slippage tracker replace them with measured numbers as real fills accumulate. The app should make that divergence visible rather than hiding it behind a single confident number.

This is a record-keeping and decision-support tool. It does not place orders, and it is not financial advice.
