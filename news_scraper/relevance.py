"""
relevance.py
------------
Strict relevance filter for GNews articles.

An article passes ONLY if:

  Hard gate 1 — NAME CHECK (non-negotiable)
    The artist's name must appear in the article TITLE
    OR in the article DESCRIPTION.
    If absent entirely → REJECTED with score -5.

  Hard gate 2 — DOMAIN BLOCK
    Articles from blocked domains → REJECTED with score -10.

Scoring (0–5, must reach MIN_RELEVANCE_SCORE to pass):
    +2   name in the TITLE (strongest signal — article is about this artist)
    +1   name in the DESCRIPTION only
    +1   entertainment keyword found in title+description
    +1   source is a known African/Nigerian press outlet
    -1   description is very short (<60 chars) — likely a stub
    -2   roundup / listicle pattern in the title
         AND artist is not clearly the primary subject
"""

import re
from urllib.parse import urlparse

from .config import (
    BLOCKED_DOMAINS,
    ENTERTAINMENT_KEYWORDS,
    MIN_RELEVANCE_SCORE,
    PREFERRED_DOMAINS,
)


def _clean(text: str) -> str:
    """Lowercase and collapse whitespace for matching."""
    return re.sub(r"[^\w\s]", " ", (text or "").lower())


def _name_variants(artist: dict) -> list[str]:
    """
    All searchable name variants for this artist.
    Includes full name, stage name, and common nickname shortenings.
    """
    variants = []
    for key in ("name", "stage_name"):
        val = (artist.get(key, "") or "").strip()
        if not val:
            continue
        variants.append(val.lower())
        parts = val.split()
        if len(parts) >= 3:
            variants.append(parts[-1].lower())           # last word / nickname
            variants.append(f"{parts[0]} {parts[-1]}".lower())  # first + last
        if len(parts) == 2:
            variants.append(parts[0].lower())             # first name alone
    return list(dict.fromkeys(variants))  # dedup, preserve order


def _extract_domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").removeprefix("www.")
    except Exception:
        return ""


def score_article(article: dict, artist: dict) -> tuple[int, str]:
    """
    Score one GNews article dict against one artist dict.

    article keys used: title, description, url, source (dict with 'url')
    artist  keys used: name, stage_name, country

    Returns (score: int, reason: str).
    Negative score = hard gate failed → always reject.
    """
    title   = (article.get("title",       "") or "")
    desc    = (article.get("description", "") or "")
    url     = (article.get("url",         "") or "")
    src_url = (article.get("source",      {}) or {}).get("url", "") or ""

    title_c = _clean(title)
    desc_c  = _clean(desc[:400])

    domain  = _extract_domain(url) or _extract_domain(src_url)

    reasons: list[str] = []
    score   = 0

    # ── Hard gate 1: domain block ──────────────────────────────────────────
    if domain in BLOCKED_DOMAINS:
        return -10, f"blocked domain: {domain}"

    # ── Hard gate 2: name must appear somewhere ────────────────────────────
    variants         = _name_variants(artist)
    name_in_title    = any(v in title_c for v in variants)
    name_in_desc     = any(v in desc_c  for v in variants)

    if not name_in_title and not name_in_desc:
        return -5, "artist name absent from title and description"

    # ── Score: name in title (+2) ──────────────────────────────────────────
    if name_in_title:
        score += 2
        reasons.append("name in title")
    elif name_in_desc:
        score += 1
        reasons.append("name in description")

    # ── Score: entertainment keyword (+1) ─────────────────────────────────
    combined = title_c + " " + desc_c
    kw_hits  = [kw for kw in ENTERTAINMENT_KEYWORDS if kw in combined]
    if kw_hits:
        score += 1
        reasons.append(f"keyword: {kw_hits[0]}")

    # ── Score: preferred domain (+1) ──────────────────────────────────────
    if domain in PREFERRED_DOMAINS:
        score += 1
        reasons.append(f"trusted source: {domain}")

    # ── Penalty: stub description (−1) ────────────────────────────────────
    if len(desc.strip()) < 60:
        score -= 1
        reasons.append("stub description")

    # ── Penalty: roundup / listicle (−2) ──────────────────────────────────
    roundup_words = ["top 10", "top 5", "best of", "list of", "roundup",
                     "10 things", "5 reasons", "artists to watch"]
    if any(rw in title_c for rw in roundup_words):
        if not name_in_title or title_c.count(" and ") > 2:
            score -= 2
            reasons.append("roundup/listicle")

    return score, "; ".join(reasons) if reasons else "no signals"


def is_relevant(article: dict, artist: dict) -> tuple[bool, int, str]:
    """Returns (accepted, score, reason)."""
    score, reason = score_article(article, artist)
    return score >= MIN_RELEVANCE_SCORE, score, reason


def deduplicate(articles: list[dict]) -> list[dict]:
    """Remove exact URL duplicates and near-duplicate titles (>80% overlap)."""
    seen_urls:   set[str]  = set()
    seen_titles: list[str] = []
    unique: list[dict] = []

    for a in articles:
        url   = (a.get("url", "") or "").strip().rstrip("/")
        title = _clean(a.get("title", "") or "")

        if url in seen_urls:
            continue

        title_tokens = set(title.split())
        dup = False
        for prev in seen_titles:
            prev_tokens = set(prev.split())
            if not title_tokens or not prev_tokens:
                continue
            overlap = len(title_tokens & prev_tokens) / max(len(title_tokens), len(prev_tokens))
            if overlap > 0.80:
                dup = True
                break

        if not dup:
            seen_urls.add(url)
            seen_titles.append(title)
            unique.append(a)

    return unique


def infer_news_type(title: str, description: str, artist_type: str) -> str:
    """Infer the MightyCiti News Type from article content."""
    text  = _clean(title + " " + description)
    atype = (artist_type or "").lower()

    if any(w in text for w in ["album", "ep", "mixtape"]):
        return "Album"
    if any(w in text for w in ["single", "track", "music video", "song", "lyrics", "beat"]):
        return "Music"
    if any(w in text for w in ["movie", "film", "series", "nollywood", "cinema"]):
        return "Movie"
    if any(w in text for w in ["concert", "show", "tour", "festival", "event", "live"]):
        return "Event"
    if any(w in text for w in ["actor", "actress", "role", "cast"]) or "actor" in atype:
        return "Artist"
    if any(w in text for w in ["singer", "rapper", "artist", "biography", "interview", "born"]):
        return "Artist"
    return "Update"
