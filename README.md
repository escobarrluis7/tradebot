# One-Trade-a-Day Ensemble Bot

Trading analytics and a daily trading bot for any account: a personal brokerage (Alpaca)
or a futures prop-firm account (Lucid, Take Profit Trader, Alpha Futures, and others).

Every day the bot scores a watchlist with six well-documented strategies, combines
them, and takes **one** trade in the single strongest setup. If nothing is strong
enough, it stays in cash. It runs on a **paper account by default**.

## Quick start
```bash
pip install -r requirements.txt
python -m tradebot backtest --per-strategy   # 14-year history test
python -m tradebot signal                    # today's ranked signals + chosen trade
python -m tradebot run                       # settle yesterday's trade, place today's
python -m tradebot status                    # account P&L
python -m tradebot dashboard --source backtest   # analytics app (see below)
```
Run `run` once a day, either after the close (4:15 PM ET or later) or before the open.
Example cron entry (weekdays, 5:00 PM ET):
`0 17 * * 1-5  cd /path/to/tradebot && python -m tradebot run >> data/log.txt 2>&1`

## How it decides
| Strategy | Idea | Evidence base |
|---|---|---|
| `trend` | Price vs. 200-day SMA, 50/200 spread | Trend following / Faber-style timing |
| `tsmom` | 12-1 month return ÷ volatility | Moskowitz, Ooi & Pedersen time-series momentum |
| `xsmom` | 6-month relative-strength rank | Jegadeesh & Titman cross-sectional momentum |
| `mean_reversion` | RSI(2) pullback inside an uptrend | Connors short-term reversal |
| `breakout` | 20-day Donchian high + volume | Turtle-style breakout |
| `macd` | MACD histogram ÷ ATR | Momentum oscillator |

* **Combining:** a weighted average of the strategy scores (`strategies:` in `config.yaml`).
  `adaptive_weights: true` shifts weight toward strategies whose scores have recently
  ranked next-day returns best. It looks only at past results, with no look-ahead.
* **Regime filter:** if SPY is below its 200-day SMA, a setup has to be twice as strong.
* **Risk:** position size risks `risk_per_trade` of equity to a stop `stop_atr_mult` × ATR
  below entry. Take-profit is `target_atr_mult` × ATR. Position size is capped at `max_position_pct`.
* **Timing:** signals use the close of day *t*. The trade buys at the open of *t+1* and
  sells at the next open, the same moment the next trade buys, so it makes one rotation
  per day. The backtester and the paper broker share the same fill engine.

## Backtest results (2013–2026, 24 symbols, 2 bps slippage per side)
| | CAGR | Sharpe | Max drawdown |
|---|---|---|---|
| Default ensemble | 11.5% | 0.91 | −17% |
| Trend only | 14.9% | 1.11 | −20% |
| SPY buy & hold | 14.2% | 0.88 | −34% |

What the testing found:
* **Selling at the same day's close (`exit_at: close`) lost money for every strategy**, about
  −7% a year. Most equity returns arrive overnight, so the bot holds overnight by default.
* The bot gives up some raw return compared with SPY. It does better on risk: roughly half
  the drawdown and a similar or better Sharpe ratio. Part of the return gap comes from
  position sizing, which averages about 65% invested.
* The defaults were picked using 2013–2019 data only. On 2020–2026 data, which they
  were not tuned on, they returned 9.9% a year with a Sharpe of 0.80.
* Backtests are optimistic. They assume no taxes, and real fills will differ. A watchlist of
  today's winners (NVDA, META, …) also adds survivorship bias. Past results don't guarantee anything.

## Your accounts (`accounts.yaml`)
Every account you trade gets its own analytics, and its own trading mode if you want one:

| mode | What the bot does | Use it for |
|---|---|---|
| `auto` | places trades (Alpaca API, or a webhook relay such as TradersPost or PickMyTrade) | brokers, and prop firms that allow bots |
| `signal` | sends you the trade idea (Discord / Slack / ntfy); you place it yourself | prop firms that ban bots |
| `track` | nothing, analytics only | any account |

```bash
python -m tradebot accounts                               # list accounts, firm bot policy, imported trades
python -m tradebot import --account lucid-50k Performance.csv   # add trade history from your platform
python -m tradebot run --all-accounts --only-profile futures    # today's decision for every futures account
python -m tradebot dashboard                              # one dashboard with an account picker
```

**Importing history.** Export a CSV from your platform and run `import`. Running it again only adds new trades.
* Tradovate: Account → Reports → Performance → download CSV (`import_format: tradovate`)
* NinjaTrader: Trade Performance → Trades → right-click → Export (`import_format: ninjatrader`)
* Rithmic platforms, TradingView and others: any CSV with symbol / side / qty / prices / P&L / times (`generic`)

**Prop rules.** Fill in `rules:` for each account with the numbers from *your* plan. The dashboard
then shows a "Prop firm rules" panel:
* room left before the drawdown limit (trailing end-of-day, trailing intraday, or static, with an optional lock level)
* profit-target progress
* the daily loss limit
* the consistency rule (best day's share of total profit)

The equity chart also draws the drawdown limit as a line. These figures are calculated from your
imported trades, and your firm's own dashboard remains the official record.

**Firm automation policies** (`tradebot/firms.py`, checked November 2026 from public sources; firms change these often):

| Firm | Bots | Flat by (ET) | What the app does |
|---|---|---|---|
| Lucid Trading | allowed within firm rules (no HFT / microscalping; LucidDirect may need written approval) | 4:45 PM (Tradovate), 4:15 PM (Rithmic) | `auto` possible via a relay, `signal` by default |
| Take Profit Trader | prohibited (approved copiers only) | about 4:55 PM | `signal` only; `auto` is refused |
| Alpha Futures | full automation prohibited; signal tools OK if you place the trades | before the close | `signal` only; `auto` is refused |
| Any other firm | unknown | set your own | `auto` needs `i_confirm_firm_allows_automation: true` |

**Connections.** Tradovate's own API excludes prop-firm and evaluation accounts. For automated
orders on a prop account, use a relay that your firm permits (TradersPost, PickMyTrade, CrossTrade
for NinjaTrader, ...). Put the relay's webhook URL in the env var named by `url_env`. The bot sends
TradersPost-format JSON: a market entry with a stop-loss, then `{"action": "exit"}` at the cutoff.
Schedule `python -m tradebot flatten --all-accounts --only-profile futures` for about 4:35 PM ET on a
machine whose clock you trust (a VPS or your own computer). GitHub's scheduler can run late, so
don't rely on it for the exit, and keep the stop-loss on every order.

### Futures strategy: read this first
The `futures` profile trades micro equity-index futures (MES, MNQ, MYM, M2K). It enters at the
6 PM ET Globex open and exits before the afternoon cutoff, so it never holds across the daily close.
Prop-aware sizing risks the smallest of 1% of the account, half the daily loss limit, and a quarter
of the remaining drawdown room. If even one micro contract would risk more than that, the bot skips the day.

**In testing (2013–2026), the daily signals had no edge on one-session futures holds.** Results:
* about +1% a year overall
* 0.0% a year on 2020–2026
* a worst drop of about $8,600 on a 50K account, which would fail a typical $2,000–2,500 drawdown limit

The edge in this strategy comes from holding overnight, and prop rules don't allow that. Use the
futures profile for alerts and tracking until a futures-specific (intraday) strategy is built and tested.
`python -m tradebot --profile futures backtest --per-strategy` reproduces these numbers.

## TradingView version (`tradingview/daily_one_trade.pine`)
The same stock strategy as a TradingView indicator. On 2014–2026 data it chooses the same daily pick
as the Python bot on 99.8% of days.
1. In TradingView, open a **daily** chart (e.g. SPY), then open **Pine Editor** at the bottom of the screen.
2. Delete what's in the editor, paste the whole file, and click **Save**, then **Add to chart**.
   A table appears showing today's pick and the top 10 ranking.
3. Click **Alert** (the clock icon) and set the condition to **Daily One-Trade Ensemble** → **Any alert() function call**.
   Turn on app or email notifications, and set expiration to open-ended if your plan allows it.
4. Each evening at the close you get the next day's trade, for example:
   * `{"ticker":"XLV","action":"buy","quantity":29,...,"stopLoss":{"type":"stop","stopPrice":183.84}}`
   * an `exit` message for the previous pick
5. Optional, on paid TradingView plans: tick **Webhook URL** in the alert and paste your
   TradersPost (or similar) URL. Orders then go to your broker automatically. Only do this where bots are allowed.

Set **Account size** in the indicator's settings, since TradingView can't see your broker balance.
Market orders sent after the close execute at the next open, if your broker or relay queues them.

## Analytics dashboard
```bash
python -m tradebot dashboard                        # every account in accounts.yaml (picker)
python -m tradebot dashboard --source backtest      # 14-year simulated history
python -m tradebot dashboard --source paper         # built-in paper simulator
python -m tradebot dashboard --source alpaca        # your real Alpaca account (paper or live)
python -m tradebot dashboard --source alpaca --serve  # live web app at http://localhost:8050
```
Each command writes one self-contained HTML file to `data/`, which opens in any browser.
* **Daily / Weekly / Monthly / Yearly tabs:** a P&L bar chart plus a table with trades, win
  rate, P&L, % return, best and worst trade, top symbol, and ending equity for every period.
* **Headline numbers:** equity, total return, last day, win rate, profit factor, max drawdown,
  Sharpe ratio, and average trade.
* **Charts and tables:** an equity curve (against SPY in the backtest) with 1M/3M/1Y/5Y/All
  ranges, drawdown, P&L by symbol, how trades ended, streaks, and the full trade log.

For Alpaca, the dashboard reads the account's daily equity history and every fill,
and pairs buys with sells into round-trip trades.

## Connecting Alpaca (automatic trading)
1. Create a free account at alpaca.markets. In the **Paper Trading** dashboard, generate an API key and secret.
2. Put this project in a **private** GitHub repository. Your imported trade history
   (`data/accounts/*/trades.csv`) is committed so the scheduled dashboard can include it.
3. In the repo, go to **Settings → Secrets and variables → Actions** and add the secrets
   `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`. Optionally add `NOTIFY_URL` for phone alerts, and a
   relay URL such as `LUCID_50K_WEBHOOK_URL`.
4. That's it. `.github/workflows/daily-trade.yml` runs stock accounts every weekday before the open,
   and sends futures alerts at about 5:15 PM ET. For Alpaca, each run:
   * checks with Alpaca that the market opens today, and skips holidays
   * builds today's signal from yesterday's close
   * sells yesterday's position at the open, or keeps it and renews the stop if it's still the top pick
   * buys today's pick at the open with a protective stop 2× ATR below
   * rebuilds the dashboard. Download it from the run's page, under **Artifacts → trading-dashboard**.

   Use **Actions → Daily trade → Run workflow** to trigger it by hand.

Safety rails:
* **Paper by default.** Real money needs both `allow_live: true` in `config.yaml` and the repo
  variable `ALPACA_LIVE=1`.
* **No double orders.** Each order carries a unique id for its signal day, so re-running the same
  morning does nothing.
* **Activity log.** Every action is written to `data/journal.csv`.

To run it on your own computer instead, export the two keys, set `TRADEBOT_BROKER=alpaca`, and
schedule `python -m tradebot run` for about 8:30 AM ET on weekdays.

## Tuning
Everything is in `config.yaml`: the watchlist, weights, stops and targets, the threshold, shorting, and hold period.
Re-run `backtest --per-strategy` after each change, and judge changes on more than one time period.
