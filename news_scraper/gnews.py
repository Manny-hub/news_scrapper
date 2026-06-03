"""
gnews.py
--------
GNews API client.

Handles:
  - Query building from artist + template
  - HTTP request with full error handling per GNews docs
  - Rate limiting (free plan = 1 req/sec)
  - Retry with exponential backoff on 429 and 5xx
  - Structured error logging with the exact GNews error messages

GNews search endpoint:
  GET https://gnews.io/api/v4/search
  Params: q, lang, max, apikey
  Response: { totalArticles, articles: [{id, title, description, content,
              url, image, publishedAt, lang, source: {id,name,url,country}}] }
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .config import (
    GNEWS_API_KEY,
    GNEWS_BASE_URL,
    GNEWS_LANGUAGE,
    GNEWS_MAX_PER_REQUEST,
    MIN_ARTICLES_BEFORE_FALLBACK,
    QUERY_TEMPLATES,
    REQUEST_DELAY_SECONDS,
)

log = __import__("logging").getLogger("news_scraper.gnews")

# ── Retry settings ─────────────────────────────────────────────────────────────
MAX_RETRIES       = 3
RETRY_BACKOFF     = [2, 5, 10]   # seconds to wait before retry 1, 2, 3


class GNewsError(Exception):
    """Raised when GNews returns a non-recoverable error."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"GNews HTTP {status_code}: {message}")


class GNewsQuotaError(GNewsError):
    """Raised on 403 — daily quota exceeded."""


class GNewsRateLimitError(GNewsError):
    """Raised on 429 — too many requests per second."""


# ── Query builder ──────────────────────────────────────────────────────────────

def build_queries(artist: dict) -> list[str]:
    """
    Build GNews query strings for this artist from QUERY_TEMPLATES.
    Returns deduplicated list.
    """
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

def _fetch(query: str, max_results: int = GNEWS_MAX_PER_REQUEST) -> list[dict]:
    """
    Call the GNews search endpoint for one query.
    Returns raw article list from the API.
    Raises GNewsError subclasses for non-recoverable errors.
    Retries on 429 and 5xx with exponential backoff.
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
        "apikey": GNEWS_API_KEY,
    })
    url = f"{GNEWS_BASE_URL}?{params}"
    log.debug("  GET %s", url.replace(GNEWS_API_KEY, "***"))

    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                raw  = resp.read().decode("utf-8")
                data = json.loads(raw)
                articles = data.get("articles", [])
                log.debug("  GNews returned %d articles (total=%s)",
                          len(articles), data.get("totalArticles", "?"))
                return articles

        except urllib.error.HTTPError as exc:
            status = exc.code
            body   = ""
            try:
                body = exc.read().decode("utf-8")
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

            if status == 429:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF)-1)]
                log.warning("  429 Too Many Requests — waiting %ds before retry %d/%d",
                            wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
                last_exc = exc
                continue

            if status >= 500:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF)-1)]
                log.warning("  %d Server Error — waiting %ds before retry %d/%d",
                            status, wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
                last_exc = exc
                continue

            raise GNewsError(status, body) from exc

        except urllib.error.URLError as exc:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF)-1)]
            log.warning("  Network error (attempt %d/%d): %s — waiting %ds",
                        attempt + 1, MAX_RETRIES, exc.reason, wait)
            time.sleep(wait)
            last_exc = exc

        except Exception as exc:
            log.error("  Unexpected error: %s", exc)
            raise

    raise GNewsError(0, f"All {MAX_RETRIES} attempts failed. Last: {last_exc}") from last_exc


# ── Main search function ───────────────────────────────────────────────────────

def search_artist_news(artist: dict) -> tuple[list[dict], list[str]]:
    """
    Search GNews for news articles about one artist.

    Returns (articles: list[dict], queries_used: list[str])
    where each article dict is the raw GNews article augmented with
    a 'query_used' field.

    Strategy:
      - Try the first query template
      - If fewer than MIN_ARTICLES_BEFORE_FALLBACK results come back,
        try the next template and merge results
      - Respect the per-artist max_articles cap
      - Pause REQUEST_DELAY_SECONDS between API calls
    """
    name         = artist.get("name", "unknown")
    max_articles = artist.get("max_articles", GNEWS_MAX_PER_REQUEST)
    queries      = build_queries(artist)

    all_articles:   list[dict] = []
    queries_used:   list[str]  = []

    for i, query in enumerate(queries):
        if len(all_articles) >= max_articles:
            break

        # Only run fallback queries when first query got too few results
        if i > 0 and len(all_articles) >= MIN_ARTICLES_BEFORE_FALLBACK:
            log.debug("  Got %d articles — skipping further query templates", len(all_articles))
            break

        try:
            log.info("  Query: %s", query)
            raw = _fetch(query, max_results=GNEWS_MAX_PER_REQUEST)

            for article in raw:
                article["query_used"] = query

            all_articles.extend(raw)
            queries_used.append(query)
            log.info("  GNews: %d new results for query #%d", len(raw), i + 1)

        except GNewsQuotaError as exc:
            log.error("  QUOTA EXCEEDED — %s. Stopping all requests.", exc)
            raise   # propagate to main — no point continuing

        except GNewsError as exc:
            log.error("  GNews error for '%s': %s", name, exc)
            continue   # try the next query template

        # Respect rate limit between queries (same artist, different queries)
        if i < len(queries) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    log.debug("  Raw total before dedup: %d", len(all_articles))
    return all_articles, queries_used
