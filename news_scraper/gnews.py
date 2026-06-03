"""
gnews.py
--------
GNews API client with full-year coverage via date windowing.

Coverage strategy
-----------------
GNews returns at most 10 articles per request (free plan).
To cover a full year we split the lookback period into quarterly
windows and fire one request per window per query template:

    Window 1: today-365d  → today-274d   (Q1 of the past year)
    Window 2: today-274d  → today-183d   (Q2)
    Window 3: today-183d  → today-91d    (Q3)
    Window 4: today-91d   → today        (Q4 / most recent)

4 windows × 1 primary query × 10 articles = up to 40 raw articles
per artist before the relevance filter runs.  If the primary query
returns fewer than MIN_ARTICLES_BEFORE_FALLBACK for any window,
fallback query templates are tried for that same window.

Date parameters sent to GNews:
    from = ISO 8601  e.g. 2025-06-03T00:00:00Z
    to   = ISO 8601  e.g. 2025-09-01T00:00:00Z

GNews search endpoint:
    GET https://gnews.io/api/v4/search
    Params: q, lang, max, from, to, apikey
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .config import (
    DATE_WINDOWS,
    GNEWS_API_KEY,
    GNEWS_BASE_URL,
    GNEWS_LANGUAGE,
    GNEWS_MAX_PER_REQUEST,
    LOOKBACK_DAYS,
    MIN_ARTICLES_BEFORE_FALLBACK,
    QUERY_TEMPLATES,
    REQUEST_DELAY_SECONDS,
)

log = __import__("logging").getLogger("news_scraper.gnews")

MAX_RETRIES   = 3
RETRY_BACKOFF = [2, 5, 10]


# ── Exceptions ────────────────────────────────────────────────────────────────

class GNewsError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"GNews HTTP {status_code}: {message}")

class GNewsQuotaError(GNewsError):
    """403 — daily quota exceeded."""

class GNewsRateLimitError(GNewsError):
    """429 — too many requests per second."""


# ── Date windows ──────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    """Format a datetime as GNews ISO 8601: 2025-06-03T00:00:00Z"""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_date_windows() -> list[tuple[str, str]]:
    """
    Split LOOKBACK_DAYS into DATE_WINDOWS equal windows.
    Returns list of (from_iso, to_iso) tuples, oldest first.

    Example with LOOKBACK_DAYS=365, DATE_WINDOWS=4:
        window 1: (today-365d, today-274d)
        window 2: (today-274d, today-183d)
        window 3: (today-183d, today-91d)
        window 4: (today-91d,  today)
    """
    now        = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start      = now - timedelta(days=LOOKBACK_DAYS)
    window_len = LOOKBACK_DAYS / DATE_WINDOWS

    windows: list[tuple[str, str]] = []
    for i in range(DATE_WINDOWS):
        w_start = start + timedelta(days=i * window_len)
        w_end   = start + timedelta(days=(i + 1) * window_len)
        if i == DATE_WINDOWS - 1:
            w_end = now          # make sure last window ends exactly at now
        windows.append((_iso(w_start), _iso(w_end)))

    log.debug("Date windows (%d × ~%dd):", DATE_WINDOWS, int(window_len))
    for i, (f, t) in enumerate(windows, 1):
        log.debug("  Window %d: %s → %s", i, f[:10], t[:10])

    return windows


# ── Query builder ──────────────────────────────────────────────────────────────

def build_queries(artist: dict) -> list[str]:
    """Build GNews query strings from templates for this artist."""
    name       = (artist.get("name",        "") or "").strip()
    stage_name = (artist.get("stage_name",  "") or "").strip()
    country    = (artist.get("country",     "") or "Nigeria").strip()
    atype      = (artist.get("artist_type", "") or "Music Artist").strip()

    queries: list[str] = []
    for template in QUERY_TEMPLATES:
        if "{stage_name}" in template:
            if not stage_name or stage_name.lower() == name.lower():
                continue
            q = template.format(name=name, stage_name=stage_name,
                                country=country, artist_type=atype)
        else:
            q = template.format(name=name, stage_name=stage_name or name,
                                country=country, artist_type=atype)
        if q not in queries:
            queries.append(q)
    return queries


# ── HTTP request ───────────────────────────────────────────────────────────────

def _fetch(query: str, from_date: str, to_date: str,
           max_results: int = GNEWS_MAX_PER_REQUEST) -> list[dict]:
    """
    One GNews API call for a specific query + date window.
    Returns raw article list.  Retries on 429/5xx with backoff.
    """
    if not GNEWS_API_KEY:
        raise GNewsError(401,
            "GNEWS_API_KEY is not set. Add it to your .env file:\n"
            "  GNEWS_API_KEY=your_api_key_here"
        )

    params = urllib.parse.urlencode({
        "q":      query,
        "lang":   GNEWS_LANGUAGE,
        "max":    min(max_results, GNEWS_MAX_PER_REQUEST),
        "from":   from_date,
        "to":     to_date,
        "apikey": GNEWS_API_KEY,
    })
    url = f"{GNEWS_BASE_URL}?{params}"
    log.debug("  GET %s", url.replace(GNEWS_API_KEY, "***"))

    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data     = json.loads(resp.read().decode("utf-8"))
                articles = data.get("articles", [])
                log.debug("  → %d articles  (total in index: %s)",
                          len(articles), data.get("totalArticles", "?"))
                return articles

        except urllib.error.HTTPError as exc:
            status = exc.code
            body   = ""
            try:
                body     = exc.read().decode("utf-8")
                err_data = json.loads(body)
                errors   = err_data.get("errors", "")
                if isinstance(errors, list):
                    body = errors[0]
                elif isinstance(errors, dict):
                    body = "; ".join(f"{k}: {v}" for k, v in errors.items())
            except Exception:
                pass

            if status == 400:
                raise GNewsError(400, f"Bad request — {body}") from exc
            if status == 401:
                raise GNewsError(401, f"Invalid API key — {body}") from exc
            if status == 403:
                raise GNewsQuotaError(403,
                    "Daily quota exceeded. Resets at 00:00 UTC. "
                    "Upgrade at https://gnews.io/pricing") from exc

            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            if status == 429:
                log.warning("  429 Too Many Requests — waiting %ds (retry %d/%d)",
                            wait, attempt + 1, MAX_RETRIES)
            else:
                log.warning("  %d Server Error — waiting %ds (retry %d/%d)",
                            status, wait, attempt + 1, MAX_RETRIES)
            time.sleep(wait)
            last_exc = exc
            continue

        except urllib.error.URLError as exc:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            log.warning("  Network error (attempt %d/%d): %s — waiting %ds",
                        attempt + 1, MAX_RETRIES, exc.reason, wait)
            time.sleep(wait)
            last_exc = exc

        except Exception as exc:
            log.error("  Unexpected error: %s", exc)
            raise

    raise GNewsError(
        0, f"All {MAX_RETRIES} attempts failed. Last: {last_exc}"
    ) from last_exc


# ── Main search function ───────────────────────────────────────────────────────

def search_artist_news(artist: dict) -> tuple[list[dict], list[str]]:
    """
    Search GNews for one artist across all date windows.

    For each window:
      1. Try the primary query template
      2. If fewer than MIN_ARTICLES_BEFORE_FALLBACK results, try fallback templates
      3. Tag each article with query_used, window_from, window_to
      4. Pause REQUEST_DELAY_SECONDS between every API call

    Returns (all_articles: list[dict], queries_used: list[str])
    """
    name     = artist.get("name", "unknown")
    queries  = build_queries(artist)
    windows  = build_date_windows()

    all_articles:  list[dict] = []
    queries_used:  list[str]  = []
    total_calls    = 0

    log.info("  Searching %d date window(s) × up to %d query template(s)",
             len(windows), len(queries))

    for w_idx, (w_from, w_to) in enumerate(windows, 1):
        log.info("  Window %d/%d: %s → %s",
                 w_idx, len(windows), w_from[:10], w_to[:10])

        window_articles = 0

        for q_idx, query in enumerate(queries):

            # If first query for this window got enough results, skip fallbacks
            if q_idx > 0 and window_articles >= MIN_ARTICLES_BEFORE_FALLBACK:
                log.debug("    Window %d: %d articles — skipping fallback queries",
                          w_idx, window_articles)
                break

            try:
                # Pause between every API call (rate limiting)
                if total_calls > 0:
                    time.sleep(REQUEST_DELAY_SECONDS)

                raw = _fetch(query, w_from, w_to)
                total_calls += 1

                # Tag articles with their window and query context
                for article in raw:
                    article["query_used"]   = query
                    article["window_from"]  = w_from[:10]
                    article["window_to"]    = w_to[:10]

                all_articles.extend(raw)
                window_articles += len(raw)

                if query not in queries_used:
                    queries_used.append(query)

                log.info("    Query %d: '%s' → %d articles",
                         q_idx + 1, query[:60], len(raw))

            except GNewsQuotaError:
                raise   # propagate — no point continuing any window

            except GNewsError as exc:
                log.error("    GNews error (window %d, query %d): %s",
                          w_idx, q_idx + 1, exc)
                continue

    log.info("  Total raw articles across all windows: %d  (API calls: %d)",
             len(all_articles), total_calls)
    return all_articles, queries_used
