"""Validation-only PIT missingness and prediction-coverage diagnostics.

The module is descriptive: it compares PIT-missing and PIT-observed groups
only after predictions have been produced and validation labels have matured.
It never tunes a model, changes row eligibility, or reads external data.
"""
from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any

import polars as pl

from ashare_quant.research.feature_eligibility import (
    PIT_FEATURE_PREFIX,
    feature_eligibility_expression,
)

_KEYS = ["signal_date", "instrument"]
_LABEL_COLUMNS = {*_KEYS, "label_end_date", "forward_return"}
_ANY_PIT_MISSING = "__any_pit_missing__"

_DIAGNOSTIC_SCHEMA = {
    "signal_date": pl.String,
    "pit_feature": pl.String,
    "universe_rows": pl.UInt32,
    "universe_missing_rows": pl.UInt32,
    "universe_missing_rate": pl.Float64,
    "eligible_rows": pl.UInt32,
    "prediction_rows": pl.UInt32,
    "prediction_missing_rows": pl.UInt32,
    "prediction_missing_rate": pl.Float64,
    "top_n_rows": pl.UInt32,
    "top_n_missing_rows": pl.UInt32,
    "top_n_missing_rate": pl.Float64,
    "missing_prediction_rows": pl.UInt32,
    "observed_prediction_rows": pl.UInt32,
    "missing_mean_predicted_return": pl.Float64,
    "observed_mean_predicted_return": pl.Float64,
    "missing_labeled_rows": pl.UInt32,
    "observed_labeled_rows": pl.UInt32,
    "missing_label_coverage_rate": pl.Float64,
    "observed_label_coverage_rate": pl.Float64,
    "missing_mean_forward_return": pl.Float64,
    "observed_mean_forward_return": pl.Float64,
    "missing_positive_forward_ratio": pl.Float64,
    "observed_positive_forward_ratio": pl.Float64,
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


def _validate_keys(
    frame: pl.DataFrame,
    *,
    name: str,
    start: str,
    end: str,
) -> pl.DataFrame:
    missing = set(_KEYS).difference(frame.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")
    normalized = frame.with_columns(
        pl.col("signal_date").cast(pl.String),
        pl.col("instrument").cast(pl.String),
    )
    if normalized.select(
        pl.any_horizontal(
            pl.col("signal_date").is_null(),
            pl.col("instrument").is_null(),
            (pl.col("signal_date").str.strip_chars() == ""),
            (pl.col("instrument").str.strip_chars() == ""),
        ).any()
    ).item():
        raise ValueError(f"{name} contain null or empty keys")
    if normalized.select(pl.struct(_KEYS).is_duplicated().any()).item():
        raise ValueError(f"{name} contain duplicate signal_date/instrument keys")
    for signal_date in normalized["signal_date"].to_list():
        _iso_date(signal_date, f"{name}.signal_date")
        if not start <= signal_date <= end:
            raise ValueError(f"{name} contain a signal date outside the validation window")
    return normalized


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def _group_metrics(
    rows: list[dict[str, Any]],
    labels_by_key: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    scores = [float(row["predicted_return"]) for row in rows]
    matched = [
        labels_by_key[(str(row["signal_date"]), str(row["instrument"]))]
        for row in rows
        if (str(row["signal_date"]), str(row["instrument"])) in labels_by_key
    ]
    returns = [float(row["forward_return"]) for row in matched]
    return {
        "prediction_rows": len(rows),
        "mean_predicted_return": _mean(scores),
        "labeled_rows": len(matched),
        "label_coverage_rate": _rate(len(matched), len(rows)),
        "mean_forward_return": _mean(returns),
        "positive_forward_ratio": (
            sum(value > 0 for value in returns) / len(returns) if returns else None
        ),
    }


def evaluate_validation_feature_missingness(
    features: pl.DataFrame,
    predictions: pl.DataFrame,
    labels: pl.DataFrame,
    *,
    top_n: int,
    validation_start: str,
    validation_end: str,
    labels_mature_before: str,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Measure PIT missingness, eligible coverage, Top-N mix and group outcomes.

    Features are the full signal-date PIT-universe frame before inference
    eligibility filtering. Predictions must exactly cover rows admitted by the
    shared feature-eligibility rule. Labels must mature strictly before cutoff.
    """
    if not all(isinstance(frame, pl.DataFrame) for frame in (features, predictions, labels)):
        raise TypeError("features, predictions and labels must be Polars DataFrames")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a positive integer")
    start = _iso_date(validation_start, "validation_start")
    end = _iso_date(validation_end, "validation_end")
    cutoff = _iso_date(labels_mature_before, "labels_mature_before")
    if start > end or end >= cutoff:
        raise ValueError("validation window must end before the label maturity cutoff")

    feature_frame = _validate_keys(features, name="features", start=start, end=end)
    feature_columns = [name for name in feature_frame.columns if name not in _KEYS]
    if not feature_columns:
        raise ValueError("features contain no model feature columns")
    for name in feature_columns:
        if not feature_frame.schema[name].is_numeric():
            raise ValueError(f"feature column {name} must be numeric")
    try:
        feature_frame = feature_frame.with_columns(
            *[pl.col(name).cast(pl.Float64, strict=True) for name in feature_columns]
        )
    except Exception as exc:
        raise ValueError("feature columns must contain numeric values") from exc

    pit_features = sorted(
        name for name in feature_columns if name.startswith(PIT_FEATURE_PREFIX)
    )
    if not pit_features:
        raise ValueError("features contain no strict PIT feature columns")
    invalid_nonfinite = pl.any_horizontal(
        *[
            pl.col(name).is_not_null() & (~pl.col(name).is_finite())
            for name in feature_columns
        ]
    )
    if feature_frame.filter(invalid_nonfinite).height:
        raise ValueError("features contain non-null NaN or infinite source values")

    prediction_frame = _validate_keys(
        predictions, name="predictions", start=start, end=end
    )
    if "predicted_return" not in prediction_frame.columns:
        raise ValueError("predictions missing columns: ['predicted_return']")
    prediction_frame = prediction_frame.select(
        *_KEYS, pl.col("predicted_return").cast(pl.Float64, strict=True)
    )
    if prediction_frame.filter(
        pl.col("predicted_return").is_null()
        | (~pl.col("predicted_return").is_finite())
    ).height:
        raise ValueError("predictions contain null or non-finite predicted_return")

    eligible = feature_frame.filter(
        feature_eligibility_expression(feature_columns)
    )
    feature_keys = set(
        zip(
            eligible["signal_date"].to_list(),
            eligible["instrument"].to_list(),
            strict=True,
        )
    )
    prediction_keys = set(
        zip(
            prediction_frame["signal_date"].to_list(),
            prediction_frame["instrument"].to_list(),
            strict=True,
        )
    )
    if prediction_keys != feature_keys:
        missing_predictions = sorted(feature_keys - prediction_keys)[:5]
        unexpected_predictions = sorted(prediction_keys - feature_keys)[:5]
        raise ValueError(
            "predictions must exactly cover feature-eligible rows; "
            f"missing={missing_predictions}, unexpected={unexpected_predictions}"
        )

    missing_labels = _LABEL_COLUMNS.difference(labels.columns)
    if missing_labels:
        raise ValueError(f"labels missing columns: {sorted(missing_labels)}")
    label_frame = _validate_keys(
        labels, name="labels", start=start, end=end
    ).select(
        *_KEYS,
        pl.col("label_end_date").cast(pl.String),
        pl.col("forward_return").cast(pl.Float64, strict=True),
    )
    for label_end_date in label_frame["label_end_date"].to_list():
        _iso_date(label_end_date, "labels.label_end_date")
    if label_frame.filter(
        pl.col("label_end_date").is_null()
        | pl.col("forward_return").is_null()
        | (~pl.col("forward_return").is_finite())
        | (pl.col("label_end_date") <= pl.col("signal_date"))
        | (pl.col("label_end_date") >= cutoff)
    ).height:
        raise ValueError(
            "validation labels must be finite and mature strictly before the test cutoff"
        )
    feature_key_set = set(
        zip(
            feature_frame["signal_date"].to_list(),
            feature_frame["instrument"].to_list(),
            strict=True,
        )
    )
    label_key_set = set(
        zip(
            label_frame["signal_date"].to_list(),
            label_frame["instrument"].to_list(),
            strict=True,
        )
    )
    if not label_key_set.issubset(feature_key_set):
        raise ValueError("labels contain keys absent from the validation feature universe")

    feature_rows = feature_frame.to_dicts()
    prediction_rows = prediction_frame.to_dicts()
    label_rows = label_frame.to_dicts()
    feature_by_key = {
        (str(row["signal_date"]), str(row["instrument"])): row
        for row in feature_rows
    }
    labels_by_key = {
        (str(row["signal_date"]), str(row["instrument"])): row
        for row in label_rows
    }
    predictions_by_date: dict[str, list[dict[str, Any]]] = {}
    features_by_date: dict[str, list[dict[str, Any]]] = {}
    for row in feature_rows:
        features_by_date.setdefault(str(row["signal_date"]), []).append(row)
    for row in prediction_rows:
        predictions_by_date.setdefault(str(row["signal_date"]), []).append(row)
    eligible_by_date: dict[str, set[tuple[str, str]]] = {}
    for signal_date, instrument in feature_keys:
        eligible_by_date.setdefault(signal_date, set()).add((signal_date, instrument))

    diagnostic_rows: list[dict[str, Any]] = []
    signal_dates = sorted(feature_frame["signal_date"].unique().to_list())
    diagnostic_features = [*pit_features, _ANY_PIT_MISSING]
    for signal_date in signal_dates:
        date_features = features_by_date.get(signal_date, [])
        date_predictions = sorted(
            predictions_by_date.get(signal_date, []),
            key=lambda row: (
                -float(row["predicted_return"]),
                str(row["instrument"]),
            ),
        )
        top_rows = date_predictions[:top_n]
        date_eligible_count = len(eligible_by_date.get(signal_date, set()))
        for pit_feature in diagnostic_features:
            def is_missing(
                row: dict[str, Any],
                selected_feature: str = pit_feature,
            ) -> bool:
                if selected_feature == _ANY_PIT_MISSING:
                    return any(row[name] is None for name in pit_features)
                return row[selected_feature] is None

            universe_missing = [row for row in date_features if is_missing(row)]
            prediction_missing = [
                row
                for row in date_predictions
                if is_missing(feature_by_key[(signal_date, str(row["instrument"]))])
            ]
            top_missing = [
                row
                for row in top_rows
                if is_missing(feature_by_key[(signal_date, str(row["instrument"]))])
            ]
            missing_group = [
                row for row in date_predictions
                if is_missing(feature_by_key[(signal_date, str(row["instrument"]))])
            ]
            observed_group = [
                row for row in date_predictions
                if not is_missing(feature_by_key[(signal_date, str(row["instrument"]))])
            ]
            missing_metrics = _group_metrics(missing_group, labels_by_key)
            observed_metrics = _group_metrics(observed_group, labels_by_key)
            diagnostic_rows.append(
                {
                    "signal_date": signal_date,
                    "pit_feature": pit_feature,
                    "universe_rows": len(date_features),
                    "universe_missing_rows": len(universe_missing),
                    "universe_missing_rate": _rate(len(universe_missing), len(date_features)),
                    "eligible_rows": date_eligible_count,
                    "prediction_rows": len(date_predictions),
                    "prediction_missing_rows": len(prediction_missing),
                    "prediction_missing_rate": _rate(len(prediction_missing), len(date_predictions)),
                    "top_n_rows": len(top_rows),
                    "top_n_missing_rows": len(top_missing),
                    "top_n_missing_rate": _rate(len(top_missing), len(top_rows)),
                    "missing_prediction_rows": missing_metrics["prediction_rows"],
                    "observed_prediction_rows": observed_metrics["prediction_rows"],
                    "missing_mean_predicted_return": missing_metrics["mean_predicted_return"],
                    "observed_mean_predicted_return": observed_metrics["mean_predicted_return"],
                    "missing_labeled_rows": missing_metrics["labeled_rows"],
                    "observed_labeled_rows": observed_metrics["labeled_rows"],
                    "missing_label_coverage_rate": missing_metrics["label_coverage_rate"],
                    "observed_label_coverage_rate": observed_metrics["label_coverage_rate"],
                    "missing_mean_forward_return": missing_metrics["mean_forward_return"],
                    "observed_mean_forward_return": observed_metrics["mean_forward_return"],
                    "missing_positive_forward_ratio": missing_metrics["positive_forward_ratio"],
                    "observed_positive_forward_ratio": observed_metrics["positive_forward_ratio"],
                }
            )

    diagnostics = pl.DataFrame(diagnostic_rows, schema=_DIAGNOSTIC_SCHEMA)

    by_feature: dict[str, dict[str, Any]] = {}
    for pit_feature in diagnostic_features:
        rows = [
            row for row in diagnostic_rows if row["pit_feature"] == pit_feature
        ]

        def total(field: str) -> int:
            return sum(int(row[field]) for row in rows)

        def weighted_mean(value_field: str, count_field: str) -> float | None:
            denominator = total(count_field)
            if denominator == 0:
                return None
            return sum(
                float(row[value_field]) * int(row[count_field])
                for row in rows
                if row[value_field] is not None and row[count_field]
            ) / denominator

        universe_rows = total("universe_rows")
        universe_missing_rows = total("universe_missing_rows")
        prediction_rows_count = total("prediction_rows")
        prediction_missing_rows = total("prediction_missing_rows")
        top_n_rows_count = total("top_n_rows")
        top_n_missing_rows = total("top_n_missing_rows")
        missing_prediction_count = total("missing_prediction_rows")
        observed_prediction_count = total("observed_prediction_rows")
        missing_labeled_count = total("missing_labeled_rows")
        observed_labeled_count = total("observed_labeled_rows")
        by_feature[pit_feature] = {
            "universe_rows": universe_rows,
            "universe_missing_rows": universe_missing_rows,
            "universe_missing_rate": _rate(universe_missing_rows, universe_rows),
            "eligible_rows": total("eligible_rows"),
            "prediction_rows": prediction_rows_count,
            "prediction_missing_rows": prediction_missing_rows,
            "prediction_missing_rate": _rate(prediction_missing_rows, prediction_rows_count),
            "top_n_rows": top_n_rows_count,
            "top_n_missing_rows": top_n_missing_rows,
            "top_n_missing_rate": _rate(top_n_missing_rows, top_n_rows_count),
            "missing_prediction_rows": missing_prediction_count,
            "observed_prediction_rows": observed_prediction_count,
            "missing_mean_predicted_return": weighted_mean(
                "missing_mean_predicted_return", "missing_prediction_rows"
            ),
            "observed_mean_predicted_return": weighted_mean(
                "observed_mean_predicted_return", "observed_prediction_rows"
            ),
            "missing_labeled_rows": missing_labeled_count,
            "observed_labeled_rows": observed_labeled_count,
            "missing_label_coverage_rate": _rate(
                missing_labeled_count, missing_prediction_count
            ),
            "observed_label_coverage_rate": _rate(
                observed_labeled_count, observed_prediction_count
            ),
            "missing_mean_forward_return": weighted_mean(
                "missing_mean_forward_return", "missing_labeled_rows"
            ),
            "observed_mean_forward_return": weighted_mean(
                "observed_mean_forward_return", "observed_labeled_rows"
            ),
            "missing_positive_forward_ratio": weighted_mean(
                "missing_positive_forward_ratio", "missing_labeled_rows"
            ),
            "observed_positive_forward_ratio": weighted_mean(
                "observed_positive_forward_ratio", "observed_labeled_rows"
            ),
            "mean_daily_universe_missing_rate": _mean(
                [float(row["universe_missing_rate"]) for row in rows
                 if row["universe_missing_rate"] is not None]
            ),
            "mean_daily_prediction_missing_rate": _mean(
                [float(row["prediction_missing_rate"]) for row in rows
                 if row["prediction_missing_rate"] is not None]
            ),
            "mean_daily_top_n_missing_rate": _mean(
                [float(row["top_n_missing_rate"]) for row in rows
                 if row["top_n_missing_rate"] is not None]
            ),
            "signal_date_count": len(rows),
        }

    report = {
        "status": "evaluated" if prediction_rows else "no_eligible_predictions",
        "validation_period": {"start": start, "end": end},
        "labels_mature_before": cutoff,
        "top_n": top_n,
        "pit_features": pit_features,
        "signal_date_count": len(signal_dates),
        "prediction_rows": len(prediction_rows),
        "by_feature": by_feature,
        "metadata_dimensions_deferred": ["market_cap", "listing_age", "industry"],
        "metadata_dimensions_note": (
            "The validated bundle currently exposes point-in-time membership "
            "keys but no receipt-backed market-cap, listing-date or industry "
            "dimension table. These dimensions are intentionally not inferred "
            "from instrument codes or price/turnover proxies."
        ),
        "interpretation": (
            "Descriptive validation-only diagnostics. Missing-versus-observed "
            "score and forward-return comparisons use predictions produced "
            "without labels and labels matured strictly before the test cutoff. "
            "These diagnostics do not tune or select model parameters."
        ),
    }
    return diagnostics, report


_COVERAGE_SCHEMA = {
    "signal_date": pl.String,
    "pit_feature": pl.String,
    "universe_rows": pl.UInt32,
    "universe_missing_rows": pl.UInt32,
    "universe_missing_rate": pl.Float64,
    "eligible_rows": pl.UInt32,
    "prediction_rows": pl.UInt32,
    "prediction_missing_rows": pl.UInt32,
    "prediction_missing_rate": pl.Float64,
    "top_n_rows": pl.UInt32,
    "top_n_missing_rows": pl.UInt32,
    "top_n_missing_rate": pl.Float64,
}


def evaluate_feature_coverage(
    features: pl.DataFrame,
    predictions: pl.DataFrame,
    *,
    top_n: int,
    start_date: str,
    end_date: str,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Report PIT universe, inference and Top-N coverage without reading labels.

    This coverage-only path is suitable for untouched test/OOS windows. It
    deliberately has no label argument, so it cannot leak test outcomes into
    model selection or diagnostic reporting.
    """
    if not isinstance(features, pl.DataFrame) or not isinstance(predictions, pl.DataFrame):
        raise TypeError("features and predictions must be Polars DataFrames")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a positive integer")
    start = _iso_date(start_date, "start_date")
    end = _iso_date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date must be on or before end_date")

    feature_frame = _validate_keys(features, name="features", start=start, end=end)
    feature_columns = [name for name in feature_frame.columns if name not in _KEYS]
    if not feature_columns:
        raise ValueError("features contain no model feature columns")
    for name in feature_columns:
        if not feature_frame.schema[name].is_numeric():
            raise ValueError(f"feature column {name} must be numeric")
    try:
        feature_frame = feature_frame.with_columns(
            *[pl.col(name).cast(pl.Float64, strict=True) for name in feature_columns]
        )
    except Exception as exc:
        raise ValueError("feature columns must contain numeric values") from exc

    pit_features = sorted(
        name for name in feature_columns if name.startswith(PIT_FEATURE_PREFIX)
    )
    if not pit_features:
        raise ValueError("features contain no strict PIT feature columns")
    invalid_nonfinite = pl.any_horizontal(
        *[
            pl.col(name).is_not_null() & (~pl.col(name).is_finite())
            for name in feature_columns
        ]
    )
    if feature_frame.filter(invalid_nonfinite).height:
        raise ValueError("features contain non-null NaN or infinite source values")

    prediction_frame = _validate_keys(
        predictions, name="predictions", start=start, end=end
    )
    if "predicted_return" not in prediction_frame.columns:
        raise ValueError("predictions missing columns: ['predicted_return']")
    prediction_frame = prediction_frame.select(
        *_KEYS, pl.col("predicted_return").cast(pl.Float64, strict=True)
    )
    if prediction_frame.filter(
        pl.col("predicted_return").is_null()
        | (~pl.col("predicted_return").is_finite())
    ).height:
        raise ValueError("predictions contain null or non-finite predicted_return")

    eligible = feature_frame.filter(
        feature_eligibility_expression(feature_columns)
    )
    eligible_keys = set(
        zip(
            eligible["signal_date"].to_list(),
            eligible["instrument"].to_list(),
            strict=True,
        )
    )
    prediction_keys = set(
        zip(
            prediction_frame["signal_date"].to_list(),
            prediction_frame["instrument"].to_list(),
            strict=True,
        )
    )
    if prediction_keys != eligible_keys:
        missing_predictions = sorted(eligible_keys - prediction_keys)[:5]
        unexpected_predictions = sorted(prediction_keys - eligible_keys)[:5]
        raise ValueError(
            "predictions must exactly cover feature-eligible rows; "
            f"missing={missing_predictions}, unexpected={unexpected_predictions}"
        )

    feature_rows = feature_frame.to_dicts()
    prediction_rows = prediction_frame.to_dicts()
    feature_by_key = {
        (str(row["signal_date"]), str(row["instrument"])): row
        for row in feature_rows
    }
    features_by_date: dict[str, list[dict[str, Any]]] = {}
    predictions_by_date: dict[str, list[dict[str, Any]]] = {}
    for row in feature_rows:
        features_by_date.setdefault(str(row["signal_date"]), []).append(row)
    for row in prediction_rows:
        predictions_by_date.setdefault(str(row["signal_date"]), []).append(row)

    diagnostic_features = [*pit_features, _ANY_PIT_MISSING]
    diagnostic_rows: list[dict[str, Any]] = []
    signal_dates = sorted(feature_frame["signal_date"].unique().to_list())
    for signal_date in signal_dates:
        date_features = features_by_date.get(signal_date, [])
        date_predictions = sorted(
            predictions_by_date.get(signal_date, []),
            key=lambda row: (-float(row["predicted_return"]), str(row["instrument"])),
        )
        top_rows = date_predictions[:top_n]
        for pit_feature in diagnostic_features:
            def is_missing(
                row: dict[str, Any],
                selected_feature: str = pit_feature,
            ) -> bool:
                if selected_feature == _ANY_PIT_MISSING:
                    return any(row[name] is None for name in pit_features)
                return row[selected_feature] is None

            universe_missing = [row for row in date_features if is_missing(row)]
            prediction_missing = [
                row for row in date_predictions
                if is_missing(feature_by_key[(signal_date, str(row["instrument"]))])
            ]
            top_missing = [
                row for row in top_rows
                if is_missing(feature_by_key[(signal_date, str(row["instrument"]))])
            ]
            diagnostic_rows.append(
                {
                    "signal_date": signal_date,
                    "pit_feature": pit_feature,
                    "universe_rows": len(date_features),
                    "universe_missing_rows": len(universe_missing),
                    "universe_missing_rate": _rate(len(universe_missing), len(date_features)),
                    "eligible_rows": sum(
                        1 for key in eligible_keys if key[0] == signal_date
                    ),
                    "prediction_rows": len(date_predictions),
                    "prediction_missing_rows": len(prediction_missing),
                    "prediction_missing_rate": _rate(
                        len(prediction_missing), len(date_predictions)
                    ),
                    "top_n_rows": len(top_rows),
                    "top_n_missing_rows": len(top_missing),
                    "top_n_missing_rate": _rate(len(top_missing), len(top_rows)),
                }
            )

    diagnostics = pl.DataFrame(diagnostic_rows, schema=_COVERAGE_SCHEMA)
    by_feature: dict[str, dict[str, Any]] = {}
    for pit_feature in diagnostic_features:
        rows = [row for row in diagnostic_rows if row["pit_feature"] == pit_feature]
        universe_rows = sum(int(row["universe_rows"]) for row in rows)
        universe_missing_rows = sum(int(row["universe_missing_rows"]) for row in rows)
        prediction_rows_count = sum(int(row["prediction_rows"]) for row in rows)
        prediction_missing_rows = sum(int(row["prediction_missing_rows"]) for row in rows)
        top_n_rows_count = sum(int(row["top_n_rows"]) for row in rows)
        top_n_missing_rows = sum(int(row["top_n_missing_rows"]) for row in rows)
        by_feature[pit_feature] = {
            "universe_rows": universe_rows,
            "universe_missing_rows": universe_missing_rows,
            "universe_missing_rate": _rate(universe_missing_rows, universe_rows),
            "eligible_rows": sum(int(row["eligible_rows"]) for row in rows),
            "prediction_rows": prediction_rows_count,
            "prediction_missing_rows": prediction_missing_rows,
            "prediction_missing_rate": _rate(
                prediction_missing_rows, prediction_rows_count
            ),
            "top_n_rows": top_n_rows_count,
            "top_n_missing_rows": top_n_missing_rows,
            "top_n_missing_rate": _rate(top_n_missing_rows, top_n_rows_count),
            "mean_daily_universe_missing_rate": _mean(
                [float(row["universe_missing_rate"]) for row in rows
                 if row["universe_missing_rate"] is not None]
            ),
            "mean_daily_prediction_missing_rate": _mean(
                [float(row["prediction_missing_rate"]) for row in rows
                 if row["prediction_missing_rate"] is not None]
            ),
            "mean_daily_top_n_missing_rate": _mean(
                [float(row["top_n_missing_rate"]) for row in rows
                 if row["top_n_missing_rate"] is not None]
            ),
            "signal_date_count": len(rows),
        }

    report = {
        "status": "evaluated" if prediction_rows else "no_eligible_predictions",
        "period": {"start": start, "end": end},
        "top_n": top_n,
        "pit_features": pit_features,
        "signal_date_count": len(signal_dates),
        "prediction_rows": len(prediction_rows),
        "by_feature": by_feature,
        "metadata_dimensions_deferred": ["market_cap", "listing_age", "industry"],
        "metadata_dimensions_note": (
            "The validated bundle currently exposes point-in-time membership "
            "keys but no receipt-backed market-cap, listing-date or industry "
            "dimension table. These dimensions are intentionally not inferred "
            "from instrument codes or price/turnover proxies."
        ),
        "interpretation": (
            "Coverage-only diagnostics for validation or untouched test/OOS "
            "predictions. No labels are accepted or read by this function."
        ),
    }
    return diagnostics, report
