from investment_adviser import perform_symbol_sentiment_analysis
from investment_adviser.analysis.sentiment import set_sentiment_source_provider
from investment_adviser.providers.fallback import MockSentimentSourceProvider


def teardown_function():
    set_sentiment_source_provider(None)


def test_perform_symbol_sentiment_analysis_positive_text():
    set_sentiment_source_provider(
        MockSentimentSourceProvider(
            [
                "strong profit growth beat expectations and bullish upgrade",
                "optimistic rally with gains and upside",
            ]
        )
    )

    result = perform_symbol_sentiment_analysis("AAPL")
    score = int(result.loc[0, "sentiment_score"])

    assert -100 <= score <= 100
    assert score > 0
    assert int(result.loc[0, "source_count"]) == 2


def test_perform_symbol_sentiment_analysis_negative_text():
    set_sentiment_source_provider(
        MockSentimentSourceProvider(
            [
                "panic sell after weak warning and losses",
                "bearish downgrade as fraud concerns trigger a crash",
            ]
        )
    )

    result = perform_symbol_sentiment_analysis("AAPL")
    score = int(result.loc[0, "sentiment_score"])

    assert -100 <= score <= 100
    assert score < 0
    assert int(result.loc[0, "negative_count"]) == 2


def test_perform_symbol_sentiment_analysis_neutral_no_data():
    set_sentiment_source_provider(MockSentimentSourceProvider([]))

    result = perform_symbol_sentiment_analysis("AAPL")

    assert int(result.loc[0, "sentiment_score"]) == 0
    assert float(result.loc[0, "confidence"]) == 0.0
    assert int(result.loc[0, "source_count"]) == 0
