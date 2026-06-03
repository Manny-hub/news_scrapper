"""
gnews.py
--------
GNews API client with full-year coverage via date windowing
and automatic fallback to web scraping on quota exhaustion.

Quota management (free plan = 100 calls/day)
--------------------------------------------
Old behaviour: 4 windows × 3 query templates = 12 calls per artist
  → quota burns after ~8 artists

New behaviour:
  - Primary query only per window (1 call/window × 4 windows = 4 calls/artist)
  - Fallback queries only fire if primary returns 0 results for that window
  - GNewsQuotaError triggers web scraping fallback for all remaining artists
  - Result: ~25 artists covered before quota exhaustion

Date windowing
--------------
    Window 1: today-365d → today-274d   (oldest)
    Window 2: today-274d → today-183d
    Window 3: today-183d → today-91d
    Window 4: today-91d  → today        (most recent)

4 windows × 10 articles = up to 40 raw articles per artist.
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
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_date_windows() -> list[tuple[str, str]]:
    """Split LOOKBACK_DAYS into DATE_WINDOWS equal time slices, oldest first."""
    now        = datetime.now(timezone.utc).replace(
                     hour=0, minute=0, second=0, microsecond=0)
    start      = now - timedelta(days=LOOKBACK_DAYS)
    window_len = LOOKBACK_DAYS / DATE_WINDOWS

    windows = []
    for i in range(DATE_WINDOWS):
        w_start = start + timedelta(days=i * window_len)
        w_end   = start + timedelta(days=(i + 1) * window_len)
        if i == DATE_WINDOWS - 1:
            w_end = now
        windows.append((_iso(w_start), _iso(w_end)))

    log.debug("Date windows (%d × ~%dd):", DATE_WINDOWS, int(window_len))
    for i, (f, t) in enumerate(windows, 1):
        log.debug("  Window %d: %s → %s", i, f[:10], t[:10])
    return windows


# ── Query builder ──────────────────────────────────────────────────────────────

def build_queries(artist: dict) -> list[str]:
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
    """One GNews API call. Retries on 429/5xx. Raises on 400/401/403."""
    if not GNEWS_API_KEY:
        raise GNewsError(401,
            "GNEWS_API_KEY is not set. Add it to your .env file:\n"
            "  GNEWS_API_KEY=your_api_key_here")

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
                log.debug("  → %d articles (total in index: %s)",
                          len(articles), data.get("totalArticles", "?"))
                return articles

        except urllib.error.HTTPError as exc:
            status = exc.code
            body   = ""
            try:
                raw      = exc.read().decode("utf-8")
                err_data = json.loads(raw)
                errors   = err_data.get("errors", "")
                body     = (errors[0] if isinstance(errors, list)
                            else "; ".join(f"{k}: {v}" for k,v in errors.items())
                            if isinstance(errors, dict) else str(errors))
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
            log.warning("  HTTP %d — waiting %ds (retry %d/%d)",
                        status, wait, attempt + 1, MAX_RETRIES)
            time.sleep(wait)
            last_exc = exc
            continue

        except urllib.error.URLError as exc:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            log.warning("  Network error — waiting %ds (retry %d/%d): %s",
                        wait, attempt + 1, MAX_RETRIES, exc.reason)
            time.sleep(wait)
            last_exc = exc

        except Exception as exc:
            log.error("  Unexpected error: %s", exc)
            raise

    raise GNewsError(0, f"All {MAX_RETRIES} retries failed. Last: {last_exc}") from last_exc


# ── Main search function ───────────────────────────────────────────────────────

def search_artist_news(artist: dict) -> tuple[list[dict], list[str]]:
    """
    Search GNews across all date windows for one artist.

    Quota-efficient strategy:
      - Run only the PRIMARY query per window (not all templates)
      - Only try fallback templates if primary returns 0 for that window
      - This uses 4 calls/artist instead of 12, tripling capacity

    Raises GNewsQuotaError if quota is hit — caller handles fallback.
    Returns (articles, queries_used).
    """
    queries      = build_queries(artist)
    primary_q    = queries[0]
    fallback_qs  = queries[1:]
    windows      = build_date_windows()

    all_articles: list[dict] = []
    queries_used: list[str]  = []
    total_calls  = 0

    log.info("  Searching %d date window(s) (primary query only; "
             "fallbacks activate on 0 results)", len(windows))

    for w_idx, (w_from, w_to) in enumerate(windows, 1):
        log.info("  Window %d/%d: %s → %s",
                 w_idx, len(windows), w_from[:10], w_to[:10])

        # ── Primary query ──────────────────────────────────────────────────
        if total_calls > 0:
            time.sleep(REQUEST_DELAY_SECONDS)

        try:
            raw = _fetch(primary_q, w_from, w_to)
            total_calls += 1
        except GNewsQuotaError:
            raise   # propagate immediately — caller triggers web fallback
        except GNewsError as exc:
            log.error("  GNews error (window %d): %s", w_idx, exc)
            raw = []

        for a in raw:
            a["query_used"]  = primary_q
            a["window_from"] = w_from[:10]
            a["window_to"]   = w_to[:10]
        all_articles.extend(raw)
        if primary_q not in queries_used:
            queries_used.append(primary_q)
        log.info("    Primary: '%s' → %d articles", primary_q[:60], len(raw))

        # ── Fallback queries — only when primary got nothing ───────────────
        if len(raw) < MIN_ARTICLES_BEFORE_FALLBACK:
            for fq in fallback_qs:
                time.sleep(REQUEST_DELAY_SECONDS)
                try:
                    fb_raw = _fetch(fq, w_from, w_to)
                    total_calls += 1
                except GNewsQuotaError:
                    raise
                except GNewsError as exc:
                    log.error("  Fallback error: %s", exc)
                    continue

                for a in fb_raw:
                    a["query_used"]  = fq
                    a["window_from"] = w_from[:10]
                    a["window_to"]   = w_to[:10]
                all_articles.extend(fb_raw)
                if fq not in queries_used:
                    queries_used.append(fq)
                log.info("    Fallback: '%s' → %d articles", fq[:60], len(fb_raw))

                if len(all_articles) >= MIN_ARTICLES_BEFORE_FALLBACK:
                    break

    log.info("  Total raw: %d articles across %d windows (%d API calls)",
             len(all_articles), len(windows), total_calls)
    return all_articles, queries_used