import json
from pathlib import Path

import polars as pl
import pytest

from ashare_quant.backtest import BacktestConfig, CostModel
from ashare_quant.research import (
    WalkForwardConfig,
    WalkForwardMomentumFoldRun,
    WalkForwardMomentumRun,
    compare_walk_forward_experiments,
    persist_walk_forward_momentum_run,
    verify_walk_forward_experiment,
)
from ashare_quant.research.walk_forward import WalkForwardFold
from ashare_quant.strategies.momentum import MomentumConfig


COMMIT = "d14a2f920b62c5bd68cf60d335348064ac9997b9"


def make_run() -> WalkForwardMomentumRun:
    fold = WalkForwardFold(
        fold_id="fold_001",
        train_start="2020-01-02",
        train_end="2020-01-03",
        train_label_maturity="2020-01-03",
        validation_start="2020-01-06",
        validation_end="2020-01-07",
        validation_label_maturity="2020-01-07",
        test_start="2020-01-08",
        test_end="2020-01-09",
        train_sessions=2,
        validation_sessions=2,
        test_sessions=2,
        label_horizon_sessions=0,
        embargo_sessions=0,
        expanding_train=True,
    )
    daily = pl.DataFrame({
        "date": ["2020-01-08", "2020-01-09"],
        "nav": [100_100.0, 100_250.0],
        "daily_return": [0.001, 0.0014985],
    })
    orders = pl.DataFrame({
        "date": ["2020-01-09"],
        "instrument": ["600000.SH"],
        "side": ["buy"],
        "filled_qty": [100],
        "fill_price": [10.0],
        "fees": [1.2],
    })
    holdings = pl.DataFrame({
        "date": ["2020-01-08", "2020-01-09"],
        "instrument": ["600000.SH", "600000.SH"],
        "quantity": [100, 100],
        "market_value": [1_000.0, 1_010.0],
    })
    common_provenance = {
        "acceptance_report_id": "acceptance-synthetic-001",
        "acceptance_report_window": {"start": "2020-01-02", "end": "2020-01-09"},
        "universe_profile": "synthetic-test-universe",
        "source_revisions": {
            "daily_bars": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
            "adj_factors": {"revision_id": "hfq-r1", "contract_fingerprint": "hfq-contract"},
            "universe": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
            "trading_status": {"revision_id": "status-r1", "contract_fingerprint": "status-contract"},
            "trading_calendar": {"revision_id": "calendar-r1", "contract_fingerprint": "calendar-contract"},
            "price_limit_rules": {"revision_id": "limits-r1", "contract_fingerprint": "limits-contract", "source": "synthetic-provider"},
        },
        "source_receipts": [
            {"dataset": "daily_bars", "receipt": {"replayable": True, "receipt_id": "adapter-1", "dependencies": {
                "daily_bars": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
                "adj_factors": {"revision_id": "hfq-r1", "contract_fingerprint": "hfq-contract"},
                "trading_status": {"revision_id": "status-r1", "contract_fingerprint": "status-contract"},
                "trading_calendar": {"revision_id": "calendar-r1", "contract_fingerprint": "calendar-contract"},
            }}},
        ],
        "receipt_provenance": {"dependency_identities": {
            "daily_bars": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
            "adj_factors": {"revision_id": "hfq-r1", "contract_fingerprint": "hfq-contract"},
            "trading_status": {"revision_id": "status-r1", "contract_fingerprint": "status-contract"},
            "trading_calendar": {"revision_id": "calendar-r1", "contract_fingerprint": "calendar-contract"},
        }},
        "acceptance_source_receipts": {
            "historical_universe": {"replayable": True, "receipt_id": "u1"},
            "hfq": {"replayable": True, "receipt_id": "h1"},
        },
        "limit_evidence": {"revision": "limits-r1", "source_revision_identity": {
            "revision_id": "limits-r1", "contract_fingerprint": "limits-contract", "source": "synthetic-provider",
        }},
        "training_dataset_unlocked": False,
    }
    fold_provenance = {
        **common_provenance,
        "walk_forward": {
            "fold": fold.to_dict(),
            "data_cutoff_date": fold.test_end,
            "training_dataset_unlocked": False,
            "model_training_performed": False,
        },
    }
    fold_run = WalkForwardMomentumFoldRun(
        fold=fold,
        baseline_date="2020-01-07",
        baseline_nav=100_000.0,
        daily=daily,
        orders=orders,
        holdings=holdings,
        report={
            "initial_nav": 100_000.0,
            "final_nav": 100_250.0,
            "total_return": 0.0025,
            "cagr": 0.3,
            "annualized_volatility": 0.02,
            "sharpe_ratio": 0.5,
            "max_drawdown": -0.01,
            "costs": {"total_transaction_costs": 1.2},
        },
        provenance=fold_provenance,
    )
    return WalkForwardMomentumRun(
        folds=[fold_run],
        fold_table=pl.DataFrame({
            "fold_id": ["fold_001"],
            "test_start": ["2020-01-08"],
            "test_end": ["2020-01-09"],
            "test_sessions": [2],
            "total_return": [0.0025],
        }),
        oos_daily=daily,
        oos_orders=orders,
        oos_holdings=holdings,
        aggregate_report={
            "fold_count": 1,
            "out_of_sample_sessions": 2,
            "mean_fold_total_return": 0.0025,
            "median_fold_total_return": 0.0025,
            "positive_fold_ratio": 1.0,
            "mean_fold_cagr": 0.3,
            "mean_fold_sharpe": 0.5,
            "worst_fold_max_drawdown": -0.01,
            "fold_linked_total_return": 0.0025,
            "model_training_performed": False,
            "training_dataset_unlocked": False,
        },
    )


def persist(root: Path, *, run: WalkForwardMomentumRun | None = None) -> Path:
    return persist_walk_forward_momentum_run(
        run or make_run(),
        root=root,
        experiment_id="momentum-smoke-001",
        repository_commit=COMMIT,
        walk_forward_config=WalkForwardConfig(
            min_train_sessions=2,
            validation_sessions=2,
            test_sessions=2,
            label_horizon_sessions=0,
            embargo_sessions=0,
        ),
        strategy_config=MomentumConfig(top_n=1, lookback_sessions=2, skip_recent_sessions=0),
        backtest_config=BacktestConfig(
            initial_cash=100_000,
            top_n=1,
            costs=CostModel(minimum_commission=1.0),
        ),
        random_seed=17,
    )


def test_persist_writes_manifest_hashes_and_parquet_ledgers(tmp_path):
    run = make_run()
    destination = persist(tmp_path, run=run)

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["experiment_id"] == "momentum-smoke-001"
    assert manifest["repository_commit"] == COMMIT
    assert manifest["random_seed"] == 17
    assert manifest["configs"]["strategy"]["top_n"] == 1
    assert manifest["data_provenance"]["source_revisions"]["daily_bars"]["revision_id"] == "daily-r1"
    assert manifest["training_dataset_unlocked"] is False
    assert manifest["model_training_performed"] is False

    for name, frame in (
        ("fold_table", run.fold_table),
        ("oos_daily", run.oos_daily),
        ("oos_orders", run.oos_orders),
        ("oos_holdings", run.oos_holdings),
    ):
        artifact = manifest["artifacts"][name]
        saved_path = destination / artifact["file"]
        assert artifact["rows"] == frame.height
        assert len(artifact["sha256"]) == 64
        assert pl.read_parquet(saved_path).equals(frame)

    fold_manifest = json.loads((destination / "folds.json").read_text(encoding="utf-8"))
    assert fold_manifest[0]["fold"]["fold_id"] == "fold_001"
    assert fold_manifest[0]["provenance"]["walk_forward"]["data_cutoff_date"] == "2020-01-09"


def test_persist_never_overwrites_an_existing_experiment(tmp_path):
    destination = persist(tmp_path)
    original_manifest = (destination / "manifest.json").read_bytes()

    with pytest.raises(FileExistsError, match="will not be overwritten"):
        persist(tmp_path)

    assert (destination / "manifest.json").read_bytes() == original_manifest
    assert not list(tmp_path.glob(".momentum-smoke-001.tmp-*"))


def test_persist_rejects_training_unlocked_run(tmp_path):
    run = make_run()
    invalid = WalkForwardMomentumRun(
        folds=run.folds,
        fold_table=run.fold_table,
        oos_daily=run.oos_daily,
        oos_orders=run.oos_orders,
        oos_holdings=run.oos_holdings,
        aggregate_report={
            "model_training_performed": False,
            "training_dataset_unlocked": True,
        },
    )
    with pytest.raises(ValueError, match="training_dataset_unlocked=false"):
        persist(tmp_path, run=invalid)
    assert not (tmp_path / "momentum-smoke-001").exists()


@pytest.mark.parametrize("experiment_id", ["../escape", "has/slash", "", "."])
def test_persist_rejects_unsafe_experiment_ids(tmp_path, experiment_id):
    with pytest.raises(ValueError, match="experiment_id"):
        persist_walk_forward_momentum_run(
            make_run(),
            root=tmp_path,
            experiment_id=experiment_id,
            repository_commit=COMMIT,
            walk_forward_config={"min_train_sessions": 2},
            strategy_config={"top_n": 1},
            backtest_config={"initial_cash": 100_000},
        )


def test_persist_requires_a_git_commit_sha(tmp_path):
    with pytest.raises(ValueError, match="repository_commit"):
        persist_walk_forward_momentum_run(
            make_run(),
            root=tmp_path,
            experiment_id="bad-commit",
            repository_commit="not-a-sha",
            walk_forward_config={"min_train_sessions": 2},
            strategy_config={"top_n": 1},
            backtest_config={"initial_cash": 100_000},
        )



def test_persist_rejects_combined_ledgers_that_do_not_match_fold_outputs(tmp_path):
    run = make_run()
    invalid = WalkForwardMomentumRun(
        folds=run.folds,
        fold_table=run.fold_table,
        oos_daily=run.oos_daily.head(1),
        oos_orders=run.oos_orders,
        oos_holdings=run.oos_holdings,
        aggregate_report=run.aggregate_report,
    )
    with pytest.raises(ValueError, match="oos_daily row count"):
        persist(tmp_path, run=invalid)
    assert not (tmp_path / "momentum-smoke-001").exists()


def test_persist_rejects_missing_source_revision(tmp_path):
    run = make_run()
    fold_run = run.folds[0]
    invalid_provenance = {
        **fold_run.provenance,
        "source_revisions": {
            key: value
            for key, value in fold_run.provenance["source_revisions"].items()
            if key != "price_limit_rules"
        },
    }
    invalid_fold = WalkForwardMomentumFoldRun(
        fold=fold_run.fold,
        baseline_date=fold_run.baseline_date,
        baseline_nav=fold_run.baseline_nav,
        daily=fold_run.daily,
        orders=fold_run.orders,
        holdings=fold_run.holdings,
        report=fold_run.report,
        provenance=invalid_provenance,
    )
    invalid = WalkForwardMomentumRun(
        folds=[invalid_fold],
        fold_table=run.fold_table,
        oos_daily=run.oos_daily,
        oos_orders=run.oos_orders,
        oos_holdings=run.oos_holdings,
        aggregate_report=run.aggregate_report,
    )
    with pytest.raises(ValueError, match="price_limit_rules"):
        persist(tmp_path, run=invalid)
    assert not (tmp_path / "momentum-smoke-001").exists()



def test_persist_rejects_combined_ledgers_with_different_values(tmp_path):
    run = make_run()
    altered_daily = run.oos_daily.with_columns(
        (pl.col("nav") * 2).alias("nav")
    )
    invalid = WalkForwardMomentumRun(
        folds=run.folds,
        fold_table=run.fold_table,
        oos_daily=altered_daily,
        oos_orders=run.oos_orders,
        oos_holdings=run.oos_holdings,
        aggregate_report=run.aggregate_report,
    )
    with pytest.raises(ValueError, match="oos_daily contents"):
        persist(tmp_path, run=invalid)
    assert not (tmp_path / "momentum-smoke-001").exists()



def persist_variant(
    root: Path,
    experiment_id: str,
    *,
    strategy_config: MomentumConfig,
    backtest_config: BacktestConfig | None = None,
    run: WalkForwardMomentumRun | None = None,
) -> Path:
    return persist_walk_forward_momentum_run(
        run or make_run(),
        root=root,
        experiment_id=experiment_id,
        repository_commit=COMMIT,
        walk_forward_config=WalkForwardConfig(
            min_train_sessions=2,
            validation_sessions=2,
            test_sessions=2,
            label_horizon_sessions=0,
            embargo_sessions=0,
        ),
        strategy_config=strategy_config,
        backtest_config=backtest_config or BacktestConfig(
            initial_cash=100_000,
            top_n=1,
            costs=CostModel(minimum_commission=1.0),
        ),
    )


def test_verify_checks_hashes_and_returns_parquet_ledgers(tmp_path):
    destination = persist(tmp_path)
    verified = verify_walk_forward_experiment(destination)

    assert verified.manifest["experiment_id"] == "momentum-smoke-001"
    assert verified.fold_table.height == 1
    assert verified.oos_daily.height == 2
    assert verified.oos_orders.height == 1
    assert verified.oos_holdings.height == 2
    assert verified.folds[0]["fold"]["fold_id"] == "fold_001"


def test_verify_rejects_tampered_artifact(tmp_path):
    destination = persist(tmp_path)
    artifact = destination / "oos_daily.parquet"
    artifact.write_bytes(artifact.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_walk_forward_experiment(destination)


def test_compare_allows_strategy_variants_on_identical_data_and_execution(tmp_path):
    first = persist_variant(
        tmp_path,
        "momentum-126-21",
        strategy_config=MomentumConfig(top_n=1, lookback_sessions=2, skip_recent_sessions=0),
    )
    second = persist_variant(
        tmp_path,
        "momentum-3-1",
        strategy_config=MomentumConfig(top_n=1, lookback_sessions=3, skip_recent_sessions=1),
    )

    comparison = compare_walk_forward_experiments([first, second])

    assert comparison.summary.height == 2
    assert comparison.by_fold.height == 2
    assert comparison.summary["experiment_id"].to_list() == [
        "momentum-126-21",
        "momentum-3-1",
    ]
    assert comparison.summary["strategy_config"].n_unique() == 2


def test_compare_rejects_different_source_revisions(tmp_path):
    first = persist_variant(
        tmp_path,
        "same-data-r1",
        strategy_config=MomentumConfig(top_n=1, lookback_sessions=2, skip_recent_sessions=0),
    )
    run = make_run()
    fold_run = run.folds[0]
    altered_provenance = {
        **fold_run.provenance,
        "source_revisions": {
            **fold_run.provenance["source_revisions"],
            "daily_bars": {"revision_id": "daily-r2", "contract_fingerprint": "daily-contract"},
            "universe": {"revision_id": "daily-r2", "contract_fingerprint": "daily-contract"},
        },
    }
    altered_dependencies = {
        **fold_run.provenance["receipt_provenance"]["dependency_identities"],
        "daily_bars": {"revision_id": "daily-r2", "contract_fingerprint": "daily-contract"},
    }
    altered_provenance["receipt_provenance"] = {"dependency_identities": altered_dependencies}
    altered_provenance["source_receipts"] = [{
        **record,
        "receipt": {**record["receipt"], "dependencies": altered_dependencies},
    } for record in fold_run.provenance["source_receipts"]]
    altered_fold = WalkForwardMomentumFoldRun(
        fold=fold_run.fold,
        baseline_date=fold_run.baseline_date,
        baseline_nav=fold_run.baseline_nav,
        daily=fold_run.daily,
        orders=fold_run.orders,
        holdings=fold_run.holdings,
        report=fold_run.report,
        provenance=altered_provenance,
    )
    altered_run = WalkForwardMomentumRun(
        folds=[altered_fold],
        fold_table=run.fold_table,
        oos_daily=run.oos_daily,
        oos_orders=run.oos_orders,
        oos_holdings=run.oos_holdings,
        aggregate_report=run.aggregate_report,
    )
    second = persist_variant(
        tmp_path,
        "same-data-r2",
        strategy_config=MomentumConfig(top_n=1, lookback_sessions=3, skip_recent_sessions=1),
        run=altered_run,
    )

    with pytest.raises(ValueError, match="source revision daily_bars does not match retained receipt identity"):
        compare_walk_forward_experiments([first, second])


def test_compare_rejects_different_cost_configs(tmp_path):
    first = persist_variant(
        tmp_path,
        "costs-a",
        strategy_config=MomentumConfig(top_n=1, lookback_sessions=2, skip_recent_sessions=0),
    )
    second = persist_variant(
        tmp_path,
        "costs-b",
        strategy_config=MomentumConfig(top_n=1, lookback_sessions=3, skip_recent_sessions=1),
        backtest_config=BacktestConfig(
            initial_cash=200_000,
            top_n=1,
            costs=CostModel(minimum_commission=1.0),
        ),
    )

    with pytest.raises(ValueError, match="backtest/cost configurations"):
        compare_walk_forward_experiments([first, second])
