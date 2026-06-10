"""Custom exceptions raised by the investment adviser package."""


class InvestmentAdviserError(Exception):
    """Base class for all package-specific exceptions."""


class DataProviderError(InvestmentAdviserError):
    """Raised when an instrument or market data provider cannot return data."""


class SentimentProviderError(InvestmentAdviserError):
    """Raised when sentiment sources cannot be queried or scored safely."""


class ValidationError(InvestmentAdviserError):
    """Raised when structured input data does not match the expected schema."""
