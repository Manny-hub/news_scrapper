"""
config.py
---------
All configuration for the MightyCiti GNews scraper.
Set GNEWS_API_KEY in your .env file before running.
"""

import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

_env = find_dotenv(usecwd=True)
if _env:
    load_dotenv(dotenv_path=_env, override=True)

# ── GNews API ─────────────────────────────────────────────────────────────────
GNEWS_API_KEY   = os.getenv("GNEWS_API_KEY", "")
GNEWS_BASE_URL  = "https://gnews.io/api/v4/search"
GNEWS_LANGUAGE  = "en"
GNEWS_MAX_PER_REQUEST = 10   # GNews max per request on free plan is 10

# ── Rate limiting (GNews free plan = 1 req/sec) ───────────────────────────────
REQUEST_DELAY_SECONDS = 1.2   # slightly over 1s to stay safely under the limit

# ── Relevance filtering ───────────────────────────────────────────────────────
MIN_RELEVANCE_SCORE    = 2    # articles scoring below this are discarded
MAX_ARTICLES_PER_ARTIST = 5   # default cap — overrideable per artist in .txt file

# ── Output ────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent.parent
OUTPUT_DIR  = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Relevance: keywords that signal entertainment / music content ──────────────
# At least one must appear in the article title or description
ENTERTAINMENT_KEYWORDS = {
    "music", "song", "album", "single", "concert", "tour", "release",
    "track", "video", "award", "performance", "singer", "artist", "rapper",
    "producer", "studio", "fans", "hit", "chart", "stream", "spotify",
    "audiomack", "afrobeats", "afropop", "hausa", "kannywood", "nollywood",
    "musician", "melody", "record", "debut", "ep", "mixtape",
    "collaboration", "feature", "lyrics", "beat", "genre",
    "entertainment", "celebrity", "star", "band", "actor", "actress",
    "film", "movie", "comedy", "show",
}

# ── Relevance: domains that are BLOCKED ───────────────────────────────────────
BLOCKED_DOMAINS = {
    "pinterest.com", "quora.com", "reddit.com", "wikipedia.org",
    "amazon.com", "ebay.com", "aliexpress.com",
    "azlyrics.com", "genius.com", "lyrics.com",
}

# ── Known African/Nigerian entertainment press (boosts relevance score) ────────
PREFERRED_DOMAINS = {
    "bellanaija.com", "pulse.ng", "vanguardngr.com", "thecable.ng",
    "dailypost.ng", "premiumtimesng.com", "guardian.ng",
    "notjustok.com", "tooxclusive.com", "jaguda.com",
    "naijaloaded.com.ng", "360nobs.com", "afropop.org",
    "okayafrica.com", "africanews.com", "dailytrust.com",
    "tribuneonlineng.com", "sunnewsonline.com", "thisdaylive.com",
}

# ── GNews query templates — tried in order ────────────────────────────────────
# {name}, {stage_name}, {country}, {artist_type} are substituted at runtime.
# The first template is always used; subsequent ones are used as fallbacks
# if the first returns fewer than MIN_ARTICLES_BEFORE_FALLBACK results.
MIN_ARTICLES_BEFORE_FALLBACK = 2

QUERY_TEMPLATES = [
    '"{name}" {artist_type}',
    '"{name}" music news',
    '"{name}" {country}',
    '"{stage_name}" musician',      # only when stage_name differs from name
]

# ── CSV output columns (in order) ─────────────────────────────────────────────
CSV_COLUMNS = [
    "artist_name",
    "stage_name",
    "country",
    "artist_type",
    "title",
    "description",
    "content_preview",
    "url",
    "image",
    "published_at",
    "source_name",
    "source_url",
    "source_country",
    "news_type",
    "relevance_score",
    "relevance_reason",
    "query_used",
]
