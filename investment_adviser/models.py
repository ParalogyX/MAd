"""Typed data models used across providers and analysis modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MarketDataRequest:
    """A normalized request for historical OHLCV market data."""

    symbol: str
    timeframe: str
    begin_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class SentimentDocument:
    """A text document collected from a public sentiment source."""

    source: str
    title: str
    summary: str = ""
    url: str | None = None
    published_at: datetime | None = None

    @property
    def text(self) -> str:
        """Return the combined text used for sentiment scoring."""

        return f"{self.title} {self.summary}".strip()


@dataclass(frozen=True)
class SentimentResult:
    """Aggregated sentiment result for one symbol."""

    symbol: str
    sentiment_score: int
    confidence: float
    source_count: int
    positive_count: int
    neutral_count: int
    negative_count: int
    timestamp: datetime
