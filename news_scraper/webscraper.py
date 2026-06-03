"""
webscraper.py
-------------
DDG-powered web scraping fallback that activates when:
  - GNews quota is exceeded (403)
  - GNews returns 0 results for an artist (not in English press)
  - GNews API key is missing

Strategy
--------
For each artist we build targeted search queries that restrict results
to the PREFERRED_DOMAINS list using DDG's site: operator.  This means
we search directly inside bellanaija.com, pulse.ng, notjustok.com etc.
and only return articles from those trusted African entertainment outlets.

Each article page is then fetched and parsed for:
  - title          (og:title or <h1>)
  - description    (og:description or first paragraph)
  - image          (og:image)
  - published_at   (article:published_time or <time datetime>)
  - content        (article body text, first 300 chars)
  - source         (domain name and URL)

The output format exactly mirrors the GNews article dict so the
relevance filter and CSV writer work identically for both sources.
"""

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .config import (
    LOOKBACK_DAYS,
    PREFERRED_DOMAINS,
    REQUEST_DELAY_SECONDS,
)

log = __import__("logging").getLogger("news_scraper.webscraper")

# ── How many DDG results to fetch per query ────────────────────────────────────
DDG_MAX_RESULTS  = 10
# ── How many preferred domains to search per artist ───────────────────────────
# Searching all 17 domains would use too many requests; pick the top ones
TOP_DOMAINS = [
    "pulse.ng",
    "bellanaija.com",
    "notjustok.com",
    "tooxclusive.com",
    "dailypost.ng",
    "premiumtimesng.com",
    "360nobs.com",
    "okayafrica.com",
    "naijaloaded.com.ng",
    "jaguda.com",
    "africanews.com",
    "vanguardngr.com",
    "dailytrust.com",
    "thisdaylive.com",
    "tribuneonlineng.com",
    "sunnewsonline.com",
    "afropop.org",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


# ── DDG client ────────────────────────────────────────────────────────────────

def _ddg_search(query: str, max_results: int = DDG_MAX_RESULTS) -> list[dict]:
    """Search DuckDuckGo text. Returns list of {title, snippet, url}."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
        return [{"title": r.get("title",""), "snippet": r.get("body",""),
                 "url": r.get("href","")} for r in raw]
    except Exception as exc:
        log.warning("DDG search failed for '%s': %s", query[:60], exc)
        return []


# ── Date filter ───────────────────────────────────────────────────────────────

def _within_lookback(published_at: str) -> bool:
    """Return True if the article's date is within LOOKBACK_DAYS of today."""
    if not published_at:
        return True  # can't determine — let it through
    try:
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%d", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(published_at[:19], fmt[:len(published_at[:19])])
                cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
                # make dt timezone-aware if needed
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt >= cutoff
            except ValueError:
                continue
    except Exception:
        pass
    return True


# ── Article page fetcher ──────────────────────────────────────────────────────

def _fetch_article(url: str) -> dict:
    """
    Fetch one article page and extract structured metadata.
    Returns a GNews-compatible article dict.
    Returns {} on failure.
    """
    try:
        req  = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        log.debug("Fetch failed: %s — %s", url[:70], exc)
        return {}

    # ── Title ─────────────────────────────────────────────────────────────
    title = ""
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
                  html, re.I | re.S)
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.I | re.S)
        if m:
            title = re.sub(r'<[^>]+>', '', m.group(1)).strip()

    # ── Description ───────────────────────────────────────────────────────
    description = ""
    m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
                  html, re.I | re.S)
    if m:
        description = m.group(1).strip()
    if not description:
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
                      html, re.I | re.S)
        if m:
            description = m.group(1).strip()

    # ── Content preview ───────────────────────────────────────────────────
    content = ""
    # Strip scripts and styles first
    clean_html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S | re.I)
    clean_html = re.sub(r'<style[^>]*>.*?</style>',  '', clean_html, flags=re.S | re.I)
    paras = re.findall(r'<p[^>]*>(.*?)</p>', clean_html, re.S | re.I)
    para_texts = []
    for p in paras:
        text = re.sub(r'<[^>]+>', '', p).strip()
        text = re.sub(r'\s+', ' ', text)
        if len(text) > 60:
            para_texts.append(text)
        if sum(len(t) for t in para_texts) >= 300:
            break
    content = " ".join(para_texts)[:300]

    # ── Image ─────────────────────────────────────────────────────────────
    image = ""
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']',
                  html, re.I | re.S)
    if m:
        image = m.group(1).strip()

    # ── Published date ────────────────────────────────────────────────────
    published_at = ""
    m = re.search(
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\'](.*?)["\']',
        html, re.I | re.S)
    if m:
        published_at = m.group(1).strip()
    if not published_at:
        m = re.search(r'<time[^>]+datetime=["\'](.*?)["\']', html, re.I)
        if m:
            published_at = m.group(1).strip()

    # ── Source ────────────────────────────────────────────────────────────
    try:
        parsed   = urlparse(url)
        hostname = (parsed.hostname or "").removeprefix("www.")
        src_url  = f"{parsed.scheme}://{parsed.hostname}"
    except Exception:
        hostname = ""
        src_url  = ""

    return {
        "title":       title,
        "description": description,
        "content":     content,
        "url":         url,
        "image":       image,
        "publishedAt": published_at,
        "source": {
            "name":    hostname,
            "url":     src_url,
            "country": "ng",   # all preferred domains are Nigerian/African
        },
        "_source": "webscraper",    # internal tag — not written to CSV
    }


# ── Build search queries ──────────────────────────────────────────────────────

def _build_web_queries(artist: dict) -> list[str]:
    """
    Build DDG site-restricted queries.
    Two strategies:
      1. Search across ALL preferred domains combined (broadest)
      2. Search each top domain individually (deepest)
    """
    name    = (artist.get("name",        "") or "").strip()
    atype   = (artist.get("artist_type", "") or "").strip()
    country = (artist.get("country",     "") or "Nigeria").strip()

    queries = []

    # Strategy 1: broad query across top 5 domains combined with OR
    top5   = " OR ".join(f"site:{d}" for d in TOP_DOMAINS[:5])
    queries.append(f'"{name}" ({top5})')
    queries.append(f'"{name}" {atype} ({top5})')

    # Strategy 2: per-domain queries for remaining top domains
    for domain in TOP_DOMAINS[5:12]:   # domains 6-12 get individual queries
        queries.append(f'"{name}" site:{domain}')

    return queries


# ── Main fallback function ─────────────────────────────────────────────────────

def scrape_artist_news(artist: dict, max_articles: int = 10) -> list[dict]:
    """
    Scrape news articles for one artist from preferred domains via DDG.

    Returns a list of article dicts in GNews format, tagged with:
        query_used   — the DDG query that found them
        window_from  — LOOKBACK_DAYS ago (ISO date string)
        window_to    — today (ISO date string)
        _source      — "webscraper" (internal tag)

    Applies date filtering — only articles within LOOKBACK_DAYS are kept.
    """
    name = artist.get("name", "unknown")
    log.info("  [WEB FALLBACK] Scraping preferred domains for: %s", name)

    queries  = _build_web_queries(artist)
    seen_urls: set[str] = set()
    articles: list[dict] = []

    # Date window labels (whole year)
    now    = datetime.now(timezone.utc)
    w_from = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    w_to   = now.strftime("%Y-%m-%d")

    for i, query in enumerate(queries):
        if len(articles) >= max_articles:
            break

        log.info("    DDG query %d/%d: %s", i + 1, len(queries), query[:80])

        hits = _ddg_search(query)
        log.info("    → %d DDG results", len(hits))
        time.sleep(REQUEST_DELAY_SECONDS)

        for hit in hits:
            url = (hit.get("url", "") or "").strip()
            if not url or url in seen_urls:
                continue

            # Only process URLs from preferred domains
            try:
                domain = urlparse(url).hostname.removeprefix("www.")
            except Exception:
                continue
            if domain not in PREFERRED_DOMAINS:
                log.debug("    Skip non-preferred domain: %s", domain)
                continue

            seen_urls.add(url)

            # Fetch the full article page
            article = _fetch_article(url)
            if not article.get("title"):
                # Fall back to DDG snippet data if fetch failed
                article = {
                    "title":       hit.get("title", ""),
                    "description": hit.get("snippet", ""),
                    "content":     "",
                    "url":         url,
                    "image":       "",
                    "publishedAt": "",
                    "source": {
                        "name":    domain,
                        "url":     f"https://{domain}",
                        "country": "ng",
                    },
                    "_source": "webscraper",
                }

            # Date filter
            if not _within_lookback(article.get("publishedAt", "")):
                log.debug("    Skip — outside lookback window: %s", url[:60])
                continue

            article["query_used"]  = query
            article["window_from"] = w_from
            article["window_to"]   = w_to

            articles.append(article)
            log.debug("    Fetched: %s", (article.get("title", ""))[:70])

            time.sleep(0.5)   # polite pause between page fetches

            if len(articles) >= max_articles:
                break

    log.info("  [WEB FALLBACK] Found %d articles for %s", len(articles), name)
    return articles