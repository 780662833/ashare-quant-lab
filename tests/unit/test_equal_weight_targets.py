import polars as pl
import pytest

from ashare_quant.backtest.engine import equal_weight_targets


def test_equal_weight_targets_selects_top_n_with_stable_ties():
    scores = pl.DataFrame({
        "signal_date": ["2024-01-05", "2024-01-05", "2024-01-05"],
        "instrument": ["600000.SH", "000001.SZ", "600519.SH"],
        "score": [0.2, 0.2, 0.1],
    })

    result = equal_weight_targets(scores, top_n=2)

    assert result["instrument"].to_list() == ["000001.SZ", "600000.SH"]
    assert result["weight"].to_list() == [0.5, 0.5]


def test_equal_weight_targets_returns_typed_empty_result():
    scores = pl.DataFrame(schema={
        "signal_date": pl.String,
        "instrument": pl.String,
        "score": pl.Float64,
    })

    result = equal_weight_targets(scores)

    assert result.is_empty()
    assert result.schema == {
        "signal_date": pl.String,
        "instrument": pl.String,
        "weight": pl.Float64,
    }


@pytest.mark.parametrize("top_n", [True, False, 0, -1, 1.5, "2"])
def test_equal_weight_targets_requires_positive_integer_top_n(top_n):
    scores = pl.DataFrame({
        "signal_date": ["2024-01-05"],
        "instrument": ["600000.SH"],
        "score": [0.1],
    })

    with pytest.raises(ValueError, match="positive integer"):
        equal_weight_targets(scores, top_n=top_n)


@pytest.mark.parametrize("bad_score", [float("nan"), float("inf"), float("-inf")])
def test_equal_weight_targets_rejects_non_finite_scores(bad_score):
    scores = pl.DataFrame({
        "signal_date": ["2024-01-05"],
        "instrument": ["600000.SH"],
        "score": [bad_score],
    })

    with pytest.raises(ValueError, match="finite"):
        equal_weight_targets(scores)


def test_equal_weight_targets_rejects_null_scores():
    scores = pl.DataFrame({
        "signal_date": ["2024-01-05"],
        "instrument": ["600000.SH"],
        "score": [None],
    }, schema={
        "signal_date": pl.String,
        "instrument": pl.String,
        "score": pl.Float64,
    })

    with pytest.raises(ValueError, match="null"):
        equal_weight_targets(scores)


def test_equal_weight_targets_rejects_boolean_or_non_numeric_scores():
    boolean_scores = pl.DataFrame({
        "signal_date": ["2024-01-05"],
        "instrument": ["600000.SH"],
        "score": [True],
    })
    text_scores = pl.DataFrame({
        "signal_date": ["2024-01-05"],
        "instrument": ["600000.SH"],
        "score": ["high"],
    })

    with pytest.raises(ValueError, match="boolean"):
        equal_weight_targets(boolean_scores)
    with pytest.raises(ValueError, match="numeric"):
        equal_weight_targets(text_scores)


@pytest.mark.parametrize(
    "signal_date,instrument",
    [("", "600000.SH"), ("2024-01-05", ""), (None, "600000.SH"), ("2024-01-05", None)],
)
def test_equal_weight_targets_rejects_empty_or_null_keys(signal_date, instrument):
    scores = pl.DataFrame({
        "signal_date": [signal_date],
        "instrument": [instrument],
        "score": [0.1],
    }, schema={
        "signal_date": pl.String,
        "instrument": pl.String,
        "score": pl.Float64,
    })

    with pytest.raises(ValueError, match="empty keys or null"):
        equal_weight_targets(scores)


def test_equal_weight_targets_rejects_duplicate_signal_instrument_scores():
    scores = pl.DataFrame({
        "signal_date": ["2024-01-05", "2024-01-05"],
        "instrument": ["600000.SH", "600000.SH"],
        "score": [0.1, 0.2],
    })

    with pytest.raises(ValueError, match="Duplicate"):
        equal_weight_targets(scores)
