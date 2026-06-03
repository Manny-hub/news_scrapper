"""
main.py
-------
MightyCiti GNews Scraper — entry point.

Reads artist names and query parameters from a .txt file,
searches GNews for relevant articles, filters strictly by relevance,
and saves results to a timestamped CSV in the output/ folder.

Usage:
    python main.py                          # uses artists.txt by default
    python main.py artists.txt              # explicit input file
    python main.py artists.txt --dry-run    # print results, no CSV written
    python main.py artists.txt --limit 3    # process only first 3 artists
    python main.py artists.txt --verbose    # show rejected articles too
"""

import argparse
import sys
import time
from datetime import datetime

from news_scraper.config import OUTPUT_DIR, REQUEST_DELAY_SECONDS
from news_scraper.gnews import GNewsQuotaError, search_artist_news
from news_scraper.loader import load_artists
from news_scraper.logger import get_logger
from news_scraper.relevance import deduplicate, infer_news_type, is_relevant
from news_scraper.writer import CsvWriter


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "MightyCiti GNews scraper — fetches news articles for African artists.\n"
            "Input:  .txt file  (artist_name | country | artist_type | max_articles)\n"
            "Output: CSV file   (output/news_TIMESTAMP.csv)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "input",
        nargs="?",
        default="artists.txt",
        help="Path to the artist input .txt file (default: artists.txt)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print accepted articles to console — do NOT write CSV",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Process only the first N artists (0 = all)",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Also log rejected articles with their rejection reason",
    )
    return p.parse_args()


def main() -> None:
    args      = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log       = get_logger(OUTPUT_DIR)

    # ── Load artist list ───────────────────────────────────────────────────
    try:
        artists = load_artists(args.input)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        sys.exit(1)

    if args.limit:
        artists = artists[: args.limit]
        log.info("--limit %d: processing %d artist(s)", args.limit, len(artists))

    # ── Prepare CSV writer (skip in dry-run) ───────────────────────────────
    writer = None if args.dry_run else CsvWriter(timestamp)

    # ── Run counters ───────────────────────────────────────────────────────
    total_accepted  = 0
    total_rejected  = 0
    total_artists   = len(artists)
    artists_with_hits = 0

    # ── Main loop ──────────────────────────────────────────────────────────
    for idx, artist in enumerate(artists, 1):
        name = artist["name"]
        log.info("━━ [%d/%d] %s (%s · %s · max=%d)",
                 idx, total_artists, name,
                 artist["country"], artist["artist_type"],
                 artist["max_articles"])

        # ── Fetch from GNews ───────────────────────────────────────────────
        try:
            raw_articles, queries_used = search_artist_news(artist)
        except GNewsQuotaError as exc:
            log.error("Daily quota exceeded — stopping run early. %s", exc)
            break
        except Exception as exc:
            log.error("Unexpected error fetching news for '%s': %s", name, exc)
            continue

        if not raw_articles:
            log.info("  No results from GNews for: %s", name)
            continue

        # ── Deduplicate raw results ────────────────────────────────────────
        unique = deduplicate(raw_articles)
        log.info("  %d unique articles after dedup (from %d raw)",
                 len(unique), len(raw_articles))

        # ── Apply strict relevance filter ──────────────────────────────────
        artist_accepted = 0
        max_for_artist  = artist["max_articles"]

        for article in unique:
            if artist_accepted >= max_for_artist:
                log.debug("  Reached max_articles=%d for %s", max_for_artist, name)
                break

            accepted, score, reason = is_relevant(article, artist)

            if accepted:
                news_type = infer_news_type(
                    article.get("title",       ""),
                    article.get("description", ""),
                    artist["artist_type"],
                )

                if args.dry_run:
                    _print_article(article, artist, score, reason, news_type)
                else:
                    written = writer.write(
                        article, artist, news_type, score, reason
                    )
                    if written:
                        log.info(
                            "  ✔  [score=%d | %s] %s",
                            score, news_type,
                            (article.get("title", "") or "")[:70],
                        )

                artist_accepted += 1
                total_accepted  += 1

            else:
                total_rejected += 1
                if args.verbose:
                    log.debug(
                        "  ✘  [score=%d] %s — %s",
                        score,
                        (article.get("title", "") or "")[:60],
                        reason,
                    )

        if artist_accepted:
            artists_with_hits += 1
            log.info("  → %d article(s) accepted for %s", artist_accepted, name)
        else:
            log.info("  → No relevant articles found for %s", name)

        # ── Respect GNews rate limit between artists ───────────────────────
        if idx < total_artists:
            time.sleep(REQUEST_DELAY_SECONDS)

    # ── Final summary ──────────────────────────────────────────────────────
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("Finished")
    log.info("  Artists processed : %d / %d", total_artists, total_artists)
    log.info("  Artists with hits : %d", artists_with_hits)
    log.info("  Articles accepted : %d", total_accepted)
    log.info("  Articles rejected : %d  (failed relevance filter)", total_rejected)

    if args.dry_run:
        log.info("  DRY RUN — no CSV written")
    elif writer:
        log.info("  Output CSV        : %s", writer.path)
        log.info("  Rows written      : %d", writer.count)

    if total_accepted == 0:
        log.warning(
            "No articles passed the relevance filter.\n"
            "Tips:\n"
            "  • Lower MIN_RELEVANCE_SCORE in config.py (currently %d)\n"
            "  • Check artist names are spelled correctly in artists.txt\n"
            "  • Run with --verbose to see why articles are being rejected\n"
            "  • Verify GNEWS_API_KEY is set correctly in your .env file",
            __import__("news_scraper.config", fromlist=["MIN_RELEVANCE_SCORE"])
            .MIN_RELEVANCE_SCORE,
        )


def _print_article(article: dict, artist: dict,
                   score: int, reason: str, news_type: str) -> None:
    """Pretty-print one accepted article to the console (dry-run mode)."""
    source = article.get("source") or {}
    print()
    print(f"  Artist  : {artist['name']}  [{artist['country']} · {artist['artist_type']}]")
    print(f"  Score   : {score}  |  Type: {news_type}  |  {reason}")
    print(f"  Title   : {(article.get('title', '') or '')[:90]}")
    print(f"  Desc    : {(article.get('description', '') or '')[:110]}")
    print(f"  Date    : {article.get('publishedAt', '')}")
    print(f"  Source  : {source.get('name', '')}  ({source.get('url', '')})")
    print(f"  URL     : {(article.get('url', '') or '')[:90]}")
    print(f"  Image   : {(article.get('image', '') or '')[:80] or '(none)'}")
    print(f"  Query   : {article.get('query_used', '')}")


if __name__ == "__main__":
    main()
