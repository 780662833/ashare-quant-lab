"""Bounded, read-only probe for the unofficially documented CNINFO search endpoint.

This module is a source-research tool only. It does not create production
security metadata, download original announcement files, or prove archive
completeness or PIT eligibility.
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

SEARCH_PAGE_URL = (
    "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch"
    "?url=disclosure/list/search"
)
QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
SHANGHAI = ZoneInfo("Asia/Shanghai")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_REQUIRED_ANNOUNCEMENT_FIELDS = (
    "secCode",
    "announcementId",
    "announcementTitle",
    "announcementTime",
    "adjunctUrl",
)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _validate_request(
    *,
    start_date: str,
    end_date: str,
    column: str,
    search_key: str,
    page_size: int,
    max_pages: int,
    min_interval_seconds: float,
) -> tuple[date, date]:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError("start_date and end_date must use YYYY-MM-DD") from exc
    if start.isoformat() != start_date or end.isoformat() != end_date:
        raise ValueError("start_date and end_date must use canonical YYYY-MM-DD")
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    if (end - start).days > 30:
        raise ValueError("a probe date window must not exceed 31 calendar days")
    if column not in {"sse", "szse"}:
        raise ValueError("column must be sse or szse")
    if not isinstance(search_key, str) or not search_key.strip():
        raise ValueError("search_key must be a non-empty keyword")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    if not 1 <= max_pages <= 10:
        raise ValueError("max_pages must be between 1 and 10")
    if min_interval_seconds < 0.5:
        raise ValueError("min_interval_seconds must be at least 0.5")
    return start, end


def _publication_time_observation(value: Any) -> dict[str, Any]:
    """Decode a timestamp-shaped field without claiming it is a release instant."""
    raw = str(value) if value is not None else None
    result: dict[str, Any] = {
        "raw": raw,
        "local_datetime": None,
        "local_date": None,
        "timestamp_semantics": "unknown",
    }
    if value is None:
        return result
    try:
        numeric = int(value)
        if numeric <= 0:
            return result
        # The reverse-engineered client documents milliseconds. Do not silently
        # reinterpret smaller numbers as seconds.
        observed = datetime.fromtimestamp(numeric / 1000, tz=SHANGHAI)
    except (TypeError, ValueError, OverflowError, OSError):
        return result
    result["local_datetime"] = observed.isoformat()
    result["local_date"] = observed.date().isoformat()
    result["timestamp_semantics"] = (
        "midnight_date_stamp_suspected"
        if observed.time().hour == 0
        and observed.time().minute == 0
        and observed.time().second == 0
        else "intraday_value_unverified"
    )
    return result


def _make_opener() -> Any:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _read_response(opener: Any, request: urllib.request.Request, timeout: float) -> tuple[bytes, int, str]:
    with opener.open(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200))
        final_url = response.geturl() if hasattr(response, "geturl") else request.full_url
        return response.read(), status, str(final_url)


def _official_host(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme.lower() == "https"
        and host in {"www.cninfo.com.cn", "static.cninfo.com.cn"}
        and parsed.username is None
        and parsed.password is None
    )


def _announcement_identity(row: dict[str, Any]) -> str | None:
    value = row.get("announcementId")
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def probe_cninfo_announcement_api(
    *,
    start_date: str,
    end_date: str,
    column: str,
    plate: str,
    search_key: str,
    output_dir: str | Path,
    page_size: int = 30,
    max_pages: int = 2,
    min_interval_seconds: float = 1.0,
    timeout_seconds: float = 20.0,
    opener: Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Capture a bounded metadata query and its raw JSON pages.

    The supplied opener/sleep function are injectable for unit tests. No
    announcement PDF is downloaded, and a complete page traversal is only
    complete for the response's own query counters, not proof of source-wide
    historical coverage.
    """
    _validate_request(
        start_date=start_date,
        end_date=end_date,
        column=column,
        search_key=search_key,
        page_size=page_size,
        max_pages=max_pages,
        min_interval_seconds=min_interval_seconds,
    )
    if not isinstance(plate, str):
        raise ValueError("plate must be a string; use the value for the board under test")

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    client = opener if opener is not None else _make_opener()
    report: dict[str, Any] = {
        "schema_version": 1,
        "probe": "cninfo_reverse_engineered_announcement_query",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "ERROR",
        "request": {
            "start_date": start_date,
            "end_date": end_date,
            "column": column,
            "plate": plate,
            "search_key": search_key,
            "page_size": page_size,
            "max_pages": max_pages,
            "min_interval_seconds": min_interval_seconds,
        },
        "endpoint": QUERY_URL,
        "search_page": SEARCH_PAGE_URL,
        "safety": {
            "read_only": True,
            "bounded_date_window": True,
            "bounded_page_count": True,
            "local_data_lake_access": False,
            "production_dataset_write": False,
            "announcement_documents_downloaded": False,
            "historical_universe_completeness_proven": False,
            "pit_admitted": False,
            "training_unlocked": False,
            "backtest_admitted": False,
        },
        "bootstrap": {},
        "pages": [],
        "query_counters": {
            "reported_total_pages": None,
            "reported_total_records": None,
            "captured_pages": 0,
            "captured_records": 0,
            "unique_announcement_ids": 0,
            "duplicate_announcement_ids": [],
            "rows_missing_identity_fields": [],
            "midnight_timestamp_rows": 0,
            "intraday_timestamp_rows_unverified": 0,
        },
        "anomalies": [],
        "page_traversal_complete_for_query": False,
        "source_coverage_proven": False,
        "notes": [
            "The query endpoint and parameter schema are reverse-engineered research leads, not an official CNINFO API contract.",
            "A complete traversal covers only the server-reported query result and does not prove historical archive completeness.",
            "announcementTime is not accepted as an exact first-publication timestamp by this probe.",
            "Raw response hashes are capture identities, not source revision receipts or immutable-document proofs.",
        ],
    }

    try:
        bootstrap_request = urllib.request.Request(
            SEARCH_PAGE_URL,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
            method="GET",
        )
        bootstrap_bytes, bootstrap_status, bootstrap_url = _read_response(
            client, bootstrap_request, timeout_seconds
        )
        if not _official_host(bootstrap_url):
            raise ValueError("search-page bootstrap redirected outside the official CNINFO host")
        bootstrap_path = target_dir / "search_page_bootstrap.html"
        bootstrap_path.write_bytes(bootstrap_bytes)
        report["bootstrap"] = {
            "status_code": bootstrap_status,
            "final_url": bootstrap_url,
            "bytes": len(bootstrap_bytes),
            "sha256": hashlib.sha256(bootstrap_bytes).hexdigest(),
            "capture_file": bootstrap_path.name,
        }
        if bootstrap_status != 200:
            raise RuntimeError(f"search-page bootstrap returned HTTP {bootstrap_status}")

        expected_pages: int | None = None
        expected_records: int | None = None
        all_ids: list[str] = []
        previous_request_at: float | None = None
        for page_num in range(1, max_pages + 1):
            if expected_pages is not None and page_num > expected_pages:
                break
            if previous_request_at is not None:
                elapsed = time.monotonic() - previous_request_at
                if elapsed < min_interval_seconds:
                    sleep_fn(min_interval_seconds - elapsed)

            form = {
                "pageNum": str(page_num),
                "pageSize": str(page_size),
                "column": column,
                "tabName": "fulltext",
                "plate": plate,
                "stock": "",
                "searchkey": search_key,
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": f"{start_date}~{end_date}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            encoded = urllib.parse.urlencode(form).encode("utf-8")
            request = urllib.request.Request(
                QUERY_URL,
                data=encoded,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Referer": SEARCH_PAGE_URL,
                    "Origin": "https://www.cninfo.com.cn",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                method="POST",
            )
            previous_request_at = time.monotonic()
            raw_bytes, status, final_url = _read_response(client, request, timeout_seconds)
            if not _official_host(final_url):
                raise ValueError("query request redirected outside the official CNINFO host")
            page_path = target_dir / f"page_{page_num:03d}.json"
            page_path.write_bytes(raw_bytes)
            page_report: dict[str, Any] = {
                "page_num": page_num,
                "status_code": status,
                "final_url": final_url,
                "bytes": len(raw_bytes),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "capture_file": page_path.name,
            }
            report["pages"].append(page_report)
            if status != 200:
                raise RuntimeError(f"query page {page_num} returned HTTP {status}")
            try:
                payload = json.loads(raw_bytes.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"query page {page_num} did not return UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"query page {page_num} JSON root must be an object")

            announcements = payload.get("announcements")
            if not isinstance(announcements, list):
                raise ValueError(f"query page {page_num} has no announcements array")
            total_pages_raw = _as_int(payload.get("totalpages"))
            total_records_raw = _as_int(payload.get("totalRecordNum"))
            has_more = payload.get("hasMore")
            if total_pages_raw is None and total_records_raw is None:
                raise ValueError(
                    f"query page {page_num} has neither totalpages nor totalRecordNum"
                )
            calculated_pages = (
                (total_records_raw + page_size - 1) // page_size
                if total_records_raw is not None and total_records_raw > 0
                else 0
            )
            reported_pages = (
                total_pages_raw if total_pages_raw is not None else calculated_pages
            )
            if total_pages_raw is not None and total_records_raw is not None:
                if total_records_raw > 0 and total_pages_raw == 0:
                    report["anomalies"].append(
                        "totalpages is zero while totalRecordNum is positive"
                    )
                if calculated_pages > total_pages_raw:
                    report["anomalies"].append(
                        "totalpages is smaller than the page count implied by totalRecordNum"
                    )
            if expected_pages is None:
                expected_pages = reported_pages
                expected_records = total_records_raw
                report["query_counters"]["reported_total_pages"] = expected_pages
                report["query_counters"]["reported_total_records"] = expected_records
            else:
                if reported_pages != expected_pages:
                    report["anomalies"].append(
                        f"reported page count changed from {expected_pages} to {reported_pages} on page {page_num}"
                    )
                if (
                    total_records_raw is not None
                    and expected_records is not None
                    and total_records_raw != expected_records
                ):
                    report["anomalies"].append(
                        f"reported record count changed from {expected_records} to {total_records_raw} on page {page_num}"
                    )

            page_report["record_count"] = len(announcements)
            page_report["reported_total_pages"] = reported_pages
            page_report["reported_total_records"] = total_records_raw
            page_report["has_more"] = has_more
            missing_identity_rows: list[dict[str, Any]] = []
            for row_index, row in enumerate(announcements):
                if not isinstance(row, dict):
                    missing_identity_rows.append({
                        "page_num": page_num,
                        "row_index": row_index,
                        "reason": "announcement row is not an object",
                    })
                    continue
                missing_fields = [
                    field for field in _REQUIRED_ANNOUNCEMENT_FIELDS
                    if row.get(field) in (None, "")
                ]
                if missing_fields:
                    missing_identity_rows.append({
                        "page_num": page_num,
                        "row_index": row_index,
                        "missing_fields": missing_fields,
                    })
                announcement_id = _announcement_identity(row)
                if announcement_id is not None:
                    all_ids.append(announcement_id)
                time_observation = _publication_time_observation(
                    row.get("announcementTime")
                )
                if time_observation["timestamp_semantics"] == "midnight_date_stamp_suspected":
                    report["query_counters"]["midnight_timestamp_rows"] += 1
                elif time_observation["timestamp_semantics"] == "intraday_value_unverified":
                    report["query_counters"]["intraday_timestamp_rows_unverified"] += 1
            page_report["rows_missing_identity_fields"] = len(missing_identity_rows)
            report["query_counters"]["rows_missing_identity_fields"].extend(
                missing_identity_rows
            )
            report["query_counters"]["captured_records"] += len(announcements)
            if has_more is False and page_num < (expected_pages or 0):
                report["anomalies"].append(
                    f"hasMore=false before reported final page {expected_pages}"
                )
                break

        reported_pages_final = report["query_counters"]["reported_total_pages"]
        captured_pages = len(report["pages"])
        report["query_counters"]["captured_pages"] = captured_pages
        duplicates = sorted(
            value for value, count in __import__("collections").Counter(all_ids).items()
            if count > 1
        )
        report["query_counters"]["unique_announcement_ids"] = len(set(all_ids))
        report["query_counters"]["duplicate_announcement_ids"] = duplicates
        if duplicates:
            report["anomalies"].append("duplicate announcementId values appeared across captured pages")
        page_complete = (
            isinstance(reported_pages_final, int)
            and captured_pages == reported_pages_final
            and not any("hasMore=false before" in item for item in report["anomalies"])
        )
        report["page_traversal_complete_for_query"] = page_complete
        if not page_complete:
            report["status"] = "PARTIAL"
            report["anomalies"].append(
                "page traversal is partial; a bounded probe must not claim query completeness"
            )
        elif report["query_counters"]["captured_records"] == 0:
            report["status"] = "NO_RESULTS"
        else:
            report["status"] = "COMPLETE_FOR_QUERY"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        report["status"] = "ERROR"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["page_traversal_complete_for_query"] = False

    report_path = target_dir / "probe_report.json"
    report["report_file"] = report_path.name
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def render_probe_plan(
    *,
    start_date: str,
    end_date: str,
    column: str,
    plate: str,
    search_key: str,
    page_size: int,
    max_pages: int,
    min_interval_seconds: float,
) -> str:
    """Return a no-network plan for the requested bounded probe."""
    _validate_request(
        start_date=start_date,
        end_date=end_date,
        column=column,
        search_key=search_key,
        page_size=page_size,
        max_pages=max_pages,
        min_interval_seconds=min_interval_seconds,
    )
    return json.dumps(
        {
            "mode": "PLAN_ONLY_NO_NETWORK",
            "query_url": QUERY_URL,
            "request": {
                "start_date": start_date,
                "end_date": end_date,
                "column": column,
                "plate": plate,
                "search_key": search_key,
                "page_size": page_size,
                "max_pages": max_pages,
                "min_interval_seconds": min_interval_seconds,
            },
            "writes": "Only the report and raw responses under the selected probe output directory after --execute.",
            "production_admitted": False,
        },
        ensure_ascii=False,
        indent=2,
    )
