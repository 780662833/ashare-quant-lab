from pathlib import Path

import polars as pl
import pytest

from ashare_quant.research.experiment_compare import VerifiedWalkForwardExperiment
from ashare_quant.research import statistical_qa

def make_experiment(tmp_path, *, fail_gate=False, daily_dates=None):
    dependencies = {
        "daily_bars": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
        "adj_factors": {"revision_id": "hfq-r1", "contract_fingerprint": "hfq-contract"},
        "trading_status": {"revision_id": "status-r1", "contract_fingerprint": "status-contract"},
        "trading_calendar": {"revision_id": "calendar-r1", "contract_fingerprint": "calendar-contract"},
    }
    revisions = {
        **dependencies, "universe": dependencies["daily_bars"],
        "price_limit_rules": {
            "revision_id": "limits-r1", "contract_fingerprint": "limits-contract",
            "source": "synthetic-provider",
        },
    }
    provenance = {
        "acceptance_report_id": "acceptance-stats-001",
        "acceptance_report_window": {"start": "2020-01-01", "end": "2020-12-31"},
        "universe_profile": "cn_a_sh_sz_research_v1",
        "acceptance_gates": {
            "historical_universe": "FAIL" if fail_gate else "PASS",
            "hfq_adjustment": "PASS",
            "point_in_time_fundamentals": "FAIL",
            "trading_status": "PASS",
        },
        "required_benchmark_gates": {
            "historical_universe": "FAIL" if fail_gate else "PASS",
            "hfq_adjustment": "PASS", "trading_status": "PASS",
        },
        "pit_fundamentals_used": False, "training_dataset_unlocked": False,
        "scope": {"start_date": "2020-01-01", "end_date": "2020-12-31"},
        "source_revisions": revisions,
        "source_receipts": [{
            "dataset": "daily_bars",
            "receipt": {"replayable": True, "receipt_id": "adapter-r1", "dependencies": dependencies},
        }],
        "receipt_provenance": {"dependency_identities": dependencies},
        "acceptance_source_receipts": {
            "historical_universe": {"replayable": True, "receipt_id": "universe-r1"},
            "hfq": {"replayable": True, "receipt_id": "hfq-r1"},
        },
        "limit_evidence": {
            "revision": "limits-r1", "source_revision_identity": revisions["price_limit_rules"],
        },
    }
    dates = daily_dates or ["2020-03-02", "2020-03-03", "2020-03-04"]
    folds = [
        {"fold": {"fold_id": f"fold_{i:03d}", "test_start": day, "test_end": day, "test_sessions": 1}}
        for i, day in enumerate(dates, 1)
    ]
    fold_table = pl.DataFrame({
        "fold_id": [f"fold_{i:03d}" for i in range(1, 4)],
        "test_sessions": [1, 1, 1],
        "total_return": [0.10, -0.05, 0.03],
        "cagr": [0.10, -0.05, 0.03],
        "annualized_volatility": [0.20, 0.30, 0.15],
        "sharpe_ratio": [1.2, -0.2, 0.8],
        "max_drawdown": [-0.04, -0.10, -0.03],
        "order_count": [2, 3, 1],
        "no_fill_order_count": [0, 1, 0],
        "total_transaction_costs": [10.0, 12.0, 8.0],
    })
    daily = pl.DataFrame({
        "date": dates, "daily_return": [0.01, -0.02, 0.03],
        "nav": [100.0, 98.0, 101.0],
    })
    orders = pl.DataFrame({
        "requested_qty": [100, 100, 100],
        "filled_qty": [100, 50, 0],
        "status": ["filled", "partially_filled", "no_fill"],
        "reason": [None, None, "suspended"],
    })
    return VerifiedWalkForwardExperiment(
        path=Path(tmp_path) / "stats-smoke",
        manifest={
            "experiment_id": "stats-smoke", "data_provenance": provenance,
            "training_dataset_unlocked": False, "model_training_performed": False,
        },
        folds=folds, fold_table=fold_table, oos_daily=daily,
        oos_orders=orders, oos_holdings=pl.DataFrame(),
    )

def test_statistical_qa_reports_descriptive_fold_distribution_only(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path)
    monkeypatch.setattr(statistical_qa, "verify_walk_forward_experiment", lambda _path: experiment)

    report = statistical_qa.build_statistical_qa_summary(str(experiment.path))

    assert report["decision"] == "DESCRIPTIVE_ONLY"
    assert report["sample_counts"] == {"fold_count": 3, "oos_session_count": 3, "order_count": 3}
    assert report["fold_metric_distributions"]["total_return"]["median"] == pytest.approx(0.03)
    assert report["fold_metric_distributions"]["total_return"]["sample_count"] == 3
    assert report["positive_fold_ratio"] == pytest.approx(2 / 3)
    assert report["worst_fold"] == {"fold_id": "fold_002", "total_return": -0.05}
    assert report["positive_excess_fold_ratio"]["status"] == "NOT_AVAILABLE"
    assert report["statistical_inference"]["status"] == "NOT_IMPLEMENTED"
    assert report["training_dataset_unlocked"] is False


def test_statistical_qa_blocks_metrics_when_data_admission_fails(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path, fail_gate=True)
    monkeypatch.setattr(statistical_qa, "verify_walk_forward_experiment", lambda _path: experiment)

    report = statistical_qa.build_statistical_qa_summary(str(experiment.path))

    assert report["decision"] == "BLOCKED"
    assert report["fold_metric_distributions"] is None
    assert report["limitations"]


def test_statistical_qa_rejects_duplicate_or_incomplete_oos_sessions(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path, daily_dates=["2020-03-02", "2020-03-02", "2020-03-04"])
    monkeypatch.setattr(statistical_qa, "verify_walk_forward_experiment", lambda _path: experiment)

    with pytest.raises(ValueError, match="unique and chronological"):
        statistical_qa.build_statistical_qa_summary(str(experiment.path))


def test_statistical_qa_rejects_invalid_daily_returns(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path)
    experiment.oos_daily = experiment.oos_daily.with_columns(
        pl.when(pl.col("date") == "2020-03-03").then(float("nan")).otherwise(pl.col("daily_return")).alias("daily_return")
    )
    monkeypatch.setattr(statistical_qa, "verify_walk_forward_experiment", lambda _path: experiment)

    with pytest.raises(ValueError, match="invalid daily returns"):
        statistical_qa.build_statistical_qa_summary(str(experiment.path))
