import sys
from types import ModuleType

import polars as pl
import pytest

from ashare_quant.data.cne_adapter import CNEquityAdapter, CNEquityDataError


def install_fake_cne(monkeypatch, loader):
    query = ModuleType("cnequity.query")
    query.load = loader
    package = ModuleType("cnequity")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "cnequity", package)
    monkeypatch.setitem(sys.modules, "cnequity.query", query)


def bars(exact=True):
    return pl.DataFrame({
        "symbol": ["600000.SH"], "trade_date": ["2024-01-02"],
        "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
        "volume": [1000], "amount": [10500.0], "adj_close": [8.5],
        "adj_is_exact": [exact],
    })


def test_daily_bars_keeps_raw_execution_prices_and_hfq(monkeypatch):
    calls = []
    def fake_load(dataset, **kwargs):
        calls.append((dataset, kwargs))
        return bars()
    install_fake_cne(monkeypatch, fake_load)
    result = CNEquityAdapter(data_root="/tmp/cne").daily_bars(
        ["600000.SH"], "2024-01-01", "2024-01-31"
    )
    assert result["instrument"].to_list() == ["600000.SH"]
    assert result["date"].to_list() == ["2024-01-02"]
    assert result["close"].item() == 10.5
    assert result["adj_close"].item() == 8.5
    assert calls[0][0] == "daily_bars"
    assert calls[0][1]["adjust"] == "hfq"
    assert calls[0][1]["strict_adj"] is True


def test_daily_bars_rejects_inexact_adjustment(monkeypatch):
    install_fake_cne(monkeypatch, lambda dataset, **kwargs: bars(False))
    adapter = CNEquityAdapter(data_root="/tmp/cne")
    with pytest.raises(CNEquityDataError, match="not exact"):
        adapter.daily_bars(None, "2024-01-01", "2024-01-31")


def test_universe_uses_official_research_profile(monkeypatch):
    calls = []
    def fake_load(dataset, **kwargs):
        calls.append((dataset, kwargs))
        return pl.DataFrame({
            "symbol": ["600000.SH", "000001.SZ", "600000.SH"],
            "trade_date": ["2024-01-02"] * 3,
        })
    install_fake_cne(monkeypatch, fake_load)
    result = CNEquityAdapter(data_root="/tmp/cne").point_in_time_universe("2024-01-02")
    assert result["instrument"].to_list() == ["000001.SZ", "600000.SH"]
    assert calls[0][1]["profile"] == "cn_a_sh_sz_research_v1"
    assert calls[0][1]["start"] == calls[0][1]["end"] == "2024-01-02"


def test_adapter_requires_exactly_one_lake_location():
    with pytest.raises(ValueError, match="required"):
        CNEquityAdapter()
    with pytest.raises(ValueError, match="not both"):
        CNEquityAdapter(config_path="cne.toml", data_root="/tmp/cne")
