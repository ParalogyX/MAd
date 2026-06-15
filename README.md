# Investment Adviser

`investment_adviser` is the first iteration of a Python investment analysis library. It provides clean provider interfaces for market data, technical indicators, candlestick pattern detection, and symbol sentiment analysis. It does not place trades, log in to brokers, or produce financial advice.

## What It Does

- Discovers tradable instruments from a configured MetaTrader 5 account.
- Loads normalized OHLCV market data from MetaTrader 5.
- Calculates technical indicators such as SMA, EMA, RSI, MACD, Bollinger Bands, ATR, Stochastic, and ADX.
- Detects deterministic candlestick patterns.
- Aggregates simple internet/news sentiment into a structured score from `-100` to `+100`.

## Installation

```bash
pip install -e .
```

Install test tooling:

```bash
pip install -e ".[test]"
```

## Running Tests

```bash
pytest
```

Live internet-dependent behavior is kept outside deterministic tests. Tests use the `mock` provider for market data and mock sentiment sources.

## Docker Deployment

The repository includes a `Dockerfile`, `docker-compose.yml`, and
`install_server.sh` for Linux server deployment. The container runs
`trade_signal_generator.py`; generated CSV files are written to a host-mounted
directory instead of staying inside the container. The container connects to
the MT5 bridge using the editable `mt5` section in `session_rules.json`.

On the Linux server, make sure the server has SSH access to GitHub for:

```text
git@github.com:ParalogyX/MAd.git
```

Then run the installer script:

```bash
bash install_server.sh
```

By default, it clones or updates the repository into a `repo` subdirectory of
the directory where you ran the installer, builds the Docker image, starts the
`mad-signals` container, and saves runtime files beside the installer:

```text
./logs
./Best signals
./Trade plans
./Results
./session_rules.json
```

Useful options:

```bash
INSTALL_DIR=/opt/MAd DATA_DIR=/var/lib/mad-signals bash install_server.sh
```

The Docker compose file uses Linux host networking, so a bridge running on the
same server can be reached as `127.0.0.1`. The installer seeds this default
through environment variables; after first startup you can edit it in:

```text
session_rules.json
```

Useful commands after deployment:

```bash
cd ~/MAd
docker compose logs -f mad-signals
docker attach mad-signals
```

In attached console mode, type `now` to run immediately or `stop` to stop the
scheduler. Detach from the container without stopping it with `Ctrl-p` then
`Ctrl-q`. If you stop it from the console, start it again with
`docker compose up -d`.

## Basic Usage

```python
from datetime import datetime, timezone
from investment_adviser import (
    find_libertex_instruments,
    load_symbol_data,
    perform_technical_analysis,
    perform_candle_analysis,
    perform_symbol_sentiment_analysis,
)

symbols = find_libertex_instruments()

data = load_symbol_data(
    symbol="AAPL",
    timeframe="1d",
    begin_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end_time=datetime(2024, 12, 31, tzinfo=timezone.utc),
)

indicators = perform_technical_analysis(data)
candles = perform_candle_analysis(data)
sentiment = perform_symbol_sentiment_analysis("AAPL")

print(indicators)
print(candles)
print(sentiment)
```

For deterministic local development:

```python
from datetime import datetime, timezone
from investment_adviser import load_symbol_data

data = load_symbol_data(
    symbol="AAPL",
    timeframe="1d",
    begin_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end_time=datetime(2024, 3, 1, tzinfo=timezone.utc),
    provider="mock",
)
```

## Data Provider Limitations

The active market data source is MetaTrader 5 through the `mt5linux` bridge.
For backward compatibility, public function names such as
`find_libertex_instruments()` and provider values such as `auto`, `fallback`,
and `libertex` are still accepted, but they route to MT5 internally.

The MT5 bridge must be reachable from the machine or Docker container running
the library. In Docker deployment on Linux, the default is:

```text
session_rules.json -> mt5.host = 127.0.0.1
session_rules.json -> mt5.port = 8001
```

The deterministic `mock` provider remains available for tests only.

## Sentiment Limitations

The default sentiment pipeline uses public no-key sources when possible and a simple lexicon-based scorer. Network failures are handled cleanly, and no sources are fabricated. A result with no documents returns a neutral score with low confidence.

## Not Financial Advice

This library calculates structured data, indicators, detected candle patterns, and sentiment scores. It does not recommend buying, selling, holding, or taking any financial action.

## Future Architecture

The package is intentionally split into independent modules:

- `providers/` for instrument, market data, and sentiment source access.
- `analysis/technical.py` for indicator calculations.
- `analysis/candles.py` for candlestick detection.
- `analysis/sentiment.py` for scoring text documents.

This keeps the base ready for future modules such as `strategy/`, `backtesting/`, `portfolio/`, `risk/`, and `reporting/` without mixing analysis logic with broker or network code.
