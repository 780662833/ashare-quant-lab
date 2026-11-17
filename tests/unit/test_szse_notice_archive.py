from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from ashare_quant.research.szse_notice_archive import (
    LISTING_URL,
    extract_szse_notice_records,
    probe_szse_notice_archive,
)


class FakeResponse:
    def __init__(self, body: bytes, *, url: str, status: int = 200) -> None:
        self.body = body
        self.url = url
        self.status = status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url


class FakeOpener:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[object] = []

    def open(self, request: object, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        url = str(getattr(request, "full_url"))
        if url not in self.responses:
            raise AssertionError(f"unexpected request URL: {url}")
        return self.responses[url]


def test_extracts_article_link_date_and_title_from_archive_list() -> None:
    page_url = LISTING_URL
    raw_html = """
    <html><body><ul class="article-list">
      <li>
        <a href="/disclosure/notice/t20200522_577530.html">
          关于山东龙力生物科技股份有限公司股票终止上市的公告
        </a>
        <span class="time">2020-05-22</span>
      </li>
      <li>
        <a href="https://outside.example/ignore.html">External</a>
        <a href="/download/not-a-notice.pdf">PDF</a>
      </li>
    </ul></body></html>
    """

    records = extract_szse_notice_records(raw_html, page_url)

    assert len(records) == 1
    assert records[0]["record_id"] == "577530"
    assert records[0]["article_date_from_url"] == "2020-05-22"
    assert records[0]["publication_date_observed"] == "2020-05-22"
    assert records[0]["title"] == "关于山东龙力生物科技股份有限公司股票终止上市的公告"
    assert records[0]["url"] == (
        "https://www.szse.cn/disclosure/notice/t20200522_577530.html"
    )


def test_extracts_js_curhref_curtitle_fallback() -> None:
    raw_html = """
    <ul><li><span class="time">2020-05-22</span>
      <script>
        var curHref = '/disclosure/notice/t20200522_577530.html';
        var curTitle = '关于山东龙力生物科技股份有限公司股票终止上市的公告';
      </script>
    </li></ul>
    """

    records = extract_szse_notice_records(raw_html, LISTING_URL)

    assert len(records) == 1
    assert records[0]["title"].startswith("关于山东龙力生物科技股份有限公司")
    assert records[0]["publication_date_observed"] == "2020-05-22"


def test_rejects_non_szse_listing_page_url() -> None:
    with pytest.raises(ValueError, match="only canonical HTTPS URLs"):
        extract_szse_notice_records("<html></html>", "https://example.com/index.html")


def test_probe_captures_page_and_article_original_bytes_and_hashes(
    tmp_path: Path,
) -> None:
    listing_url = LISTING_URL
    article_url = "https://www.szse.cn/disclosure/notice/t20200522_577530.html"
    listing_bytes = (
        b'<ul><li><a href="/disclosure/notice/t20200522_577530.html">'
        b'\xe5\x85\xb3\xe4\xba\x8e\xe5\xb1\xb1\xe4\xb8\x9c\xe9\xbe\x99\xe5\x8a\x9b'
        b'\xe7\x94\x9f\xe7\x89\xa9\xe7\xa7\x91\xe6\x8a\x80\xe8\x82\xa1\xe4\xbb\xbd'
        b'\xe6\x9c\x89\xe9\x99\x90\xe5\x85\xac\xe5\x8f\xb8\xe8\x82\xa1\xe7\xa5\xa8'
        b'\xe7\xbb\x88\xe6\xad\xa2\xe4\xb8\x8a\xe5\xb8\x82\xe7\x9a\x84\xe5\x85\xac'
        b'\xe5\x91\x8a</a><span>2020-05-22</span></li></ul>'
    )
    article_bytes = (
        "<html><head><title>关于山东龙力生物科技股份有限公司股票终止上市的公告</title></head>"
        "<body><h1>关于山东龙力生物科技股份有限公司股票终止上市的公告</h1>"
        "<p>时间：2020-05-22</p><p>2020年5月22日，本所决定公司股票终止上市。</p>"
        "</body></html>"
    ).encode("utf-8")
    opener = FakeOpener(
        {
            listing_url: FakeResponse(listing_bytes, url=listing_url),
            article_url: FakeResponse(article_bytes, url=article_url),
        }
    )

    report = probe_szse_notice_archive(
        output_dir=tmp_path,
        start_page=0,
        max_pages=1,
        max_articles=5,
        min_interval_seconds=0.5,
        opener=opener,
        sleep_fn=lambda _: None,
    )

    assert report["status"] == "CAPTURED_BOUNDED_SAMPLE"
    assert report["counters"]["archive_pages_captured"] == 1
    assert report["counters"]["discovered_notice_links"] == 1
    assert report["counters"]["notice_pages_captured"] == 1
    assert report["safety"]["archive_completeness_proven"] is False
    assert report["safety"]["pdf_attachments_downloaded"] is False
    page = report["pages"][0]
    notice = report["captured_notices"][0]
    assert page["sha256"] == hashlib.sha256(listing_bytes).hexdigest()
    assert notice["sha256"] == hashlib.sha256(article_bytes).hexdigest()
    assert notice["record_id"] == "577530"
    assert notice["publication_date_observed"] == "2020-05-22"
    assert "本所决定公司股票终止上市" in notice["visible_text"]
    assert (tmp_path / page["capture_file"]).read_bytes() == listing_bytes
    assert (tmp_path / notice["capture_file"]).read_bytes() == article_bytes
    stored = (tmp_path / "capture_report.json").read_text(encoding="utf-8")
    assert '"status": "CAPTURED_BOUNDED_SAMPLE"' in stored
    assert len(opener.requests) == 2
    assert all(urlsplit(str(getattr(req, "full_url"))).hostname == "www.szse.cn" for req in opener.requests)


def test_operator_seed_is_captured_but_not_claimed_as_archive_discovered(
    tmp_path: Path,
) -> None:
    article_url = "https://www.szse.cn/disclosure/notice/t20200522_577530.html"
    page_url = LISTING_URL
    listing_bytes = b"<html><body><p>No links</p></body></html>"
    article_bytes = b"<html><title>SEED</title><body>2020-05-22</body></html>"
    opener = FakeOpener(
        {
            page_url: FakeResponse(listing_bytes, url=page_url),
            article_url: FakeResponse(article_bytes, url=article_url),
        }
    )

    report = probe_szse_notice_archive(
        output_dir=tmp_path,
        max_pages=1,
        max_articles=1,
        article_urls=[article_url],
        min_interval_seconds=0.5,
        opener=opener,
        sleep_fn=lambda _: None,
    )

    assert report["status"] == "CAPTURED_BOUNDED_SAMPLE"
    assert report["captured_notices"][0]["capture_origin"] == "operator_supplied_seed"
    assert report["counters"]["discovered_notice_links"] == 0
    assert len(opener.requests) == 2


def test_rejects_external_redirect_and_preserves_partial_listing_capture(
    tmp_path: Path,
) -> None:
    listing_url = LISTING_URL
    opener = FakeOpener(
        {
            listing_url: FakeResponse(
                b"<html>archive</html>",
                url="https://outside.example/redirected.html",
            )
        }
    )

    report = probe_szse_notice_archive(
        output_dir=tmp_path,
        max_pages=1,
        min_interval_seconds=0.5,
        opener=opener,
        sleep_fn=lambda _: None,
    )

    assert report["status"] == "ERROR"
    assert "only canonical HTTPS URLs" in report["error"]
    assert report["safety"]["archive_completeness_proven"] is False


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"start_page": -1}, "start_page must be a non-negative integer"),
        ({"max_pages": 11}, "max_pages must be between 1 and 10"),
        ({"max_articles": 51}, "max_articles must be between 1 and 50"),
        ({"start_date": "2020-05-22"}, "start_date and end_date must be supplied together"),
    ],
)
def test_probe_rejects_unbounded_or_incomplete_options(
    tmp_path: Path,
    kwargs: dict[str, object],
    message: str,
) -> None:
    base: dict[str, object] = {
        "output_dir": tmp_path,
        "opener": FakeOpener({}),
        "sleep_fn": lambda _: None,
        "min_interval_seconds": 0.5,
    }
    base.update(kwargs)
    with pytest.raises(ValueError, match=message):
        probe_szse_notice_archive(**base)  # type: ignore[arg-type]
