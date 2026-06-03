"""
main.py
-------
MightyCiti GNews Scraper — entry point.

Source priority:
  1. GNews API  — structured, clean, date-filtered
  2. Web scrape — DDG + preferred African news domains (fallback when:
       a. GNews 403 quota exceeded
       b. GNews returns 0 results for an artist
       c. GNEWS_API_KEY not set)

Usage:
    python main.py                           # uses artists.txt by default
    python main.py artists.txt               # explicit input file
    python main.py artists.txt --dry-run     # print results, no CSV written
    python main.py artists.txt --limit 3     # process only first 3 artists
    python main.py artists.txt --verbose     # show rejected articles too
    python main.py artists.txt --web-only    # skip GNews, use web scraper only
"""

import argparse
import sys
import time
from datetime import datetime

from news_scraper.config import OUTPUT_DIR, REQUEST_DELAY_SECONDS, MAX_ARTICLES_PER_ARTIST
from news_scraper.gnews import GNewsQuotaError, search_artist_news
from news_scraper.loader import load_artists
from news_scraper.logger import get_logger
from news_scraper.relevance import deduplicate, infer_news_type, is_relevant
from news_scraper.webscraper import scrape_artist_news
from news_scraper.writer import CsvWriter


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "MightyCiti GNews scraper — fetches news for African artists.\n"
            "Primary: GNews API  |  Fallback: DDG + preferred news domains\n"
            "Input:  artists.txt  |  Output: output/news_TIMESTAMP.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", nargs="?", default="artists.txt",
                   help="Artist input .txt file (default: artists.txt)")
    p.add_argument("--dry-run",  action="store_true",
                   help="Print results to console — do NOT write CSV")
    p.add_argument("--web-only", action="store_true",
                   help="Skip GNews entirely, use web scraper only")
    p.add_argument("--limit",    type=int, default=0, metavar="N",
                   help="Process only the first N artists")
    p.add_argument("--verbose",  action="store_true",
                   help="Also log rejected articles with rejection reason")
    return p.parse_args()


# ── Source routing ─────────────────────────────────────────────────────────────

def _fetch_via_gnews(artist: dict) -> tuple[list[dict], str]:
    """
    Try GNews.  Returns (articles, source_label).
    Raises GNewsQuotaError if quota is exceeded (caller handles fallback).
    Falls back to web scraper if GNews returns 0 results.
    """
    raw, _ = search_artist_news(artist)
    if raw:
        return raw, "gnews"

    # GNews returned nothing (artist not in English press) — try web
    log.info("  GNews returned 0 results — switching to web scraper")
    web_articles = scrape_artist_news(artist, max_articles=artist["max_articles"] * 2)
    return web_articles, "webscraper"


def _fetch_via_web(artist: dict) -> tuple[list[dict], str]:
    """Always use the web scraper."""
    articles = scrape_artist_news(artist, max_articles=artist["max_articles"] * 2)
    return articles, "webscraper"


# ── Main ───────────────────────────────────────────────────────────────────────

log = None   # assigned after get_logger() call below


def main() -> None:
    global log
    args      = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log       = get_logger(OUTPUT_DIR)

    # ── Load artists ───────────────────────────────────────────────────────
    try:
        artists = load_artists(args.input)
    except FileNotFoundError as exc:
        get_logger(OUTPUT_DIR).error("%s", exc)
        sys.exit(1)

    if args.limit:
        artists = artists[:args.limit]
        log.info("--limit %d applied", args.limit)

    # ── CSV writer ─────────────────────────────────────────────────────────
    writer = None if args.dry_run else CsvWriter(timestamp)

    # ── State ──────────────────────────────────────────────────────────────
    quota_exhausted   = False   # once True, all remaining artists use web scraper
    total_accepted    = 0
    total_rejected    = 0
    artists_with_hits = 0
    source_counts     = {"gnews": 0, "webscraper": 0}

    # ── Main loop ──────────────────────────────────────────────────────────
    for idx, artist in enumerate(artists, 1):
        name = artist["name"]
        log.info("━━ [%d/%d] %s (%s · %s · max=%d)",
                 idx, len(artists), name,
                 artist["country"], artist["artist_type"],
                 artist["max_articles"])

        # ── Decide source ──────────────────────────────────────────────────
        use_web = args.web_only or quota_exhausted

        try:
            if use_web:
                if quota_exhausted:
                    log.info("  [WEB FALLBACK — quota exhausted]")
                raw_articles, source = _fetch_via_web(artist)
            else:
                raw_articles, source = _fetch_via_gnews(artist)

        except GNewsQuotaError as exc:
            log.warning("  GNews quota exceeded — switching ALL remaining artists to web scraper")
            log.warning("  (%s)", exc)
            quota_exhausted = True
            # Retry this artist immediately with web scraper
            raw_articles, source = _fetch_via_web(artist)

        except Exception as exc:
            log.error("  Unexpected error for '%s': %s", name, exc)
            continue

        if not raw_articles:
            log.info("  No results from any source for: %s", name)
            continue

        # ── Deduplicate ────────────────────────────────────────────────────
        unique = deduplicate(raw_articles)
        log.info("  %d unique articles after dedup  [source: %s]",
                 len(unique), source)

        # ── Relevance filter ───────────────────────────────────────────────
        artist_accepted = 0
        max_cap         = artist["max_articles"]

        for article in unique:
            if artist_accepted >= max_cap:
                break

            accepted, score, reason = is_relevant(article, artist)

            if accepted:
                news_type = infer_news_type(
                    article.get("title",       ""),
                    article.get("description", ""),
                    artist["artist_type"],
                )

                if args.dry_run:
                    _print_article(article, artist, score, reason, news_type, source)
                else:
                    written = writer.write(article, artist, news_type,
                                           score, reason)
                    if written:
                        log.info("  ✔  [%s | score=%d | %s] %s",
                                 source, score, news_type,
                                 (article.get("title", "") or "")[:65])

                artist_accepted += 1
                total_accepted  += 1
                source_counts[source] = source_counts.get(source, 0) + 1

            else:
                total_rejected += 1
                if args.verbose:
                    log.debug("  ✘  [score=%d] %s — %s",
                              score,
                              (article.get("title", "") or "")[:55],
                              reason)

        if artist_accepted:
            artists_with_hits += 1
            log.info("  → %d article(s) accepted for %s", artist_accepted, name)
        else:
            log.info("  → No relevant articles found for %s", name)

        # ── Rate limit pause between artists ──────────────────────────────
        if idx < len(artists):
            time.sleep(REQUEST_DELAY_SECONDS)

    # ── Summary ───────────────────────────────────────────────────────────
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("Finished")
    log.info("  Artists processed  : %d", len(artists))
    log.info("  Artists with hits  : %d", artists_with_hits)
    log.info("  Articles accepted  : %d  (GNews: %d  Web: %d)",
             total_accepted,
             source_counts.get("gnews", 0),
             source_counts.get("webscraper", 0))
    log.info("  Articles rejected  : %d  (failed relevance filter)", total_rejected)

    if quota_exhausted:
        log.warning("  GNews quota was exhausted during this run.")
        log.warning("  Resets at 00:00 UTC. Web scraper covered remaining artists.")

    if args.dry_run:
        log.info("  DRY RUN — no CSV written")
    elif writer:
        log.info("  Output CSV         : %s", writer.path)
        log.info("  Rows written       : %d", writer.count)

    if total_accepted == 0:
        log.warning(
            "No articles passed the relevance filter.\n"
            "Tips:\n"
            "  • Run with --verbose to see rejection reasons\n"
            "  • Lower MIN_RELEVANCE_SCORE in config.py (currently %d)\n"
            "  • Check artist names in artists.txt match how they appear in press\n"
            "  • Try --web-only if GNews doesn't cover these artists",
            __import__("news_scraper.config", fromlist=["MIN_RELEVANCE_SCORE"])
            .MIN_RELEVANCE_SCORE,
        )


def _print_article(article: dict, artist: dict, score: int,
                   reason: str, news_type: str, source: str) -> None:
    s = article.get("source") or {}
    print()
    print(f"  Artist  : {artist['name']}  [{artist['country']} · {artist['artist_type']}]")
    print(f"  Source  : {source.upper()}  |  Score={score}  |  Type={news_type}")
    print(f"  Reason  : {reason}")
    print(f"  Title   : {(article.get('title',       '') or '')[:90]}")
    print(f"  Desc    : {(article.get('description', '') or '')[:110]}")
    print(f"  Date    : {article.get('publishedAt', '')}")
    print(f"  Outlet  : {s.get('name','')}  ({s.get('url','')})")
    print(f"  URL     : {(article.get('url', '') or '')[:90]}")
    print(f"  Image   : {(article.get('image', '') or '')[:80] or '(none)'}")
    print(f"  Window  : {article.get('window_from','')} → {article.get('window_to','')}")


if __name__ == "__main__":
    main()
