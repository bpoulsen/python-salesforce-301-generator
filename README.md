# Salesforce 301 Generator

A Python command-line tool that fetches a sitemap **index** or a single **URL set**, walks nested sitemaps (up to two index levels), extracts every `<loc>` URL, infers a `page_type` from path patterns, optionally flags **navigational** community topics, and writes a UTF-8 CSV suited for a 301 redirect mapping workbook.

## Requirements

- Python 3.11+
- [requests](https://requests.readthedocs.io/) 2.31+

On macOS/Homebrew Python (PEP 668), use a virtual environment before `pip install`:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

## Install

**Clone and run (no install):**

```bash
python -m pip install -r requirements.txt
python sitemap_to_csv.py --help
```

**Install into the current environment (console command):**

```bash
python -m pip install .
sitemap-to-csv --help
```

Keep [`navigational-topics.txt`](navigational-topics.txt) beside the script (or pass `--navigational-topics`) when you want navigational tagging. A `pip install .` layout may not ship that file next to the installed module; if the default path is missing, the tool prints a **stderr** warning and continues without navigational tagging.

## Usage

```text
python sitemap_to_csv.py [--master URL] [--output PATH] [--delay SECONDS]
                         [--user-agent STRING] [--navigational-topics PATH]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--master` | `https://community.sw.siemens.com/s/sitemap.xml` | Sitemap URL (index or single `urlset` XML) |
| `--output` | `sitemap_urls_YYYY-MM-DD.csv` | Output CSV path (default includes **today’s** date) |
| `--delay` | `0.5` | Seconds to wait between HTTP requests |
| `--user-agent` | `sitemap-to-csv/1.0 (+https://github.com/bpoulsen/python-salesforce-301-generator)` | `User-Agent` header for all requests |
| `--navigational-topics` | `navigational-topics.txt` next to `sitemap_to_csv.py` | One Salesforce topic **record ID** per line (see below) |

Progress and step summaries go to **stdout**; warnings, duplicate counts, missing navigational file, and fatal errors go to **stderr**.

During **Step 3: Processing…**, the tool prints total rows extracted and written (after deduplication), then a **By page_type:** breakdown with counts per `page_type`.

### Examples

```bash
# Default Siemens community master sitemap → sitemap_urls_YYYY-MM-DD.csv
python sitemap_to_csv.py

# Custom master and output file
python sitemap_to_csv.py --master https://example.com/sitemap.xml --output redirects.csv

# Single-file sitemap (no index)
python sitemap_to_csv.py --master https://example.com/static-sitemap.xml --delay 1

# Custom User-Agent (corporate proxy or site policy)
python sitemap_to_csv.py --user-agent "MyBot/1.0 (contact@example.com)"

# Custom navigational-topics list
python sitemap_to_csv.py --navigational-topics /path/to/my-topic-ids.txt
```

## Navigational topics

Optional file [`navigational-topics.txt`](navigational-topics.txt): **one ID per line**, UTF-8, blank lines ignored. IDs are matched **case-sensitively** to the URL path segment **immediately after** `/topic/` or `/topics/` (the keyword itself is detected case-insensitively).

For each row with `page_type` **`topic`** whose topic ID appears in that set:

- **`priority`** is set to **`1.0`** (overrides any value from the sitemap XML `<priority>`).
- **`notes`** is set to **`navigational topic`** (replaces any previous notes for that row).

If the navigational topics file is **missing**, the run **continues** after a **stderr** warning; no rows receive navigational tagging.

## Output CSV

Columns (in order): `source_url`, `target_url`, `page_type`, `lastmod`, `priority`, `notes`, `source_sitemap`.

- Rows are sorted by `page_type`, then `source_url`.
- **`changefreq`** is not exported (it is not read from the sitemap).
- **`target_url`** is left blank for the redirect team to fill in.
- **`notes`** is usually blank unless the row is tagged as a navigational topic (see above).
- URLs that match no page-type rule use **`page_type` `root`**.

## License

See [LICENSE](LICENSE).
