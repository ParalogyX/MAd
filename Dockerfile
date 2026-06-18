FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    M_AD_OUTPUT_DIR=/data \
    MT5_HOST=127.0.0.1 \
    MT5_PORT=8001 \
    M_AD_AUTO_TRADE_ENABLED=true \
    M_AD_ALLOW_LIVE_TRADING=false \
    M_AD_TARGET_TRADE_NOTIONAL_EUR=1000 \
    M_AD_TEST_TRADE_NOTIONAL_EUR=50 \
    M_AD_TEST_TRADE_HOLD_SECONDS=60 \
    TZ=Europe/Amsterdam

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt README.md ./
COPY investment_adviser ./investment_adviser
COPY runtime_paths.py scheduler_config.py scheduler_logging.py ./
COPY execution_ledger.py mt5_execution.py ./
COPY ticker_classification_rules.py ./
COPY find_signal.py csv_analysis.py trade_signal_generator.py ./

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

RUN mkdir -p /data

VOLUME ["/data"]

CMD ["python", "trade_signal_generator.py"]
