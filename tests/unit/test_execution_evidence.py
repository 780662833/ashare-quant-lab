import polars as pl
import pytest

from ashare_quant.data.execution_evidence import (
    combine_execution_evidence,
    normalize_cnequity_trading_status,
)
from ashare_quant.research import ResearchBundleError


def make_status():
    return pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ", "600519.SH", "000002.SZ"],
        "trade_date": ["2024-01-02"] * 4,
        "is_trading": [True, False, False, True],
        "status": ["normal", "suspended", "delisted", "st"],
        "risk_warning": [True, True, None, None],
        "source": ["exchange"] * 4,
        "data_version": ["v1"] * 4,
    })


def test_normalizer_preserves_orthogonal_st_and_trading_state():
    normalized = normalize_cnequity_trading_status(make_status()).sort("instrument")
    assert normalized["instrument"].to_list() == [
        "000001.SZ", "000002.SZ", "600000.SH", "600519.SH"
    ]
    assert normalized["is_suspended"].to_list() == [True, False, False, True]
    assert normalized["risk_warning"].to_list() == [True, True, True, None]
    assert normalized["risk_warning_known"].to_list() == [True, True, True, False]


def test_null_risk_warning_is_unknown_not_false():
    normalized = normalize_cnequity_trading_status(make_status())
    delisted = normalized.filter(pl.col("instrument") == "600519.SH")
    assert delisted["risk_warning"].item() is None
    assert delisted["risk_warning_known"].item() is False


def test_normalizer_rejects_conflicting_or_unknown_status():
    rows = make_status().with_columns(
        pl.when(pl.col("symbol") == "000001.SZ")
        .then(True)
        .otherwise(pl.col("is_trading"))
        .alias("is_trading")
    )
    with pytest.raises(ResearchBundleError, match="contradict"):
        normalize_cnequity_trading_status(rows)

    rows = make_status().with_columns(
        pl.when(pl.col("symbol") == "600000.SH")
        .then("unknown")
        .otherwise(pl.col("status"))
        .alias("status")
    )
    with pytest.raises(ResearchBundleError, match="unsupported"):
        normalize_cnequity_trading_status(rows)


def make_limit_evidence():
    return pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ"],
        "trade_date": ["2024-01-02", "2024-01-02"],
        "is_limit_up_locked": [False, True],
        "is_limit_down_locked": [False, False],
        "limit_up": [11.0, 22.0],
        "limit_down": [9.0, 18.0],
        "source": ["synthetic-exchange-rules"] * 2,
        "rule_version": ["rules-v1"] * 2,
    })


def test_combine_execution_evidence_requires_separate_limit_facts():
    native = normalize_cnequity_trading_status(make_status())
    raw = pl.DataFrame({
        "instrument": ["600000.SH", "000001.SZ"],
        "date": ["2024-01-02", "2024-01-02"],
    })
    combined = combine_execution_evidence(raw, native, make_limit_evidence())
    assert combined.columns == [
        "instrument", "date", "is_suspended", "is_limit_up_locked",
        "is_limit_down_locked", "limit_up", "limit_down", "risk_warning_known",
        "limit_evidence_source", "limit_rule_version",
    ]
    assert combined.filter(pl.col("symbol") == "000001.SZ")["is_limit_up_locked"].item()


def test_combine_execution_evidence_fails_closed_on_missing_limit_state():
    native = normalize_cnequity_trading_status(make_status())
    raw = pl.DataFrame({
        "instrument": ["600000.SH", "000001.SZ"],
        "date": ["2024-01-02", "2024-01-02"],
    })
    limits = make_limit_evidence().slice(1)
    with pytest.raises(ResearchBundleError, match="Price-limit evidence missing"):
        combine_execution_evidence(raw, native, limits)


def test_combine_execution_evidence_rejects_lock_without_limit_price():
    native = normalize_cnequity_trading_status(make_status())
    raw = pl.DataFrame({"instrument": ["600000.SH"], "date": ["2024-01-02"]})
    limits = make_limit_evidence().head(1).with_columns(
        pl.lit(True).alias("is_limit_up_locked"),
        pl.lit(None, dtype=pl.Float64).alias("limit_up"),
    )
    with pytest.raises(ResearchBundleError, match="lacks a limit-up price"):
        combine_execution_evidence(raw, native, limits)
