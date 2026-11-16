from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl

from ashare_quant.data.metadata_inspection import inspect_cnequity_metadata


def _instrument_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["600000.SH", "000001.SZ", "000002.SZ", "BAD"],
            "name": ["A", "B", "C", "D"],
            "exchange": ["SH", "SZ", "SZ", "SH"],
            "asset_type": ["stock", "stock", "stock", "stock"],
            "list_date": [
                date(1999, 11, 10),
                date(1991, 4, 3),
                date(1991, 1, 29),
                None,
            ],
            "delist_date": [None, None, None, None],
            "source": ["tdx", "baostock", "baostock", "sina"],
            "data_version": ["v1", "v2", "v2", "v3"],
            "fetched_at": [None, None, None, None],
        }
    )


def test_inspection_reports_schema_coverage_and_keeps_listing_pit_closed() -> None:
    frames = {
        "instruments": _instrument_frame(),
        "delisting_events": pl.DataFrame(
            {
                "symbol": ["600000.SH", "999999.SZ"],
                "first_trade_date": [date(1999, 11, 10), date(2001, 1, 1)],
                "last_trade_date": [date(2020, 1, 1), date(2020, 1, 1)],
            }
        ),
    }
    calls: list[tuple[str, dict[str, object]]] = []

    def loader(dataset: str, **kwargs: object) -> SimpleNamespace:
        calls.append((dataset, kwargs))
        return SimpleNamespace(
            frame=frames[dataset],
            receipt={
                "dataset": dataset,
                "receipt_id": f"receipt-{dataset}",
                "replayable": dataset == "instruments",
                "unpinned_datasets": [] if dataset == "instruments" else [dataset],
                "coverage": {"completeness": "not_assessed"},
            },
        )

    schemas = {
        "instruments": {
            "symbol": pl.String,
            "list_date": pl.Date,
            "delist_date": pl.Date,
            "exchange": pl.String,
            "source": pl.String,
        },
        "delisting_events": {"symbol": pl.String, "last_trade_date": pl.Date},
    }
    report = inspect_cnequity_metadata(
        "fake-lake",
        loader=loader,
        schema_reader=schemas.__getitem__,
        as_of=date(2026, 11, 14),
    )

    instruments = report["datasets"]["instruments"]
    assert instruments["status"] == "READ"
    assert instruments["row_count"] == 4
    assert instruments["missing_contract_fields"] == []
    assert instruments["symbols"]["invalid_format_rows"] == 1
    assert instruments["symbols"]["duplicate_symbol_groups"] == 0
    assert instruments["temporal_evidence_columns"]["available_at"]["present"] is False
    assert instruments["listing_date_admission"]["status"] == "NOT_ADMITTED"
    assert instruments["receipt"]["coverage"]["completeness"] == "not_assessed"
    assert report["cross_checks"]["delisting_event_symbols"]["symbols_missing_from_instruments"] == 1
    assert report["historical_universe_admitted"] is False
    assert all(kwargs["require_replayable"] is False for _, kwargs in calls)
    expected_root = str(Path("fake-lake").resolve())
    assert all(kwargs["data_root"] == expected_root for _, kwargs in calls)


def test_inspection_preserves_dataset_read_errors() -> None:
    def loader(dataset: str, **kwargs: object) -> SimpleNamespace:
        if dataset == "instruments":
            raise FileNotFoundError("dataset missing")
        return SimpleNamespace(
            frame=pl.DataFrame({"symbol": ["600000.SH"]}),
            receipt={"dataset": dataset, "replayable": False},
        )

    report = inspect_cnequity_metadata(
        "fake-lake",
        loader=loader,
        schema_reader=lambda _: {"symbol": pl.String},
        as_of=date(2026, 11, 14),
    )
    assert report["datasets"]["instruments"]["status"] == "ERROR"
    assert "dataset missing" in report["datasets"]["instruments"]["error"]
    assert report["overall_read_status"] == "ERROR"


def test_missing_datasets_are_not_misreported_as_reader_errors() -> None:
    def loader(dataset: str, **kwargs: object) -> SimpleNamespace:
        raise RuntimeError(
            f"ReaderError: no parquet data for dataset '{dataset}' under lake/{dataset}"
        )

    report = inspect_cnequity_metadata(
        "fake-lake",
        loader=loader,
        schema_reader=lambda _: {"symbol": pl.String},
        as_of=date(2026, 11, 14),
    )

    assert report["datasets"]["instruments"]["status"] == "MISSING"
    assert (
        report["datasets"]["instruments"]["dataset_role"]
        == "required_for_historical_universe"
    )
    assert "plan_cnequity_instruments_backfill.py" in report["datasets"]["instruments"]["next_step"]
    assert report["datasets"]["delisting_events"]["status"] == "MISSING"
    assert (
        report["datasets"]["delisting_events"]["dataset_role"]
        == "derived_supplementary_dataset"
    )
    assert "Do not run" in report["datasets"]["delisting_events"]["next_step"]
    assert report["overall_read_status"] == "MISSING"


def test_default_inspection_uses_project_adapter_and_supplied_data_root(monkeypatch) -> None:
    from ashare_quant.data import cne_adapter

    frames = {
        "instruments": _instrument_frame(),
        "delisting_events": pl.DataFrame(
            {
                "symbol": ["000002.SZ"],
                "first_trade_date": [date(1991, 1, 29)],
                "last_trade_date": [date(2024, 1, 2)],
            }
        ),
    }
    calls: list[tuple[str, str]] = []

    class FakeAdapter:
        def __init__(self, *, data_root: str | Path) -> None:
            self.data_root = str(data_root)

        def read_diagnostic_dataset(
            self,
            dataset: str,
            *,
            data_root: str | Path | None = None,
            require_replayable: bool = False,
        ) -> SimpleNamespace:
            assert str(data_root) == self.data_root
            assert require_replayable is False
            calls.append((dataset, self.data_root))
            return SimpleNamespace(
                frame=frames[dataset],
                receipt={
                    "dataset": dataset,
                    "receipt_id": f"diagnostic-{dataset}",
                    "replayable": False,
                    "coverage": {"completeness": "not_assessed"},
                },
            )

        @staticmethod
        def dataset_schema(dataset: str) -> dict[str, pl.DataType]:
            return {
                "instruments": {
                    "symbol": pl.String,
                    "list_date": pl.Date,
                    "delist_date": pl.Date,
                },
                "delisting_events": {
                    "symbol": pl.String,
                    "first_trade_date": pl.Date,
                    "last_trade_date": pl.Date,
                },
            }[dataset]

    monkeypatch.setattr(cne_adapter, "CNEquityAdapter", FakeAdapter)
    report = inspect_cnequity_metadata(r"F:\cnequity", as_of=date(2026, 11, 14))

    assert report["datasets"]["instruments"]["status"] == "READ"
    assert report["datasets"]["delisting_events"]["status"] == "READ"
    assert calls == [
        ("instruments", str(Path(r"F:\cnequity").resolve())),
        ("delisting_events", str(Path(r"F:\cnequity").resolve())),
    ]
    assert report["datasets"]["instruments"]["receipt"]["replayable"] is False
    assert report["historical_universe_admitted"] is False


def test_listing_date_missingness_is_attributed_by_asset_exchange_and_source() -> None:
    instruments = pl.DataFrame(
        {
            "symbol": [
                "600000.SH", "000001.SZ", "430001.BJ",
                "BAD", "510300.SH", "123456.SZ",
            ],
            "name": ["A", "B", "C", "D", "ETF", "CDR"],
            "exchange": ["SH", "SZ", "BJ", "SH", "SH", "SZ"],
            "asset_type": ["stock", "stock", "stock", "stock", "etf", "cdr"],
            "list_date": [
                date(1999, 11, 10), None, None, None, None, date(2020, 1, 2),
            ],
            "delist_date": [None, None, None, None, None, None],
            "source": ["tdx", "baostock", "bse", "sina", "tdx", "sina"],
        }
    )
    frames = {
        "instruments": instruments,
        "delisting_events": pl.DataFrame(schema={"symbol": pl.String}),
    }

    def loader(dataset: str, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            frame=frames[dataset],
            receipt={
                "dataset": dataset,
                "receipt_id": f"receipt-{dataset}",
                "replayable": dataset == "instruments",
                "unpinned_datasets": [] if dataset == "instruments" else [dataset],
                "coverage": {"completeness": "not_assessed"},
            },
        )

    report = inspect_cnequity_metadata(
        "fake-lake",
        loader=loader,
        schema_reader=lambda _: {"symbol": pl.String},
        as_of=date(2026, 11, 16),
    )

    diagnostic = report["datasets"]["instruments"]["listing_date_missingness"]
    assert diagnostic["status"] == "AVAILABLE"
    assert diagnostic["total_rows"] == 6
    assert diagnostic["missing_or_unparseable_rows"] == 4
    assert diagnostic["stock_rows"] == 4
    assert diagnostic["stock_missing_list_date_rows"] == 3
    assert diagnostic["a_share_stock_rows"] == 3
    assert diagnostic["a_share_stock_missing_list_date_rows"] == 2
    assert diagnostic["unqualified_stock_missing_list_date_rows"] == 1
    assert diagnostic["non_stock_missing_list_date_rows"] == 1
    assert {
        row["symbol"] for row in diagnostic["a_share_stock_missing_records"]
    } == {"000001.SZ", "430001.BJ"}

    asset_groups = {
        item["value"]: item
        for item in diagnostic["missing_by_asset_type"]["groups"]
    }
    assert asset_groups["stock"]["rows"] == 4
    assert asset_groups["stock"]["missing_list_date_rows"] == 3
    assert asset_groups["etf"]["missing_list_date_rows"] == 1

    listing_events = report["cross_checks"]["listing_event_evidence"]
    assert listing_events["status"] == "NOT_CONNECTED"
    assert listing_events["expected_stock_symbols"] == 3
    assert report["historical_universe_admitted"] is False


def test_listing_event_evidence_without_source_identity_is_not_validated() -> None:
    frames = {
        "instruments": _instrument_frame(),
        "delisting_events": pl.DataFrame(schema={"symbol": pl.String}),
    }

    def loader(dataset: str, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            frame=frames[dataset],
            receipt={"dataset": dataset, "receipt_id": f"receipt-{dataset}"},
        )

    report = inspect_cnequity_metadata(
        "fake-lake",
        loader=loader,
        schema_reader=lambda _: {"symbol": pl.String},
        as_of=date(2026, 11, 16),
        listing_event_evidence=pl.DataFrame({"instrument": ["600000.SH"]}),
    )

    listing_events = report["cross_checks"]["listing_event_evidence"]
    assert listing_events["status"] == "NOT_VALIDATED"
    assert listing_events["evidence_rows"] == 1
    assert "without receipt-derived source identities" in listing_events["reason"]
    assert report["historical_universe_admitted"] is False


def test_invalid_listing_event_evidence_fails_closed() -> None:
    frames = {
        "instruments": _instrument_frame(),
        "delisting_events": pl.DataFrame(schema={"symbol": pl.String}),
    }

    def loader(dataset: str, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            frame=frames[dataset],
            receipt={"dataset": dataset, "receipt_id": f"receipt-{dataset}"},
        )

    report = inspect_cnequity_metadata(
        "fake-lake",
        loader=loader,
        schema_reader=lambda _: {"symbol": pl.String},
        as_of=date(2026, 11, 16),
        listing_event_evidence=pl.DataFrame({"instrument": ["600000.SH"]}),
        listing_event_source_identities={},
    )

    listing_events = report["cross_checks"]["listing_event_evidence"]
    assert listing_events["status"] == "INVALID"
    assert "missing columns" in listing_events["validation_error"]
    assert report["historical_universe_admitted"] is False
