import polars as pl
import pytest

from ashare_quant.data.validation import validate_daily_bars


def valid_bars() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "instrument": ["600000.SH"],
            "date": ["2024-01-02"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [1000],
            "amount": [10500.0],
        }
    )


def test_valid_daily_bars_pass() -> None:
    validate_daily_bars(valid_bars())


def test_missing_column_fails() -> None:
    with pytest.raises(ValueError, match="Missing required"):
        validate_daily_bars(valid_bars().drop("amount"))


def test_invalid_ohlc_fails() -> None:
    # A low above the open/close is invalid; low=8 would be a valid bar.
    frame = valid_bars().with_columns(pl.lit(12.0).alias("low"))
    with pytest.raises(ValueError, match="OHLC"):
        validate_daily_bars(frame)


def test_duplicate_keys_fail() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        validate_daily_bars(pl.concat([valid_bars(), valid_bars()]))
