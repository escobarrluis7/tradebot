# One-Trade-a-Day Ensemble Bot

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

## Analytics dashboard
```bash
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
2. Put this project in a GitHub repository, either private or public.
3. In the repo, go to **Settings → Secrets and variables → Actions** and add the secrets
   `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.
4. That's it. `.github/workflows/daily-trade.yml` runs every weekday before the open. Each run:
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
