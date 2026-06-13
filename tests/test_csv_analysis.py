import pandas as pd

import csv_analysis
from csv_analysis import enrich_with_current_prices, get_current_price


def test_get_current_price_prefers_live_mt5_tick(monkeypatch):
    class FakeLivePriceProvider:
        def get_current_price(self, ticker, side=None):
            assert ticker == "EURUSD"
            assert side == "buy"
            return 1.23456

    def fail_price_loader(*args, **kwargs):
        raise AssertionError("candle fallback should not be used")

    monkeypatch.setattr(csv_analysis, "_LIVE_PRICE_PROVIDER", FakeLivePriceProvider())

    assert (
        get_current_price("EURUSD", price_loader=fail_price_loader, side="buy")
        == 1.23456
    )


def test_enrich_with_current_prices_refreshes_existing_stale_price(monkeypatch):
    calls = []

    class FakeLivePriceProvider:
        def get_current_price(self, ticker, side=None):
            calls.append((ticker, side))
            return 1.23456

    monkeypatch.setattr(csv_analysis, "_LIVE_PRICE_PROVIDER", FakeLivePriceProvider())
    signals = pd.DataFrame(
        [
            {
                "ticker": "EURUSD",
                "direction": "buy",
                "current_price": 1.0,
            }
        ]
    )

    enriched = enrich_with_current_prices(signals)

    assert enriched.loc[0, "current_price"] == 1.23456
    assert calls == [("EURUSD", "buy")]
