#!/usr/bin/env python3
"""
Fetch sitemap XML (index or single urlset), extract URLs, infer page types, write redirect-mapping CSV.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

# --- Constants / Configuration ---

DEFAULT_MASTER_URL = "https://community.sw.siemens.com/s/sitemap.xml"
DEFAULT_DELAY = 0.5
REQUEST_TIMEOUT = 30.0
CSV_COLUMNS = [
    "source_url",
    "target_url",
    "page_type",
    "lastmod",
    "priority",
    "notes",
    "source_sitemap",
]

DEFAULT_USER_AGENT = (
    "sitemap-to-csv/1.0 (+https://github.com/bpoulsen/python-salesforce-301-generator)"
)

NAVIGATIONAL_TOPICS_FILENAME = "navigational-topics.txt"


def default_output_filename() -> str:
    """Default CSV name: sitemap_urls_YYYY-MM-DD.csv (run date)."""
    return f"sitemap_urls_{date.today().isoformat()}.csv"


def _file_extension_in_path(path_lower: str) -> bool:
    last = path_lower.rstrip("/").rsplit("/", 1)[-1]
    return bool(re.search(r"\.[a-z0-9]{2,5}$", last))


def _is_community_home(path_lower: str) -> bool:
    if path_lower in ("/s", "/s/"):
        return True
    return path_lower.endswith("/s/")


# Ordered (predicate(path_lower), label); first match wins (PRD).
PAGE_TYPE_RULES: list[tuple[Callable[[str], bool], str]] = [
    (
        lambda p: "/article/" in p or "/articles/" in p or "/ka/" in p,
        "article",
    ),
    (lambda p: "/topic/" in p or "/topics/" in p, "topic"),
    (_is_community_home, "community-home"),
    (lambda p: "/feed/" in p, "feed"),
    (lambda p: "/profile/" in p or "/user/" in p, "user-profile"),
    (lambda p: "/group/" in p or "/groups/" in p, "group"),
    (lambda p: "/question/" in p or "/questions/" in p, "question"),
    (lambda p: "/idea/" in p or "/ideas/" in p, "idea"),
    (lambda p: "/event/" in p or "/events/" in p, "event"),
    (
        lambda p: "/file/" in p
        or "/files/" in p
        or _file_extension_in_path(p),
        "file",
    ),
    (lambda p: "/search/" in p, "search"),
]


class FatalError(Exception):
    """Unrecoverable error; message is shown on stderr."""


def local_name(elem: ET.Element) -> str:
    tag = elem.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


# --- HTTP Layer ---


def fetch_xml(url: str, user_agent: str) -> ET.Element:
    headers = {"User-Agent": user_agent}
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
    except requests.Timeout as e:
        raise FatalError(
            f"HTTP request timed out after {REQUEST_TIMEOUT}s: {url}\n{e}"
        ) from e
    except requests.RequestException as e:
        raise FatalError(f"HTTP request failed for {url}\n{e}") from e

    if resp.status_code != 200:
        raise FatalError(
            f"Non-200 HTTP status for {url}: status_code={resp.status_code}"
        )

    try:
        return ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise FatalError(f"XML parse error for {url}: {e}") from e


# --- Sitemap Parsing ---


def is_sitemap_index(root: ET.Element) -> bool:
    return local_name(root) == "sitemapindex"


def parse_sitemap_index(root: ET.Element) -> list[str]:
    out: list[str] = []
    for child in root:
        if local_name(child) != "sitemap":
            continue
        loc_text = None
        for sub in child:
            if local_name(sub) == "loc" and sub.text:
                loc_text = sub.text.strip()
                break
        if loc_text:
            out.append(loc_text)
    return out


def parse_url_set(root: ET.Element, source_sitemap: str) -> list[dict]:
    records: list[dict] = []
    for url_el in root:
        if local_name(url_el) != "url":
            continue
        loc_text = None
        lastmod = priority = ""
        for sub in url_el:
            ln = local_name(sub)
            if ln == "loc" and sub.text:
                loc_text = sub.text.strip()
            elif ln == "lastmod" and sub.text:
                lastmod = sub.text.strip()
            elif ln == "priority" and sub.text:
                priority = sub.text.strip()

        if not loc_text:
            print(
                f"Warning: <url> without <loc> skipped (sitemap: {source_sitemap})",
                file=sys.stderr,
            )
            continue

        records.append(
            {
                "source_url": loc_text,
                "target_url": "",
                "page_type": infer_page_type(loc_text),
                "lastmod": lastmod,
                "priority": priority,
                "notes": "",
                "source_sitemap": source_sitemap,
            }
        )
    return records


# --- Page Type Inference ---


def infer_page_type(url: str) -> str:
    path = urlparse(url).path.lower()
    for pred, label in PAGE_TYPE_RULES:
        if pred(path):
            return label
    return "root"


def extract_topic_id_segment(url: str) -> str | None:
    """Return the path segment after /topic/ or /topics/ (case-insensitive keywords), or None."""
    parts = urlparse(url).path.split("/")
    lowered = [p.lower() for p in parts]
    for i, low in enumerate(lowered):
        if low in ("topic", "topics") and i + 1 < len(parts):
            seg = parts[i + 1]
            return seg if seg else None
    return None


def load_navigational_topic_ids(path: Path) -> set[str]:
    if not path.is_file():
        print(
            f"Warning: navigational topics file not found ({path}); "
            "skipping navigational tagging.",
            file=sys.stderr,
        )
        return set()
    text = path.read_text(encoding="utf-8")
    return {line.strip() for line in text.splitlines() if line.strip()}


def apply_navigational_topic_tags(entries: list[dict], ids: set[str]) -> None:
    if not ids:
        return
    for row in entries:
        if row["page_type"] != "topic":
            continue
        tid = extract_topic_id_segment(row["source_url"])
        if tid is not None and tid in ids:
            row["priority"] = "1.0"
            row["notes"] = "navigational topic"


# --- Orchestration ---


def crawl(master_url: str, delay: float, user_agent: str) -> list[dict]:
    print("Step 1: Fetching master sitemap...", flush=True)
    root = fetch_xml(master_url, user_agent)

    entries: list[dict] = []

    if not is_sitemap_index(root):
        print(
            "  Master document is a URL set (single sitemap, not an index).",
            flush=True,
        )
        print(flush=True)
        print("Step 2: Fetching child sitemaps...", flush=True)
        print(
            "  (Skipped — master is a URL set, not a sitemap index.)",
            flush=True,
        )
        entries = parse_url_set(root, master_url)
        return entries

    child_urls = parse_sitemap_index(root)
    if not child_urls:
        raise FatalError("Master sitemap index contains zero child sitemap URLs")

    print(f"  Found {len(child_urls)} child sitemap(s).", flush=True)
    print(flush=True)
    print("Step 2: Fetching child sitemaps...", flush=True)

    for i, child_url in enumerate(child_urls, start=1):
        time.sleep(delay)
        child_root = fetch_xml(child_url, user_agent)

        if is_sitemap_index(child_root):
            grand_urls = parse_sitemap_index(child_root)
            for gu in grand_urls:
                time.sleep(delay)
                g_root = fetch_xml(gu, user_agent)
                if is_sitemap_index(g_root):
                    print(
                        f"Warning: nested sitemap index at level 2 skipped (not recursed): {gu}",
                        file=sys.stderr,
                    )
                    continue
                batch = parse_url_set(g_root, gu)
                entries.extend(batch)
                print(
                    f"  [{i}/{len(child_urls)}] {gu} — {len(batch)} URLs",
                    flush=True,
                )
        else:
            batch = parse_url_set(child_root, child_url)
            entries.extend(batch)
            print(
                f"  [{i}/{len(child_urls)}] {child_url} — {len(batch)} URLs",
                flush=True,
            )

    return entries


# --- Post-processing ---


def deduplicate(entries: list[dict]) -> tuple[list[dict], int]:
    seen: set[str] = set()
    out: list[dict] = []
    dup_count = 0
    for row in entries:
        u = row["source_url"]
        if u in seen:
            dup_count += 1
            continue
        seen.add(u)
        out.append(row)
    if dup_count:
        print(
            f"Warning: removed {dup_count} duplicate source_url entries (kept first occurrence)",
            file=sys.stderr,
        )
    return out, dup_count


# --- Output ---


def write_csv(entries: list[dict], output_file: str) -> None:
    sorted_rows = sorted(
        entries,
        key=lambda r: (r["page_type"], r["source_url"]),
    )
    try:
        with open(
            output_file,
            "w",
            encoding="utf-8",
            newline="",
        ) as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(sorted_rows)
    except OSError as e:
        raise FatalError(f"Cannot write output file {output_file!r}: {e}") from e


# --- Entry Point ---


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a sitemap index or URL set, extract <loc> entries, "
            "and write a CSV for 301 redirect mapping."
        )
    )
    parser.add_argument(
        "--master",
        default=DEFAULT_MASTER_URL,
        help=f"Master sitemap URL (index or urlset). Default: {DEFAULT_MASTER_URL!r}",
    )
    _default_output = default_output_filename()
    parser.add_argument(
        "--output",
        default=_default_output,
        help=f"Output CSV path. Default: {_default_output!r} (datestamp is the run date)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Delay in seconds between HTTP requests. Default: {DEFAULT_DELAY}",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        dest="user_agent",
        help="User-Agent header for HTTP requests.",
    )
    default_nav_topics = Path(__file__).resolve().parent / NAVIGATIONAL_TOPICS_FILENAME
    parser.add_argument(
        "--navigational-topics",
        type=Path,
        default=default_nav_topics,
        metavar="PATH",
        help=(
            "Text file: one topic record ID per line (Salesforce id). "
            f"Matching topic rows get priority 1.0 and notes "
            f"'navigational topic'. Default: {default_nav_topics}"
        ),
    )
    args = parser.parse_args()

    if args.delay < 0:
        print("Error: --delay must be >= 0", file=sys.stderr)
        sys.exit(1)

    try:
        entries = crawl(args.master, args.delay, args.user_agent)
        total_extracted = len(entries)
        deduped, _dup = deduplicate(entries)

        nav_path = args.navigational_topics.expanduser().resolve()
        nav_ids = load_navigational_topic_ids(nav_path)
        apply_navigational_topic_tags(deduped, nav_ids)

        print(flush=True)
        print("Step 3: Processing...", flush=True)
        print(f"  Total extracted : {total_extracted}", flush=True)
        print(f"  Total written   : {len(deduped)}", flush=True)
        by_type = Counter(r["page_type"] for r in deduped)
        nav_topic_count = sum(1 for r in deduped if r["notes"] == "navigational topic")
        print("  By page_type:", flush=True)
        for pt in sorted(by_type):
            print(f"    {pt}: {by_type[pt]}", flush=True)
            if pt == "topic":
                print(f"      navigational: {nav_topic_count}", flush=True)

        print(flush=True)
        print(f"Step 4: Writing to {args.output}...", flush=True)
        write_csv(deduped, args.output)
        print("  Done.", flush=True)
    except FatalError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
