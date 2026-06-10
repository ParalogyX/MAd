"""Public web/news sentiment collection and lexicon-based scoring."""

from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pandas as pd
import requests

from investment_adviser.config import ProviderSettings
from investment_adviser.exceptions import SentimentProviderError
from investment_adviser.models import SentimentDocument, SentimentResult
from investment_adviser.providers.base import SentimentSourceProvider
from investment_adviser.utils.logging import get_logger
from investment_adviser.utils.validation import normalize_symbol

LOGGER = get_logger(__name__)

_SENTIMENT_PROVIDER_OVERRIDE: SentimentSourceProvider | None = None

_POSITIVE_WORDS = {
    "advance",
    "beat",
    "beats",
    "bullish",
    "buy",
    "confidence",
    "exceed",
    "gain",
    "gains",
    "growth",
    "improve",
    "improves",
    "improved",
    "optimistic",
    "outperform",
    "positive",
    "profit",
    "profits",
    "rally",
    "rebound",
    "recover",
    "record",
    "strong",
    "surge",
    "upbeat",
    "upgrade",
    "upside",
}

_NEGATIVE_WORDS = {
    "bearish",
    "concern",
    "concerns",
    "crash",
    "cut",
    "decline",
    "default",
    "downgrade",
    "drop",
    "falls",
    "fear",
    "fraud",
    "lawsuit",
    "loss",
    "losses",
    "miss",
    "misses",
    "negative",
    "panic",
    "plunge",
    "recession",
    "risk",
    "sell",
    "slump",
    "weak",
    "warning",
}


class GoogleNewsRssProvider(SentimentSourceProvider):
    """Collect recent public news snippets from Google News RSS."""

    name = "google_news_rss"

    def __init__(self, settings: ProviderSettings | None = None, limit: int = 20):
        self.settings = settings or ProviderSettings()
        self.limit = limit

    def fetch_documents(self, symbol: str) -> list[SentimentDocument]:
        """Return public RSS items for a market symbol."""

        query = urllib.parse.quote_plus(f"{symbol} stock market")
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        response = requests.get(
            url,
            timeout=self.settings.timeout_seconds,
            headers={"User-Agent": self.settings.user_agent},
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        documents: list[SentimentDocument] = []
        for item in root.findall("./channel/item")[: self.limit]:
            title = item.findtext("title") or ""
            description = item.findtext("description") or ""
            link = item.findtext("link")
            if title.strip() or description.strip():
                documents.append(
                    SentimentDocument(
                        source=self.name,
                        title=title.strip(),
                        summary=_strip_html(description),
                        url=link,
                    )
                )
        return documents


class YahooFinanceSearchProvider(SentimentSourceProvider):
    """Collect public Yahoo Finance search/news snippets when available."""

    name = "yahoo_finance_search"

    def __init__(self, settings: ProviderSettings | None = None, limit: int = 10):
        self.settings = settings or ProviderSettings()
        self.limit = limit

    def fetch_documents(self, symbol: str) -> list[SentimentDocument]:
        """Return public Yahoo Finance news items for a market symbol."""

        query = urllib.parse.quote_plus(symbol)
        url = (
            "https://query1.finance.yahoo.com/v1/finance/search"
            f"?q={query}&newsCount={self.limit}"
        )
        response = requests.get(
            url,
            timeout=self.settings.timeout_seconds,
            headers={"User-Agent": self.settings.user_agent},
        )
        response.raise_for_status()
        payload = json.loads(response.text)
        documents: list[SentimentDocument] = []
        for item in payload.get("news", [])[: self.limit]:
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or item.get("publisher") or "").strip()
            link = item.get("link")
            if title or summary:
                documents.append(
                    SentimentDocument(
                        source=self.name,
                        title=title,
                        summary=summary,
                        url=str(link) if link else None,
                    )
                )
        return documents


class CompositeSentimentSourceProvider(SentimentSourceProvider):
    """Combine multiple public sentiment source providers."""

    name = "composite"

    def __init__(self, providers: list[SentimentSourceProvider]):
        self.providers = providers

    def fetch_documents(self, symbol: str) -> list[SentimentDocument]:
        """Return documents from all configured providers."""

        documents: list[SentimentDocument] = []
        for provider in self.providers:
            try:
                documents.extend(provider.fetch_documents(symbol))
            except (requests.RequestException, ET.ParseError, json.JSONDecodeError) as exc:
                LOGGER.warning(
                    "Sentiment source '%s' failed for %s: %s",
                    provider.name,
                    symbol,
                    exc,
                )
            except SentimentProviderError as exc:
                LOGGER.warning(
                    "Sentiment source '%s' failed for %s: %s",
                    provider.name,
                    symbol,
                    exc,
                )
        return documents


class LexiconSentimentScorer:
    """Score market text using a small deterministic financial lexicon."""

    def score_text(self, text: str) -> float:
        """Return a sentiment value from -1.0 to +1.0 for a text string."""

        tokens = re.findall(r"[a-zA-Z']+", text.lower())
        if not tokens:
            return 0.0
        positive = sum(1 for token in tokens if token in _POSITIVE_WORDS)
        negative = sum(1 for token in tokens if token in _NEGATIVE_WORDS)
        total = positive + negative
        if total == 0:
            return 0.0
        return (positive - negative) / total

    def aggregate(
        self,
        symbol: str,
        documents: list[SentimentDocument],
    ) -> SentimentResult:
        """Aggregate document-level sentiment into a result object."""

        if not documents:
            return SentimentResult(
                symbol=symbol,
                sentiment_score=0,
                confidence=0.0,
                source_count=0,
                positive_count=0,
                neutral_count=0,
                negative_count=0,
                timestamp=datetime.now(timezone.utc),
            )

        scores = [self.score_text(document.text) for document in documents]
        positive_count = sum(1 for score in scores if score > 0.05)
        negative_count = sum(1 for score in scores if score < -0.05)
        neutral_count = len(scores) - positive_count - negative_count
        average_score = sum(scores) / len(scores)
        sentiment_score = int(round(max(-1.0, min(1.0, average_score)) * 100.0))
        mean_strength = sum(abs(score) for score in scores) / len(scores)
        confidence = min(1.0, len(scores) / 10.0) * min(1.0, 0.35 + mean_strength)

        return SentimentResult(
            symbol=symbol,
            sentiment_score=sentiment_score,
            confidence=round(confidence, 4),
            source_count=len(scores),
            positive_count=positive_count,
            neutral_count=neutral_count,
            negative_count=negative_count,
            timestamp=datetime.now(timezone.utc),
        )


def set_sentiment_source_provider(
    provider: SentimentSourceProvider | None,
) -> None:
    """Override the default sentiment provider, mainly for tests."""

    global _SENTIMENT_PROVIDER_OVERRIDE
    _SENTIMENT_PROVIDER_OVERRIDE = provider


def perform_symbol_sentiment_analysis(symbol: str) -> pd.DataFrame:
    """Return aggregated public sentiment for a symbol as a one-row DataFrame.

    The final `sentiment_score` is scaled from -100 to +100. No sources are
    fabricated; if no documents are found, the score is neutral with zero
    confidence.
    """

    normalized_symbol = normalize_symbol(symbol)
    provider = _SENTIMENT_PROVIDER_OVERRIDE or _default_sentiment_provider()
    documents = provider.fetch_documents(normalized_symbol)
    result = LexiconSentimentScorer().aggregate(normalized_symbol, documents)
    return pd.DataFrame(
        [
            {
                "symbol": result.symbol,
                "sentiment_score": result.sentiment_score,
                "confidence": result.confidence,
                "source_count": result.source_count,
                "positive_count": result.positive_count,
                "neutral_count": result.neutral_count,
                "negative_count": result.negative_count,
                "timestamp": result.timestamp,
            }
        ]
    )


def _default_sentiment_provider() -> SentimentSourceProvider:
    return CompositeSentimentSourceProvider(
        providers=[GoogleNewsRssProvider(), YahooFinanceSearchProvider()]
    )


def _strip_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", without_tags).strip()
