"""Chronological period and trailing-only market-regime diagnostics.

Consumes a verified Phase 8 experiment and its matching canonical research
bundle. It never reads CNEquity, reruns the strategy, or fits a model.
"""
from __future__ import annotations

from math import isfinite, prod, sqrt
from statistics import median, stdev
from typing import Any

import polars as pl

from ashare_quant.research.bundle import ResearchDataBundle
from ashare_quant.research.experiment_compare import (
    VerifiedWalkForwardExperiment,
    verify_walk_forward_experiment,
)
from ashare_quant.research.qa import _assess_verified_experiment

PROTOCOL_VERSION = "phase10.3"
REGIME_LOOKBACK_SESSIONS = 63
MIN_UNIVERSE_COVERAGE = 0.80
BULL_MEDIAN_RETURN = 0.08
BEAR_MEDIAN_RETURN = -0.08
BULL_BREADTH = 0.60
BEAR_BREADTH = 0.40
DEFAULT_MIN_PERIOD_SESSIONS = 126


def _daily_metrics(rows: list[dict[str, Any]], annualization: int) -> dict[str, Any]:
    returns = [float(row["daily_return"]) for row in rows]
    growth = prod(1.0 + value for value in returns)
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1.0)
    volatility = stdev(returns) * sqrt(annualization) if len(returns) > 1 else None
    sharpe = (
        (sum(returns) / len(returns)) / stdev(returns) * sqrt(annualization)
        if len(returns) > 1 and stdev(returns) > 0 else None
    )
    return {
        "sessions": len(returns),
        "total_return": growth - 1.0,
        "annualized_volatility": volatility,
        "sharpe_ratio": sharpe,
        "max_drawdown": drawdown,
    }


def _validate_bundle_identity(
    experiment: VerifiedWalkForwardExperiment,
    bundle: ResearchDataBundle,
) -> list[str]:
    if not isinstance(bundle, ResearchDataBundle):
        raise TypeError("bundle must be a validated ResearchDataBundle")
    if not isinstance(bundle.adjusted_bars, pl.DataFrame) or not isinstance(bundle.universe_membership, pl.DataFrame):
        raise TypeError("bundle adjusted_bars and universe_membership must be Polars DataFrames")
    if not {"instrument", "date", "adj_close", "adj_is_exact"}.issubset(bundle.adjusted_bars.columns):
        raise ValueError("bundle adjusted_bars is missing required HFQ columns")
    if not {"instrument", "date"}.issubset(bundle.universe_membership.columns):
        raise ValueError("bundle universe_membership is missing instrument/date columns")
    if not bundle.trading_dates or bundle.trading_dates != sorted(set(bundle.trading_dates)):
        raise ValueError("bundle trading_dates must be non-empty, unique and chronological")
    for name, frame in (("adjusted_bars", bundle.adjusted_bars), ("universe_membership", bundle.universe_membership)):
        if frame.select(pl.struct(["instrument", "date"]).is_duplicated().any()).item():
            raise ValueError(f"bundle {name} contains duplicate instrument/date keys")
    if bundle.adjusted_bars.filter(
        pl.col("adj_is_exact").is_null()
        | (pl.col("adj_is_exact") != True)
        | pl.col("adj_close").is_null()
        | (~pl.col("adj_close").cast(pl.Float64).is_finite())
        | (pl.col("adj_close").cast(pl.Float64) <= 0)
    ).height:
        raise ValueError("bundle adjusted_bars must contain positive, finite, exact HFQ prices")
    admission = _assess_verified_experiment(experiment)
    if admission["decision"] != "PRICE_ONLY_ELIGIBLE":
        raise ValueError("P10.3 requires PRICE_ONLY_ELIGIBLE verified experiment admission")
    provenance = bundle.provenance
    expected = experiment.manifest["data_provenance"]
    expected_window = expected.get("acceptance_report_window", {})
    if (
        not isinstance(expected_window, dict)
        or bundle.trading_dates[0] != expected_window.get("start")
        or bundle.trading_dates[-1] != expected_window.get("end")
    ):
        raise ValueError("P10.3 bundle trading calendar does not exactly match experiment window")
    calendar_dates = set(bundle.trading_dates)
    if not set(bundle.adjusted_bars["date"].cast(pl.String).to_list()).issubset(calendar_dates):
        raise ValueError("bundle adjusted_bars contains dates outside trading calendar")
    if not set(bundle.universe_membership["date"].cast(pl.String).to_list()).issubset(calendar_dates):
        raise ValueError("bundle universe_membership contains dates outside trading calendar")
    checks = {
        "acceptance_report_id": expected.get("acceptance_report_id"),
        "universe_profile": expected.get("universe_profile"),
        "source_revisions": expected.get("source_revisions"),
        "acceptance_report_window": expected.get("acceptance_report_window"),
        "acceptance_gates": expected.get("acceptance_gates"),
    }
    mismatches = [
        key for key, value in checks.items()
        if provenance.get(key) != value
    ]
    if mismatches:
        raise ValueError(
            "P10.3 bundle provenance does not match verified experiment: "
            + ", ".join(sorted(mismatches))
        )
    return list(bundle.trading_dates)


def _market_states(
    bundle: ResearchDataBundle, target_dates: list[str]
) -> dict[str, dict[str, Any]]:
    """Calculate OOS trailing labels without forward fill or full-panel materialization."""
    sessions = list(bundle.trading_dates)
    position = {session: index for index, session in enumerate(sessions)}
    price_dates = set()
    for session in target_dates:
        index = position[session]
        price_dates.add(session)
        if index >= REGIME_LOOKBACK_SESSIONS:
            price_dates.add(sessions[index - REGIME_LOOKBACK_SESSIONS])
    adjusted = bundle.adjusted_bars.filter(pl.col("date").cast(pl.String).is_in(sorted(price_dates))).select(
        pl.col("instrument").cast(pl.String),
        pl.col("date").cast(pl.String),
        pl.col("adj_close").cast(pl.Float64),
        pl.col("adj_is_exact").cast(pl.Boolean),
    )
    membership = bundle.universe_membership.filter(
        pl.col("date").cast(pl.String).is_in(target_dates)
    ).select(
        pl.col("instrument").cast(pl.String),
        pl.col("date").cast(pl.String),
    )
    prices: dict[tuple[str, str], float] = {}
    for row in adjusted.iter_rows(named=True):
        if row["adj_is_exact"] is True and row["adj_close"] is not None:
            prices[(row["instrument"], row["date"])] = float(row["adj_close"])
    members_by_date: dict[str, list[str]] = {}
    for row in membership.iter_rows(named=True):
        members_by_date.setdefault(row["date"], []).append(row["instrument"])

    output: dict[str, dict[str, Any]] = {}
    for session in target_dates:
        index = position[session]
        eligible = sorted(set(members_by_date.get(session, [])))
        record: dict[str, Any] = {
            "date": session,
            "regime": "UNKNOWN",
            "eligible_universe": len(eligible),
            "valid_universe": 0,
            "valid_universe_coverage": 0.0,
            "median_63_session_return": None,
            "positive_breadth": None,
            "unknown_reason": None,
        }
        if index < REGIME_LOOKBACK_SESSIONS:
            record["unknown_reason"] = "insufficient_63_session_lookback"
        elif not eligible:
            record["unknown_reason"] = "missing_daily_pit_universe_membership"
        else:
            lag_date = sessions[index - REGIME_LOOKBACK_SESSIONS]
            returns = []
            for instrument in eligible:
                current = prices.get((instrument, session))
                prior = prices.get((instrument, lag_date))
                if current is not None and prior is not None and prior > 0:
                    returns.append(current / prior - 1.0)
            record["valid_universe"] = len(returns)
            coverage = len(returns) / len(eligible)
            record["valid_universe_coverage"] = coverage
            if coverage < MIN_UNIVERSE_COVERAGE:
                record["unknown_reason"] = "valid_universe_coverage_below_80_percent"
            else:
                median_return = float(median(returns))
                breadth = sum(value > 0 for value in returns) / len(returns)
                record["median_63_session_return"] = median_return
                record["positive_breadth"] = breadth
                if median_return >= BULL_MEDIAN_RETURN and breadth >= BULL_BREADTH:
                    record["regime"] = "BULL"
                elif median_return <= BEAR_MEDIAN_RETURN and breadth <= BEAR_BREADTH:
                    record["regime"] = "BEAR"
                else:
                    record["regime"] = "SIDEWAYS_OR_MIXED"
        output[session] = record
    return output


def build_period_regime_diagnostics(
    experiment_path: str,
    bundle: ResearchDataBundle,
    *,
    min_period_sessions: int = DEFAULT_MIN_PERIOD_SESSIONS,
    annualization: int = 252,
) -> dict[str, Any]:
    """Build fold/year and market-regime summaries from verified OOS evidence.

    The bundle must have identical acceptance identity and source revisions to
    the experiment. Daily membership is required for non-UNKNOWN regime labels;
    weekly-only snapshots are deliberately not carried forward.
    """
    if isinstance(min_period_sessions, bool) or not isinstance(min_period_sessions, int) or min_period_sessions < 1:
        raise ValueError("min_period_sessions must be a positive integer")
    if isinstance(annualization, bool) or not isinstance(annualization, int) or annualization < 1:
        raise ValueError("annualization must be a positive integer")

    experiment = verify_walk_forward_experiment(experiment_path)
    sessions = _validate_bundle_identity(experiment, bundle)
    daily = experiment.oos_daily
    required = {"date", "daily_return"}
    if not required.issubset(daily.columns):
        raise ValueError("Verified OOS daily ledger is missing date/daily_return")
    daily_rows = daily.sort("date").to_dicts()
    oos_dates = [str(row["date"]) for row in daily_rows]
    if len(oos_dates) != len(set(oos_dates)):
        raise ValueError("Verified OOS daily ledger contains duplicate dates")
    if any(day not in set(sessions) for day in oos_dates):
        raise ValueError("OOS daily dates are outside the supplied canonical bundle calendar")
    if any(row["daily_return"] is None for row in daily_rows):
        raise ValueError("OOS daily ledger contains null daily_return")
    if any(
        not isinstance(row["daily_return"], (int, float))
        or not isfinite(float(row["daily_return"]))
        or float(row["daily_return"]) <= -1.0
        for row in daily_rows
    ):
        raise ValueError("OOS daily ledger contains invalid daily_return values")

    periods: list[dict[str, Any]] = []
    for item in experiment.folds:
        fold = item["fold"]
        selected = [
            row for row in daily_rows
            if fold["test_start"] <= str(row["date"]) <= fold["test_end"]
        ]
        if len(selected) != int(fold["test_sessions"]):
            raise ValueError(f"{fold['fold_id']} OOS daily rows do not match declared test_sessions")
        metrics = _daily_metrics(selected, annualization)
        status = "OK" if len(selected) >= min_period_sessions else "INSUFFICIENT_SAMPLE"
        periods.append({
            "period_type": "walk_forward_fold",
            "return_basis": "contiguous_fold_oos_daily_returns",
            "period_id": fold["fold_id"],
            "start": fold["test_start"],
            "end": fold["test_end"],
            "status": status,
            **metrics,
        })

    by_year: dict[int, list[dict[str, Any]]] = {}
    for row in daily_rows:
        by_year.setdefault(int(str(row["date"])[:4]), []).append(row)
    for year, rows in sorted(by_year.items()):
        metrics = _daily_metrics(rows, annualization)
        year = int(year)
        calendar_sessions = sum(str(day).startswith(f"{year:04d}-") for day in sessions)
        status = "OK" if len(rows) >= min_period_sessions else "INSUFFICIENT_SAMPLE"
        periods.append({
            "period_type": "calendar_year",
            "return_basis": "observed_oos_sessions_only; gaps_excluded",
            "observed_session_coverage": len(rows) / calendar_sessions if calendar_sessions else 0.0,
            "period_id": str(year),
            "start": str(rows[0]["date"]),
            "end": str(rows[-1]["date"]),
            "status": status,
            **metrics,
        })

    market = _market_states(bundle, oos_dates)
    regime_rows = [market[day] for day in oos_dates]
    daily_by_regime: dict[str, list[dict[str, Any]]] = {}
    for row, state in zip(daily_rows, regime_rows):
        daily_by_regime.setdefault(state["regime"], []).append(row)

    regimes: list[dict[str, Any]] = []
    for regime in ("BULL", "BEAR", "SIDEWAYS_OR_MIXED", "UNKNOWN"):
        selected = daily_by_regime.get(regime, [])
        if not selected:
            continue
        states = [market[str(row["date"])] for row in selected]
        coverage_values = [item["valid_universe_coverage"] for item in states]
        valid = [value for value in coverage_values if value > 0]
        metrics = _daily_metrics(selected, annualization)
        median_values = [item["median_63_session_return"] for item in states if item["median_63_session_return"] is not None]
        breadth_values = [item["positive_breadth"] for item in states if item["positive_breadth"] is not None]
        regimes.append({
            "regime": regime,
            "status": (
                "OK" if regime != "UNKNOWN" and len(selected) >= min_period_sessions
                else "INSUFFICIENT_SAMPLE" if regime != "UNKNOWN"
                else "UNKNOWN"
            ),
            **metrics,
            "mean_valid_universe_coverage": (
                sum(valid) / len(valid) if valid else 0.0
            ),
            "mean_eligible_universe": sum(item["eligible_universe"] for item in states) / len(states),
            "mean_valid_universe": sum(item["valid_universe"] for item in states) / len(states),
            "mean_median_63_session_return": (
                sum(median_values) / len(median_values) if median_values else None
            ),
            "mean_positive_breadth": (
                sum(breadth_values) / len(breadth_values) if breadth_values else None
            ),
        })

    unknown_reasons: dict[str, int] = {}
    for state in regime_rows:
        reason = state["unknown_reason"]
        if reason:
            unknown_reasons[reason] = unknown_reasons.get(reason, 0) + 1

    limitations = []
    if unknown_reasons:
        limitations.append(
            "Some OOS sessions have UNKNOWN market state; no membership or price "
            "values were forward-filled. Reasons: "
            + ", ".join(f"{key}={value}" for key, value in sorted(unknown_reasons.items()))
        )
    if bundle.provenance.get("acceptance_gates", {}).get("point_in_time_fundamentals") != "PASS":
        limitations.append(
            "PIT fundamentals did not PASS; results remain conditional price-only "
            "benchmark evidence and do not validate or unlock LightGBM training."
        )

    return {
        "protocol_version": PROTOCOL_VERSION,
        "experiment_id": str(experiment.manifest["experiment_id"]),
        "acceptance_report_id": experiment.manifest["data_provenance"]["acceptance_report_id"],
        "universe_profile": experiment.manifest["data_provenance"]["universe_profile"],
        "source_revisions": dict(experiment.manifest["data_provenance"]["source_revisions"]),
        "periods": periods,
        "regimes": regimes,
        "market_state_session_count": len(regime_rows),
        "unknown_market_state_sessions": sum(
            item["regime"] == "UNKNOWN" for item in regime_rows
        ),
        "unknown_reasons": unknown_reasons,
        "limitations": limitations,
        "model_training_performed": False,
        "training_dataset_unlocked": False,
    }
