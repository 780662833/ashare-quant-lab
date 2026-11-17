"""Bounded, read-only capture of SZSE's exchange-issued 本所公告 HTML archive.

The archive route and pagination convention are observed public-page behavior,
not an official machine-readable API contract. This module deliberately makes
no claim about historical archive completeness and never downloads attachments.
"""
from __future__ import annotations

import hashlib
import html
import http.cookiejar
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

LISTING_URL = "https://www.szse.cn/disclosure/notice/index.html"
SZSE_HOST = "www.szse.cn"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_MAX_HTML_BYTES = 20 * 1024 * 1024
_ARTICLE_PATH = re.compile(
    r"^/(?:www/)?disclosure/notice/t(?P<date>\d{8})_(?P<record_id>\d+)\.html$",
    re.IGNORECASE,
)
_INDEX_PATH = re.compile(
    r"^/(?:www/)?disclosure/notice/index(?:_(?P<page>\d+))?\.html$",
    re.IGNORECASE,
)
_DATE_TEXT = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_CUR_HREF = re.compile(r"""curHref\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)
_CUR_TITLE = re.compile(r"""curTitle\s*=\s*['"]([^'"]*)['"]""", re.IGNORECASE)


def _validate_https_szse_url(value: str, *, article: bool = False) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("SZSE URL must be a valid HTTPS URL") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or host != SZSE_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("only canonical HTTPS URLs on www.szse.cn are allowed")
    if article and not _ARTICLE_PATH.fullmatch(parsed.path):
        raise ValueError("article URL must use the SZSE 本所公告 tYYYYMMDD_ID.html path")
    if not article and not _INDEX_PATH.fullmatch(parsed.path):
        raise ValueError("listing URL must use the SZSE 本所公告 index[_N].html path")
    return value


def _index_url(start_page: int, offset: int) -> str:
    page = start_page + offset
    path = "/disclosure/notice/index.html" if page == 0 else (
        f"/disclosure/notice/index_{page}.html"
    )
    return f"https://{SZSE_HOST}{path}"


class _ListingHTMLParser(HTMLParser):
    """Extract article references from list items and their inline JS metadata."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, Any]] = []
        self._li_stack: list[dict[str, Any]] = []
        self._anchor: dict[str, str] | None = None
        self._script_depth = 0
        self._script_buffer: list[str] = []
        self._global_links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        tag = tag.lower()
        if tag == "li":
            self._li_stack.append({"text": [], "scripts": [], "links": []})
        elif tag == "a":
            self._anchor = {"href": attr_map.get("href") or "", "text": ""}
        elif tag == "script":
            self._script_depth += 1
            self._script_buffer = []

    def handle_data(self, data: str) -> None:
        if self._li_stack:
            for item in self._li_stack:
                item["text"].append(data)
        if self._anchor is not None:
            self._anchor["text"] += data
        if self._script_depth:
            self._script_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._anchor is not None:
            link = self._anchor
            self._anchor = None
            if self._li_stack:
                self._li_stack[-1]["links"].append(link)
            else:
                self._global_links.append(link)
        elif tag == "script" and self._script_depth:
            self._script_depth -= 1
            script = "".join(self._script_buffer)
            if self._li_stack:
                for item in self._li_stack:
                    item["scripts"].append(script)
            self._script_buffer = []
        elif tag == "li" and self._li_stack:
            item = self._li_stack.pop()
            self.items.append(item)


class _DocumentHTMLParser(HTMLParser):
    """Extract visible text and the HTML title while the raw page is retained."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._in_title:
            self.title_parts.append(data)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _normalized_article_url(raw_url: str, base_url: str) -> str | None:
    candidate = html.unescape(raw_url.strip()).replace(r"\/", "/")
    if not candidate:
        return None
    absolute = urllib.parse.urljoin(base_url, candidate)
    try:
        _validate_https_szse_url(absolute, article=True)
    except ValueError:
        return None
    return absolute


def extract_szse_notice_records(raw_html: str, page_url: str) -> list[dict[str, Any]]:
    """Extract official notice links from one captured 本所公告 listing page."""
    _validate_https_szse_url(page_url)
    parser = _ListingHTMLParser()
    parser.feed(raw_html)
    parser.close()

    discovered: dict[str, dict[str, Any]] = {}
    source_items = list(parser.items)
    if parser._global_links:
        source_items.append(
            {
                "text": [],
                "scripts": [],
                "links": parser._global_links,
            }
        )

    for item in source_items:
        item_text = _clean_text(" ".join(item["text"]))
        scripts = "\n".join(item["scripts"])
        script_href = _CUR_HREF.search(scripts)
        script_title = _CUR_TITLE.search(scripts)
        possible_links = list(item["links"])
        if script_href:
            possible_links.append(
                {
                    "href": script_href.group(1),
                    "text": script_title.group(1) if script_title else "",
                }
            )

        for link in possible_links:
            absolute = _normalized_article_url(link.get("href", ""), page_url)
            if absolute is None:
                continue
            match = _ARTICLE_PATH.fullmatch(urllib.parse.urlsplit(absolute).path)
            if match is None:
                continue
            link_title = _clean_text(link.get("text", ""))
            title = link_title or (
                _clean_text(script_title.group(1)) if script_title else ""
            )
            if not title:
                title = _DATE_TEXT.sub("", item_text).strip()
            date_match = _DATE_TEXT.search(item_text)
            published_date = (
                date_match.group(1)
                if date_match
                else datetime.strptime(match.group("date"), "%Y%m%d").date().isoformat()
            )
            record_id = match.group("record_id")
            current = discovered.get(absolute)
            if current is None or (not current["title"] and title):
                discovered[absolute] = {
                    "record_id": record_id,
                    "article_date_from_url": datetime.strptime(
                        match.group("date"), "%Y%m%d"
                    ).date().isoformat(),
                    "publication_date_observed": published_date,
                    "title": title,
                    "url": absolute,
                    "discovered_from_page": page_url,
                }

    return sorted(
        discovered.values(),
        key=lambda row: (row["publication_date_observed"], row["record_id"]),
        reverse=True,
    )


def _read_response(
    opener: Any,
    request: urllib.request.Request,
    timeout_seconds: float,
) -> tuple[bytes, int, str]:
    with opener.open(request, timeout=timeout_seconds) as response:
        raw = response.read()
        if len(raw) > _MAX_HTML_BYTES:
            raise ValueError(
                f"SZSE HTML response exceeds the {_MAX_HTML_BYTES}-byte capture limit"
            )
        status = int(getattr(response, "status", 200))
        final_url = response.geturl() if hasattr(response, "geturl") else request.full_url
        return raw, status, str(final_url)


def _make_opener() -> Any:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _pace(
    *,
    previous_request_at: float | None,
    min_interval_seconds: float,
    sleep_fn: Callable[[float], None],
) -> None:
    if previous_request_at is None:
        return
    elapsed = time.monotonic() - previous_request_at
    if elapsed < min_interval_seconds:
        sleep_fn(min_interval_seconds - elapsed)


def _fetch_official_html(
    opener: Any,
    url: str,
    *,
    referer: str,
    timeout_seconds: float,
) -> tuple[bytes, int, str]:
    _validate_https_szse_url(
        url,
        article=bool(_ARTICLE_PATH.fullmatch(urllib.parse.urlsplit(url).path)),
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Referer": referer,
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    raw, status, final_url = _read_response(opener, request, timeout_seconds)
    article = bool(_ARTICLE_PATH.fullmatch(urllib.parse.urlsplit(url).path))
    _validate_https_szse_url(final_url, article=article)
    if status != 200:
        raise RuntimeError(f"SZSE HTML request returned HTTP {status}: {url}")
    # UTF-8/GB18030 decoding below is the authoritative text check.
    if not raw:
        raise ValueError(f"SZSE returned an empty HTML body: {url}")
    return raw, status, final_url


def _decode_html(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("SZSE page is neither UTF-8 nor GB18030 text")


def _document_metadata(raw_html: str, article_url: str) -> dict[str, str]:
    parser = _DocumentHTMLParser()
    parser.feed(raw_html)
    parser.close()
    page_text = _clean_text(" ".join(parser.text_parts))
    title = _clean_text(" ".join(parser.title_parts))
    match = _ARTICLE_PATH.fullmatch(urllib.parse.urlsplit(article_url).path)
    assert match is not None
    date_match = _DATE_TEXT.search(page_text)
    publication_date = (
        date_match.group(1)
        if date_match
        else datetime.strptime(match.group("date"), "%Y%m%d").date().isoformat()
    )
    return {
        "page_title": title,
        "publication_date_observed": publication_date,
        "visible_text": page_text,
    }


def _validate_options(
    *,
    start_page: int,
    max_pages: int,
    max_articles: int,
    min_interval_seconds: float,
    timeout_seconds: float,
    keyword: str,
    start_date: str | None,
    end_date: str | None,
) -> None:
    if not isinstance(start_page, int) or isinstance(start_page, bool) or start_page < 0:
        raise ValueError("start_page must be a non-negative integer")
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 10:
        raise ValueError("max_pages must be between 1 and 10")
    if not isinstance(max_articles, int) or isinstance(max_articles, bool) or not 1 <= max_articles <= 50:
        raise ValueError("max_articles must be between 1 and 50")
    if not math.isfinite(min_interval_seconds) or min_interval_seconds < 0.5:
        raise ValueError("min_interval_seconds must be finite and at least 0.5")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    if article_urls is not None and (
        not isinstance(article_urls, list)
        or any(not isinstance(url, str) for url in article_urls)
    ):
        raise ValueError("article_urls must be a list of URL strings")
    if not isinstance(keyword, str):
        raise ValueError("keyword must be a string")
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must be supplied together")
    if start_date is not None and end_date is not None:
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError("date bounds must use YYYY-MM-DD") from exc
        if start.isoformat() != start_date or end.isoformat() != end_date:
            raise ValueError("date bounds must use canonical YYYY-MM-DD")
        if start > end:
            raise ValueError("start_date must be on or before end_date")


def probe_szse_notice_archive(
    *,
    output_dir: str | Path,
    start_page: int = 0,
    max_pages: int = 3,
    max_articles: int = 20,
    min_interval_seconds: float = 1.0,
    timeout_seconds: float = 20.0,
    keyword: str = "",
    start_date: str | None = None,
    end_date: str | None = None,
    article_urls: list[str] | None = None,
    opener: Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Capture a bounded number of official SZSE archive and notice HTML pages.

    A successful run records only the requested page/notice sample. It does not
    prove archive boundaries, all-page pagination, event coverage or PIT status.
    """
    _validate_options(
        start_page=start_page,
        max_pages=max_pages,
        max_articles=max_articles,
        min_interval_seconds=min_interval_seconds,
        timeout_seconds=timeout_seconds,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
    )
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    client = opener if opener is not None else _make_opener()
    report: dict[str, Any] = {
        "schema_version": 1,
        "probe": "szse_official_notice_archive_bounded_capture",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "ERROR",
        "request": {
            "listing_url": LISTING_URL,
            "start_page": start_page,
            "max_pages": max_pages,
            "max_articles": max_articles,
            "min_interval_seconds": min_interval_seconds,
            "timeout_seconds": timeout_seconds,
            "keyword": keyword,
            "start_date": start_date,
            "end_date": end_date,
            "operator_supplied_article_urls": list(article_urls or []),
        },
        "pages": [],
        "discovered_notices": [],
        "captured_notices": [],
        "counters": {
            "archive_pages_captured": 0,
            "discovered_notice_links": 0,
            "selected_notice_links": 0,
            "notice_pages_captured": 0,
            "duplicate_notice_urls": [],
        },
        "safety": {
            "read_only": True,
            "official_szse_host_only": True,
            "pdf_attachments_downloaded": False,
            "local_data_lake_access": False,
            "production_dataset_write": False,
            "archive_completeness_proven": False,
            "pit_admitted": False,
            "training_unlocked": False,
            "backtest_admitted": False,
        },
        "notes": [
            "The list-page index_N pagination pattern is observed public-page behavior, not an official API contract.",
            "This run captures only the bounded page range requested; it never claims full archive traversal or historical completeness.",
            "Only SZSE HTML pages on www.szse.cn are fetched. PDF and other attachments are never downloaded.",
            "Date precision and publication-time semantics are not inferred from capture time.",
            "An explicitly supplied article URL is recorded as an operator seed, not as a link discovered from the archive.",
        ],
    }

    last_request_at: float | None = None
    discovered: dict[str, dict[str, Any]] = {}
    try:
        for offset in range(max_pages):
            page_number = start_page + offset
            page_url = _index_url(start_page, offset)
            _validate_https_szse_url(page_url)
            _pace(
                previous_request_at=last_request_at,
                min_interval_seconds=min_interval_seconds,
                sleep_fn=sleep_fn,
            )
            requested_at = datetime.now(timezone.utc).isoformat()
            raw, status, final_url = _fetch_official_html(
                client,
                page_url,
                referer=LISTING_URL,
                timeout_seconds=timeout_seconds,
            )
            last_request_at = time.monotonic()
            captured_at = datetime.now(timezone.utc).isoformat()
            raw_html = _decode_html(raw)
            file_name = f"archive_page_{page_number:05d}.html"
            (target / file_name).write_bytes(raw)
            records = extract_szse_notice_records(raw_html, final_url)
            page_entry = {
                "page_number": page_number,
                "request_started_at": requested_at,
                "captured_at": captured_at,
                "requested_url": page_url,
                "final_url": final_url,
                "status_code": status,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "capture_file": file_name,
                "discovered_notice_count": len(records),
            }
            report["pages"].append(page_entry)
            for record in records:
                current = discovered.get(record["url"])
                if current is None:
                    discovered[record["url"]] = record
                else:
                    report["counters"]["duplicate_notice_urls"].append(record["url"])

        discovered_rows = list(discovered.values())
        discovered_rows.sort(
            key=lambda row: (row["publication_date_observed"], row["record_id"]),
            reverse=True,
        )
        report["counters"]["archive_pages_captured"] = len(report["pages"])
        report["counters"]["discovered_notice_links"] = len(discovered_rows)
        report["discovered_notices"] = discovered_rows

        selected: list[dict[str, Any]] = []
        for supplied_url in article_urls or []:
            _validate_https_szse_url(supplied_url, article=True)
            path_match = _ARTICLE_PATH.fullmatch(
                urllib.parse.urlsplit(supplied_url).path
            )
            assert path_match is not None
            selected.append(
                {
                    "record_id": path_match.group("record_id"),
                    "article_date_from_url": datetime.strptime(
                        path_match.group("date"), "%Y%m%d"
                    ).date().isoformat(),
                    "publication_date_observed": datetime.strptime(
                        path_match.group("date"), "%Y%m%d"
                    ).date().isoformat(),
                    "title": "",
                    "url": supplied_url,
                    "discovered_from_page": None,
                    "capture_origin": "operator_supplied_seed",
                }
            )

        for record in discovered_rows:
            published = record["publication_date_observed"]
            if keyword and keyword.casefold() not in record["title"].casefold():
                continue
            if start_date is not None and end_date is not None:
                if not start_date <= published <= end_date:
                    continue
            selected.append({**record, "capture_origin": "archive_discovered"})

        # A deterministic URL deduplication prevents a seed plus archive hit
        # from causing the same origin page to be downloaded twice.
        unique_selected: dict[str, dict[str, Any]] = {}
        for record in selected:
            unique_selected.setdefault(record["url"], record)
        selected = list(unique_selected.values())[:max_articles]
        report["counters"]["selected_notice_links"] = len(selected)

        for record in selected:
            _pace(
                previous_request_at=last_request_at,
                min_interval_seconds=min_interval_seconds,
                sleep_fn=sleep_fn,
            )
            requested_at = datetime.now(timezone.utc).isoformat()
            raw, status, final_url = _fetch_official_html(
                client,
                record["url"],
                referer=LISTING_URL,
                timeout_seconds=timeout_seconds,
            )
            last_request_at = time.monotonic()
            captured_at = datetime.now(timezone.utc).isoformat()
            raw_html = _decode_html(raw)
            metadata = _document_metadata(raw_html, final_url)
            file_name = f"notice_{record['article_date_from_url'].replace('-', '')}_{record['record_id']}.html"
            (target / file_name).write_bytes(raw)
            report["captured_notices"].append(
                {
                    **record,
                    "request_started_at": requested_at,
                    "captured_at": captured_at,
                    "final_url": final_url,
                    "status_code": status,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "capture_file": file_name,
                    "page_title": metadata["page_title"],
                    "document_publication_date_observed": metadata[
                        "publication_date_observed"
                    ],
                    "visible_text": metadata["visible_text"],
                }
            )

        report["counters"]["notice_pages_captured"] = len(
            report["captured_notices"]
        )
        report["status"] = "CAPTURED_BOUNDED_SAMPLE"
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        RuntimeError,
    ) as exc:
        report["status"] = "PARTIAL" if report["pages"] else "ERROR"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["counters"]["archive_pages_captured"] = len(report["pages"])
        report["counters"]["notice_pages_captured"] = len(
            report["captured_notices"]
        )

    report_path = target / "capture_report.json"
    report["report_file"] = report_path.name
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
