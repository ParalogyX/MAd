from datetime import datetime, timezone

import pandas as pd

from investment_adviser import (
    load_symbol_data,
    perform_candle_analysis,
    perform_symbol_sentiment_analysis,
    perform_technical_analysis,
)
from investment_adviser.analysis.sentiment import set_sentiment_source_provider
from investment_adviser.providers.fallback import MockSentimentSourceProvider


def teardown_function():
    set_sentiment_source_provider(None)


def test_integration_base():
    set_sentiment_source_provider(
        MockSentimentSourceProvider(["strong growth", "neutral market update"])
    )

    data = load_symbol_data(
        symbol="AAPL",
        timeframe="1d",
        begin_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_time=datetime(2024, 4, 1, tzinfo=timezone.utc),
        provider="mock",
    )
    technical = perform_technical_analysis(data)
    candles = perform_candle_analysis(data)
    sentiment = perform_symbol_sentiment_analysis("AAPL")

    assert isinstance(data, pd.DataFrame)
    assert not technical.empty
    assert {"timestamp", "candle_type", "direction", "confidence"} <= set(candles.columns)
    assert int(sentiment.loc[0, "source_count"]) == 2
    assert -100 <= int(sentiment.loc[0, "sentiment_score"]) <= 100
