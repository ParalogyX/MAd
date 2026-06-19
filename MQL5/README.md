# Native MetaTrader 5 MAd strategy

This directory contains a native MQL5 Expert Advisor that ports the strategy from the Python `MAd` project into MetaTrader 5.

## Files

- `Experts/MAd/MAdStrategyEA.mq5` — scheduler, multi-symbol scanning, candidate persistence, entry validation, risk sizing, order execution, and scheduled closing.
- `Include/MAd/MAdTypes.mqh` — shared enums and structures.
- `Include/MAd/MAdSymbols.mqh` — offline ticker classification rules matching the Python project, plus manual overrides.
- `Include/MAd/MAdStrategy.mqh` — indicator calculations, candlestick detection, scoring, contradiction penalties, direction choice, SL/TP, and price-drift validation.
- `Files/MAd/symbol_groups.example.csv` — optional manual symbol-to-session-group overrides.

## Installation

Copy the repository's `MQL5` directory contents into the terminal data folder opened through **MT5 → File → Open Data Folder**. The resulting paths must be:

```text
MQL5/Experts/MAd/MAdStrategyEA.mq5
MQL5/Include/MAd/MAdTypes.mqh
MQL5/Include/MAd/MAdSymbols.mqh
MQL5/Include/MAd/MAdStrategy.mqh
```

Open `MAdStrategyEA.mq5` in MetaEditor, compile it, and attach exactly one instance to any chart. The chart symbol and timeframe do not affect the strategy because the EA is timer-driven and scans multiple symbols.

Automated trading is deliberately disabled by default with:

```text
InpAllowOrderExecution = false
```

Keep it disabled for the first terminal test. Enable it only after checking the Experts log, generated candidate files, trade plans, symbol grouping, prices, SL/TP, and calculated volumes on the intended account.

## Behaviour preserved from Python

At each enabled group's analysis time, the EA:

1. obtains every classified tradable symbol in that group;
2. loads D1, H4, and H1 rates with the same lookback and minimum-candle defaults;
3. calculates the Python strategy's EMA, RSI, MACD, ADX, ATR, and candlestick rules directly in MQL5;
4. calculates long and short component scores with the same weights and contradiction penalties;
5. discards neutral signals, sorts tradable signals by strength using a stable sort, and keeps the configured top 10;
6. writes a deterministic daily state CSV for the opening event and, optionally, a timestamped best-signals CSV.

At the group's opening time, the EA reads that stored snapshot and, for each candidate:

1. checks the cached analysis signal against the group's minimum strength;
2. obtains the current ask for a buy or bid for a sell;
3. applies the same ATR-relative price-drift rule as Python;
4. recalculates SL and TP from the actual entry price with the group's multipliers;
5. checks broker stop/freeze distances and existing-position conflicts;
6. calculates volume so that the estimated loss at SL is at most `InpRiskPerTradePercent` of the current account balance;
7. opens a market position when execution is enabled.

At the configured close time, the EA closes only positions carrying this EA's magic number and group-specific `MAd|...` comment. Positions already closed by SL or TP are naturally absent and are not closed again.

## Position sizing

Risk sizing uses `OrderCalcProfit()` for one lot between the actual entry price and SL. Because that function returns P/L in the account currency, the target and estimated loss are compared in the same currency:

```text
target loss = current account balance × risk percent / 100
raw volume  = target loss / absolute one-lot SL loss
```

Volume is rounded **down** to the broker's volume step. Therefore the final estimated loss can be slightly below 1%, but it is not intentionally rounded above 1%. If the broker's minimum lot would already exceed the requested risk, the trade is skipped. A broker maximum-volume limit can also reduce actual risk below the target.

## Sentiment hook

`MAdGetSentimentScore()` currently returns `0.0`, which is neutral. The original sentiment weight remains in the score, so neutral sentiment contributes 50 to both long and short sides exactly as in Python. A future transport can replace only this function without changing the strategy structure.

## Time handling

The default clock is `MAD_CLOCK_EUROPE_AMSTERDAM`. It derives CET/CEST from GMT and automatically applies the European last-Sunday daylight-saving transitions, matching the Python scheduler's `Europe/Amsterdam` default.

Small configurable catch-up windows prevent a long multi-symbol scan from missing an opening minute:

```text
InpMaximumAnalysisDelayMinutes = 30
InpMaximumOpenDelayMinutes     = 15
```

Normal events still start at their configured minute. The catch-up only applies when the timer was busy or the terminal resumed shortly after an event.

## Symbol groups

The EA can scan either all symbols exposed by the trading server or only Market Watch. It applies the same offline categories used by the Python project for crypto, forex, commodities, known stocks, ETFs, and indices.

Broker aliases can be forced into a group by copying `symbol_groups.example.csv` to the terminal's `MQL5/Files/MAd/symbol_groups.csv` and editing it. Supported group names are:

```text
crypto_24_7
forex_major
forex_exotic
europe_stock_index
us_stock_index
commodity_us
asia_index
israel_index
```

`InpIncludeSymbols` and `InpExcludeSymbols` accept semicolon-separated MT5 symbol names and provide another simple way to constrain the universe.

## Generated files

The terminal writes files under its sandboxed `MQL5/Files/MAd` directory:

```text
state/candidates_<group>_<date>.csv
best_signals/best_signals_<group>_<timestamp>.csv
trade_plans/trade_plan_<group>_<timestamp>.csv
results/execution_<group>_<timestamp>.csv
```

The `state` candidate file is always written because it is the restart-safe hand-off between analysis and opening. `InpWriteCsvFiles` controls the additional timestamped reporting files.

The native version does not recreate Python's retrospective close-result CSV from historical bars. It records analysis, validated trade plans, and every opening attempt, while actual fills and closes remain available in MT5 account history and the Experts log.

## Important first test

Use a demo account with `InpAllowOrderExecution=false`, let at least one complete analysis/open/close cycle run, and verify:

- every expected symbol is assigned to the correct group;
- candidate scores are close to the Python output when both systems use the same MT5 candles and analysis timestamp;
- entry bid/ask, drift, SL, and TP are valid;
- estimated SL loss is no more than the configured balance percentage;
- group close events select only positions opened by this EA.

After that, repeat on demo with execution enabled before considering any live account.
