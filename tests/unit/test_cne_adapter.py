import sys
from types import ModuleType, SimpleNamespace

import polars as pl
import pytest

from ashare_quant.data.cne_adapter import CNEquityAdapter, CNEquityDataError


def install_fake_cne(monkeypatch, loader):
    query = ModuleType("cnequity.query")
    def load_with_receipt(dataset, **kwargs):
        result = loader(dataset, **kwargs)
        if isinstance(result, tuple):
            frame, receipt = result
        else:
            frame, receipt = result, {
                "replayable": True,
                "receipt_id": "test-receipt",
                "dependencies": {
                    dataset: {
                        "revision_id": "test-revision",
                        "contract_fingerprint": "test-contract",
                    }
                },
            }
        return SimpleNamespace(frame=frame, receipt=receipt)
    query.load_with_receipt = load_with_receipt
    package = ModuleType("cnequity")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "cnequity", package)
    monkeypatch.setitem(sys.modules, "cnequity.query", query)


def bars(exact=True):
    return pl.DataFrame({
        "symbol": ["600000.SH"], "trade_date": ["2024-01-02"],
        "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
        "volume": [1000], "amount": [10500.0], "adj_open": [8.0],
        "adj_high": [8.8], "adj_low": [7.2], "adj_close": [8.4],
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
    assert result["adj_close"].item() == 8.4
    assert calls[0][0] == "daily_bars"
    assert calls[0][1]["adjust"] == "hfq"
    assert calls[0][1]["strict_adj"] is True
    assert calls[0][1]["strict_universe"] is True


def test_daily_bars_rejects_inexact_adjustment(monkeypatch):
    install_fake_cne(monkeypatch, lambda dataset, **kwargs: bars(False))
    adapter = CNEquityAdapter(data_root="/tmp/cne")
    with pytest.raises(CNEquityDataError, match="not exact"):
        adapter.daily_bars(None, "2024-01-01", "2024-01-31")


def test_daily_bars_rejects_missing_adjustment_evidence(monkeypatch):
    frame = bars().drop("adj_is_exact")
    install_fake_cne(monkeypatch, lambda dataset, **kwargs: frame)
    with pytest.raises(CNEquityDataError, match="evidence columns missing"):
        CNEquityAdapter(data_root="/tmp/cne").daily_bars(
            None, "2024-01-01", "2024-01-31"
        )


def test_universe_uses_strict_official_research_profile(monkeypatch):
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
    assert calls[0][1]["strict_universe"] is True
    assert calls[0][1]["start"] == calls[0][1]["end"] == "2024-01-02"


def test_trading_status_rejects_null_risk_warning(monkeypatch):
    frame = pl.DataFrame({
        "symbol": ["600000.SH"], "trade_date": ["2024-01-02"],
        "is_trading": [True], "risk_warning": [None], "status": ["normal"],
    })
    install_fake_cne(monkeypatch, lambda dataset, **kwargs: frame)
    with pytest.raises(CNEquityDataError, match="unknown/null"):
        CNEquityAdapter(data_root="/tmp/cne").trading_status(
            ["600000.SH"], "2024-01-01", "2024-01-31"
        )


def test_pit_requires_nonempty_exact_evidence_and_announce_date(monkeypatch):
    frame = pl.DataFrame({
        "symbol": ["600000.SH"], "report_period": ["2023Q4"],
        "item_code": ["roe"], "item_value": [10.0],
        "announce_date": ["2024-03-20"], "pit_is_exact": [True],
        "pit_quality": ["strict"],
    })
    calls = []
    def fake_load(dataset, **kwargs):
        calls.append((dataset, kwargs))
        return frame
    install_fake_cne(monkeypatch, fake_load)
    result = CNEquityAdapter(data_root="/tmp/cne").financial_statement_items(
        ["roe"], "2024-04-01"
    )
    assert result.height == 1
    assert calls[0][1]["pit_mode"] == "strict"
    assert calls[0][1]["as_of"] == "2024-04-01"


@pytest.mark.parametrize("frame", [
    pl.DataFrame({"symbol": ["600000.SH"]}),
    pl.DataFrame({
        "symbol": ["600000.SH"], "announce_date": ["2024-03-20"],
        "pit_is_exact": [False], "pit_quality": ["reconstructed"],
    }),
])
def test_pit_rejects_missing_or_non_exact_evidence(monkeypatch, frame):
    install_fake_cne(monkeypatch, lambda dataset, **kwargs: frame)
    with pytest.raises(CNEquityDataError):
        CNEquityAdapter(data_root="/tmp/cne").financial_statement_items(
            ["roe"], "2024-04-01"
        )


def test_pit_rejects_empty_strict_result(monkeypatch):
    install_fake_cne(monkeypatch, lambda dataset, **kwargs: pl.DataFrame())
    with pytest.raises(CNEquityDataError, match="coverage failure"):
        CNEquityAdapter(data_root="/tmp/cne").financial_statement_items(
            ["roe"], "2024-04-01"
        )


def test_adapter_requires_exactly_one_lake_location():
    with pytest.raises(ValueError, match="required"):
        CNEquityAdapter()
    with pytest.raises(ValueError, match="not both"):
        CNEquityAdapter(config_path="cne.toml", data_root="/tmp/cne")



def test_adapter_collects_replayable_receipts_for_each_read(monkeypatch):
    calls = []
    def fake_load(dataset, **kwargs):
        calls.append((dataset, kwargs))
        return bars()
    install_fake_cne(monkeypatch, fake_load)
    adapter = CNEquityAdapter(data_root="/tmp/cne")
    adapter.daily_bars(["600000.SH"], "2024-01-01", "2024-01-31")
    receipts = adapter.source_receipts
    assert len(receipts) == 1
    assert receipts[0]["dataset"] == "daily_bars"
    assert receipts[0]["receipt"]["replayable"] is True
    assert calls[0][1]["require_replayable"] is True


def test_adapter_rejects_non_replayable_receipt(monkeypatch):
    install_fake_cne(
        monkeypatch,
        lambda dataset, **kwargs: (bars(), {"replayable": False}),
    )
    adapter = CNEquityAdapter(data_root="/tmp/cne")
    with pytest.raises(CNEquityDataError, match="replayable"):
        adapter.daily_bars(None, "2024-01-01", "2024-01-31")



def test_adapter_pins_dependency_revisions_across_reads(monkeypatch):
    calls = []

    def fake_load(dataset, **kwargs):
        calls.append((dataset, kwargs))
        return bars()

    install_fake_cne(monkeypatch, fake_load)
    adapter = CNEquityAdapter(data_root="/tmp/cne")
    adapter.daily_bars(["600000.SH"], "2024-01-01", "2024-01-31")
    adapter.daily_bars(["600000.SH"], "2024-02-01", "2024-02-29")

    assert calls[0][1].get("revision_map") is None
    assert calls[1][1]["revision_map"] == {"daily_bars": "test-revision"}
    assert adapter.source_revision_map == {"daily_bars": "test-revision"}
    assert adapter.source_contract_fingerprints == {"daily_bars": "test-contract"}


def test_adapter_rejects_dependency_revision_drift(monkeypatch):
    calls = 0

    def fake_load(dataset, **kwargs):
        nonlocal calls
        calls += 1
        revision = "revision-a" if calls == 1 else "revision-b"
        return bars(), {
            "replayable": True,
            "receipt_id": f"receipt-{calls}",
            "dependencies": {
                dataset: {
                    "revision_id": revision,
                    "contract_fingerprint": "test-contract",
                }
            },
        }

    install_fake_cne(monkeypatch, fake_load)
    adapter = CNEquityAdapter(data_root="/tmp/cne")
    adapter.daily_bars(["600000.SH"], "2024-01-01", "2024-01-31")
    with pytest.raises(CNEquityDataError, match="revision drift"):
        adapter.daily_bars(["600000.SH"], "2024-02-01", "2024-02-29")


def test_adapter_rejects_receipt_without_dependency_identity(monkeypatch):
    install_fake_cne(
        monkeypatch,
        lambda dataset, **kwargs: (
            bars(),
            {"replayable": True, "receipt_id": "missing-deps"},
        ),
    )
    adapter = CNEquityAdapter(data_root="/tmp/cne")
    with pytest.raises(CNEquityDataError, match="dependency identities"):
        adapter.daily_bars(None, "2024-01-01", "2024-01-31")
