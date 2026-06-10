"""Abstract provider interfaces for instruments, OHLCV data, and sentiment."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from investment_adviser.models import MarketDataRequest, SentimentDocument


class InstrumentProvider(ABC):
    """Interface for providers that discover tradable instruments."""

    @abstractmethod
    def find_instruments(self) -> list[str]:
        """Return normalized tradable instrument symbols."""


class MarketDataProvider(ABC):
    """Interface for providers that load historical OHLCV data."""

    name: str

    @abstractmethod
    def load_data(self, request: MarketDataRequest) -> pd.DataFrame:
        """Return OHLCV candles for a normalized market data request."""


class SentimentSourceProvider(ABC):
    """Interface for providers that collect public text for sentiment analysis."""

    name: str

    @abstractmethod
    def fetch_documents(self, symbol: str) -> list[SentimentDocument]:
        """Return public documents relevant to a symbol."""
