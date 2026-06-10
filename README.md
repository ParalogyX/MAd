# Investment Adviser

`investment_adviser` is the first iteration of a Python investment analysis library. It provides clean provider interfaces for market data, technical indicators, candlestick pattern detection, and symbol sentiment analysis. It does not place trades, log in to brokers, or produce financial advice.

## What It Does

- Discovers Libertex instruments using best-effort public Libertex pages.
- Loads normalized OHLCV market data through pluggable providers.
- Calculates technical indicators such as SMA, EMA, RSI, MACD, Bollinger Bands, ATR, Stochastic, and ADX.
- Detects deterministic candlestick patterns.
- Aggregates simple internet/news sentiment into a structured score from `-100` to `+100`.

## Installation

```bash
pip install -e .
```

Install optional live yfinance market data support:

```bash
pip install -e ".[providers]"
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
directory instead of staying inside the container.

On the Linux server, make sure the server has SSH access to GitHub for:

```text
git@github.com:ParalogyX/MAd.git
```

Then run the installer script:

```bash
bash install_server.sh
```

By default, it clones or updates the repository in `~/MAd`, builds the Docker
image, starts the `mad-signals` container, and saves CSV files in:

```text
~/MAd/data
```

Useful options:

```bash
APP_DIR=/opt/MAd DATA_DIR=/var/lib/mad-signals bash install_server.sh
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

Libertex does not appear to publish a stable official public historical market data or trading API for this use case. The Libertex provider therefore only implements instrument discovery as far as practical from public Libertex pages and a documented local snapshot. It deliberately raises `DataProviderError` for Libertex OHLCV data instead of inventing an unofficial broker feed.

The default `auto` market data flow tries Libertex first and then the real fallback provider chain. The fallback chain tries yfinance symbol aliases, Binance public crypto klines, and Stooq daily CSV data. The deterministic `mock` provider is only for tests and local development.

Not every Libertex CFD symbol has an exact public-market equivalent. For example, some broker-only CFDs, delisted stocks, very new meme coins, or regional instruments may still fail. The library raises `DataProviderError` for those cases instead of returning invented data.

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
