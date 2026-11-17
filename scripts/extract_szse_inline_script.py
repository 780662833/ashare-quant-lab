"""Extract one inline script from a captured SZSE HTML file, offline only."""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
from pathlib import Path


class _InlineScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.index = 0
        self.current: int | None = None
        self.parts: list[str] = []
        self.scripts: dict[int, tuple[bool, str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        self.index += 1
        values = {key.lower(): value or "" for key, value in attrs}
        self.current = self.index
        self.parts = []
        self.scripts[self.index] = (not bool(values.get("src")), "")

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or self.current is None:
            return
        is_inline, _ = self.scripts[self.current]
        self.scripts[self.current] = (is_inline, "".join(self.parts))
        self.current = None
        self.parts = []


def extract_inline_script(html_path: str | Path, index: int) -> bytes:
    """Return the UTF-8 bytes of the 1-based inline script index."""
    if index < 1:
        raise ValueError("script index must be >= 1")
    raw = Path(html_path).read_bytes()
    if not raw:
        raise ValueError("captured HTML file is empty")
    decoded: str | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            decoded = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("captured HTML is neither UTF-8 nor GB18030")
    parser = _InlineScriptParser()
    parser.feed(decoded)
    parser.close()
    selected = parser.scripts.get(index)
    if selected is None:
        raise ValueError(f"script index {index} does not exist; found {parser.index}")
    is_inline, source = selected
    if not is_inline:
        raise ValueError(f"script index {index} is external, not inline")
    return source.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract one inline script from captured SZSE HTML; no network or JS execution."
    )
    parser.add_argument("--html", required=True, help="Previously captured HTML file")
    parser.add_argument("--index", type=int, default=4, help="1-based script tag index (default: 4)")
    parser.add_argument("--output", required=True, help="Destination .js or .txt file")
    args = parser.parse_args()
    try:
        source = extract_inline_script(args.html, args.index)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source)
    print(f"script_index={args.index}")
    print(f"source_bytes={len(source)}")
    print(f"source_sha256={hashlib.sha256(source).hexdigest()}")
    print(f"output={target.resolve()}")
    print("network_requests_made=false")
    print("javascript_executed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
