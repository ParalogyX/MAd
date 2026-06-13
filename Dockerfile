FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    M_AD_OUTPUT_DIR=/data \
    MT5_HOST=127.0.0.1 \
    MT5_PORT=8001 \
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
COPY runtime_paths.py ticker_classification_rules.py ./
COPY find_signal.py csv_analysis.py trade_signal_generator.py ./

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

RUN mkdir -p /data

VOLUME ["/data"]

CMD ["python", "trade_signal_generator.py"]
