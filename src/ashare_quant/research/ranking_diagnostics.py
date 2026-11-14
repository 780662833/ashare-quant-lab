"""Post-hoc cross-sectional ranking diagnostics for matured validation labels.

The diagnostic model is fitted on the training partition and predicts the
validation partition without labels. Only after validation labels have matured
strictly before the next test cutoff are those labels joined for evaluation.
No test-period labels are accepted by this module.
"""
from __future__ import annotations

from datetime import date
from math import sqrt
from statistics import mean, median
from typing import Any

import polars as pl

_PREDICTION_COLUMNS = {"signal_date", "instrument", "predicted_return"}
_LABEL_COLUMNS = {"signal_date", "instrument", "label_end_date", "forward_return"}
_DIAGNOSTIC_SCHEMA = {
    "signal_date": pl.String,
    "sample_count": pl.UInt32,
    "spearman_rank_ic": pl.Float64,
    "pearson_ic": pl.Float64,
    "top_n_count": pl.UInt32,
    "top_n_mean_forward_return": pl.Float64,
    "universe_mean_forward_return": pl.Float64,
    "top_n_excess_forward_return": pl.Float64,
    "diagnostic_bucket_count": pl.UInt32,
    "top_bucket_mean_forward_return": pl.Float64,
    "bottom_bucket_mean_forward_return": pl.Float64,
    "top_minus_bottom_forward_return": pl.Float64,
    "top_n_positive_ratio": pl.Float64,
}


def _iso_date(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO YYYY-MM-DD string") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use canonical YYYY-MM-DD")
    return value


def _rankdata(values: list[float]) -> list[float]:
    """Return one-based average ranks, preserving ties."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    offset = 0
    while offset < len(order):
        end = offset + 1
        value = values[order[offset]]
        while end < len(order) and values[order[end]] == value:
            end += 1
        average_rank = ((offset + 1) + end) / 2.0
        for position in range(offset, end):
            ranks[order[position]] = average_rank
        offset = end
    return ranks


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    left_delta = [value - left_mean for value in left]
    right_delta = [value - right_mean for value in right]
    left_sum = sum(value * value for value in left_delta)
    right_sum = sum(value * value for value in right_delta)
    if left_sum <= 0 or right_sum <= 0:
        return None
    covariance = sum(a * b for a, b in zip(left_delta, right_delta, strict=True))
    return covariance / sqrt(left_sum * right_sum)


def _mean_optional(values: list[float | None]) -> float | None:
    observed = [value for value in values if value is not None]
    return mean(observed) if observed else None


def evaluate_validation_ranking(
    predictions: pl.DataFrame,
    labels: pl.DataFrame,
    *,
    top_n: int,
    validation_start: str,
    validation_end: str,
    labels_mature_before: str,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Measure validation ranking only after all supplied labels are mature.

    Predictions must be produced from feature-only validation rows. The caller
    supplies labels from that same validation date window; every label must
    mature strictly before labels_mature_before (normally the fold test start).
    Top-N return is compared with the full labeled cross-section. A separate
    equal-size top/bottom bucket spread is reported without overlapping buckets
    when a synthetic/small universe has fewer than 2 * top_n names.
    """
    if not isinstance(predictions, pl.DataFrame) or not isinstance(labels, pl.DataFrame):
        raise TypeError("predictions and labels must be Polars DataFrames")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a positive integer")
    start = _iso_date(validation_start, "validation_start")
    end = _iso_date(validation_end, "validation_end")
    cutoff = _iso_date(labels_mature_before, "labels_mature_before")
    if start > end or end >= cutoff:
        raise ValueError("validation window must end before the label maturity cutoff")

    missing_predictions = _PREDICTION_COLUMNS.difference(predictions.columns)
    missing_labels = _LABEL_COLUMNS.difference(labels.columns)
    if missing_predictions:
        raise ValueError(f"predictions missing columns: {sorted(missing_predictions)}")
    if missing_labels:
        raise ValueError(f"labels missing columns: {sorted(missing_labels)}")

    pred = predictions.select(
        pl.col("signal_date").cast(pl.String),
        pl.col("instrument").cast(pl.String),
        pl.col("predicted_return").cast(pl.Float64, strict=True),
    )
    actual = labels.select(
        pl.col("signal_date").cast(pl.String),
        pl.col("instrument").cast(pl.String),
        pl.col("label_end_date").cast(pl.String),
        pl.col("forward_return").cast(pl.Float64, strict=True),
    )
    for frame, name in ((pred, "predictions"), (actual, "labels")):
        if frame.select(
            pl.any_horizontal(
                pl.col("signal_date").is_null(),
                pl.col("instrument").is_null(),
                (pl.col("signal_date").str.strip_chars() == ""),
                (pl.col("instrument").str.strip_chars() == ""),
            ).any()
        ).item():
            raise ValueError(f"{name} contain null or empty keys")
        if frame.select(pl.struct(["signal_date", "instrument"]).is_duplicated().any()).item():
            raise ValueError(f"{name} contain duplicate signal_date/instrument keys")
        for signal_date in frame["signal_date"].to_list():
            _iso_date(signal_date, f"{name}.signal_date")
            if not start <= signal_date <= end:
                raise ValueError(f"{name} contain a signal date outside the validation window")

    if pred.filter(
        pl.col("predicted_return").is_null()
        | (~pl.col("predicted_return").is_finite())
    ).height:
        raise ValueError("predictions contain null or non-finite predicted_return")
    if actual.filter(
        pl.col("label_end_date").is_null()
        | pl.col("forward_return").is_null()
        | (~pl.col("forward_return").is_finite())
    ).height:
        raise ValueError("labels contain null or non-finite maturity/forward_return")
    if actual.filter(
        (pl.col("label_end_date") <= pl.col("signal_date"))
        | (pl.col("label_end_date") >= cutoff)
    ).height:
        raise ValueError("validation labels must mature strictly before the test cutoff")

    matched = pred.join(actual, on=["signal_date", "instrument"], how="inner", validate="1:1")
    rows: list[dict[str, Any]] = []
    if not matched.is_empty():
        for signal_date in sorted(matched["signal_date"].unique().to_list()):
            cross_section = matched.filter(pl.col("signal_date") == signal_date)
            ordered = sorted(
                cross_section.to_dicts(),
                key=lambda row: (-float(row["predicted_return"]), str(row["instrument"])),
            )
            prediction_values = [float(row["predicted_return"]) for row in ordered]
            forward_returns = [float(row["forward_return"]) for row in ordered]
            count = len(ordered)
            top_rows = ordered[:top_n]
            top_returns = [float(row["forward_return"]) for row in top_rows]
            top_mean = mean(top_returns)
            universe_mean = mean(forward_returns)
            bucket_count = min(top_n, count // 2)
            if bucket_count:
                top_bucket = forward_returns[:bucket_count]
                bottom_bucket = forward_returns[-bucket_count:]
                top_bucket_mean = mean(top_bucket)
                bottom_bucket_mean = mean(bottom_bucket)
                spread = top_bucket_mean - bottom_bucket_mean
            else:
                top_bucket_mean = None
                bottom_bucket_mean = None
                spread = None

            rows.append(
                {
                    "signal_date": signal_date,
                    "sample_count": count,
                    "spearman_rank_ic": _correlation(
                        _rankdata(prediction_values),
                        _rankdata(forward_returns),
                    ),
                    "pearson_ic": _correlation(prediction_values, forward_returns),
                    "top_n_count": len(top_rows),
                    "top_n_mean_forward_return": top_mean,
                    "universe_mean_forward_return": universe_mean,
                    "top_n_excess_forward_return": top_mean - universe_mean,
                    "diagnostic_bucket_count": bucket_count,
                    "top_bucket_mean_forward_return": top_bucket_mean,
                    "bottom_bucket_mean_forward_return": bottom_bucket_mean,
                    "top_minus_bottom_forward_return": spread,
                    "top_n_positive_ratio": (
                        sum(value > 0 for value in top_returns) / len(top_returns)
                        if top_returns else None
                    ),
                }
            )

    diagnostics = pl.DataFrame(rows, schema=_DIAGNOSTIC_SCHEMA)
    ic_values = [row["spearman_rank_ic"] for row in rows]
    pearson_values = [row["pearson_ic"] for row in rows]
    spread_values = [row["top_minus_bottom_forward_return"] for row in rows]
    top_excess_values = [row["top_n_excess_forward_return"] for row in rows]
    top_return_values = [row["top_n_mean_forward_return"] for row in rows]
    valid_ic = [value for value in ic_values if value is not None]
    positive_ic = [value for value in valid_ic if value > 0]
    positive_spreads = [value for value in spread_values if value is not None]
    prediction_rows = pred.height
    matched_rows = matched.height
    report: dict[str, Any] = {
        "status": "evaluated" if matched_rows else "no_matched_validation_labels",
        "validation_period": {"start": start, "end": end},
        "labels_mature_before": cutoff,
        "prediction_rows": prediction_rows,
        "matched_labeled_rows": matched_rows,
        "label_coverage_rate": matched_rows / prediction_rows if prediction_rows else 0.0,
        "evaluated_signal_date_count": len(rows),
        "rank_ic_signal_date_count": len(valid_ic),
        "positive_rank_ic_signal_date_count": len(positive_ic),
        "mean_spearman_rank_ic": _mean_optional(ic_values),
        "median_spearman_rank_ic": median(valid_ic) if valid_ic else None,
        "positive_spearman_rank_ic_ratio": (
            len(positive_ic) / len(valid_ic)
            if valid_ic else None
        ),
        "mean_pearson_ic": _mean_optional(pearson_values),
        "mean_top_n_forward_return": _mean_optional(top_return_values),
        "mean_top_n_excess_forward_return": _mean_optional(top_excess_values),
        "mean_top_minus_bottom_forward_return": _mean_optional(spread_values),
        "positive_top_minus_bottom_ratio": (
            sum(value > 0 for value in positive_spreads) / len(positive_spreads)
            if positive_spreads else None
        ),
        "interpretation": (
            "Post-hoc validation-only ranking diagnostics. Labels are joined only "
            "after maturity strictly before test_start; test labels are not used, "
            "and these metrics do not tune or select model parameters."
        ),
    }
    return diagnostics, report
