"""
writer.py
---------
Writes accepted news articles to a timestamped CSV file.

Output columns (in order):
    artist_name, stage_name, country, artist_type,
    title, description, content_preview,
    url, image, published_at,
    source_name, source_url, source_country,
    news_type, relevance_score, relevance_reason, query_used

The CSV is opened once per run and rows are appended immediately after
each article is accepted — crash-safe, no data lost on interruption.
"""

import csv
from datetime import datetime
from pathlib import Path

from .config import CSV_COLUMNS, OUTPUT_DIR

log = __import__("logging").getLogger("news_scraper.writer")

CONTENT_PREVIEW_LEN = 300   # chars to keep from the full content field


def _parse_published_at(raw: str) -> str:
    """Convert ISO 8601 publishedAt to MM/DD/YYYY for readability."""
    if not raw:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt[:len(raw[:19])]).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return raw  # return raw if unparseable


def _build_row(article: dict, artist: dict, news_type: str,
               relevance_score: int, relevance_reason: str) -> dict:
    """Map a GNews article dict + artist metadata → one CSV row dict."""
    source      = article.get("source") or {}
    content_raw = (article.get("content", "") or "")

    return {
        # Artist context
        "artist_name":      artist.get("name",        ""),
        "stage_name":       artist.get("stage_name",  ""),
        "country":          artist.get("country",     ""),
        "artist_type":      artist.get("artist_type", ""),
        # Article data
        "title":            (article.get("title",       "") or "").strip(),
        "description":      (article.get("description", "") or "").strip(),
        "content_preview":  content_raw[:CONTENT_PREVIEW_LEN].strip(),
        "url":              (article.get("url",         "") or "").strip(),
        "image":            (article.get("image",       "") or "").strip(),
        "published_at":     _parse_published_at(article.get("publishedAt", "")),
        # Source metadata
        "source_name":      (source.get("name",    "") or "").strip(),
        "source_url":       (source.get("url",     "") or "").strip(),
        "source_country":   (source.get("country", "") or "").strip(),
        # Classification
        "news_type":        news_type,
        "relevance_score":  relevance_score,
        "relevance_reason": relevance_reason,
        "query_used":       (article.get("query_used", "") or "").strip(),
    }


class CsvWriter:
    """
    Append-mode CSV writer — one file per run, one row per accepted article.
    Opens the file immediately on construction and writes the header.
    """

    def __init__(self, run_timestamp: str) -> None:
        OUTPUT_DIR.mkdir(exist_ok=True)
        self._path     = OUTPUT_DIR / f"news_{run_timestamp}.csv"
        self._count    = 0
        self._seen_urls: set[str] = set()

        with open(self._path, "w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=CSV_COLUMNS).writeheader()

        log.info("Output CSV → %s", self._path)

    def write(self, article: dict, artist: dict, news_type: str,
              relevance_score: int, relevance_reason: str) -> bool:
        """
        Write one row.  Returns True if written, False if skipped (duplicate URL).
        """
        url = (article.get("url", "") or "").strip().rstrip("/")
        if url in self._seen_urls:
            log.debug("  Duplicate URL skipped: %s", url[:70])
            return False

        row = _build_row(article, artist, news_type, relevance_score, relevance_reason)
        with open(self._path, "a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(
                fh, fieldnames=CSV_COLUMNS, extrasaction="ignore"
            ).writerow(row)

        self._seen_urls.add(url)
        self._count += 1
        return True

    @property
    def count(self) -> int:
        return self._count

    @property
    def path(self) -> Path:
        return self._path
