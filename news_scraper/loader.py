"""
loader.py
---------
Reads the artists.txt input file.

File format (one artist per line):
    artist_name | country | artist_type | max_articles

Rules:
    - Lines starting with # are comments — ignored
    - Blank lines are ignored
    - Fields after the first (country, artist_type, max_articles) are optional
    - Pipe separator with optional surrounding whitespace

Returns a list of dicts:
    {
        "name":          str,
        "stage_name":    str,   # same as name unless overridden
        "country":       str,
        "artist_type":   str,
        "max_articles":  int,
    }
"""

from pathlib import Path

from .config import MAX_ARTICLES_PER_ARTIST

log = __import__("logging").getLogger("news_scraper.loader")

DEFAULT_COUNTRY      = "Nigeria"
DEFAULT_ARTIST_TYPE  = "Music Artist"


def load_artists(filepath: str) -> list[dict]:
    """Parse the .txt input file and return a list of artist dicts."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(
            f"Artist input file not found: {path.resolve()}\n"
            "Create a plain text file with one artist per line:\n"
            "  Artist Name | Country | Artist Type | max_articles"
        )

    artists: list[dict] = []

    with open(path, encoding="utf-8") as fh:
        for lineno, raw_line in enumerate(fh, 1):
            line = raw_line.strip()

            # Skip comments and blank lines
            if not line or line.startswith("#"):
                continue

            parts = [p.strip() for p in line.split("|")]

            name = parts[0].strip()
            if not name:
                log.warning("Line %d: empty artist name — skipped", lineno)
                continue

            country      = parts[1] if len(parts) > 1 and parts[1] else DEFAULT_COUNTRY
            artist_type  = parts[2] if len(parts) > 2 and parts[2] else DEFAULT_ARTIST_TYPE
            max_articles = MAX_ARTICLES_PER_ARTIST

            if len(parts) > 3 and parts[3]:
                try:
                    max_articles = int(parts[3])
                except ValueError:
                    log.warning("Line %d: invalid max_articles '%s' — using default %d",
                                lineno, parts[3], MAX_ARTICLES_PER_ARTIST)

            artists.append({
                "name":         name,
                "stage_name":   name,   # can be overridden later via CSV if needed
                "country":      country,
                "artist_type":  artist_type,
                "max_articles": max_articles,
            })
            log.debug("Loaded: %s (%s, %s, max=%d)", name, country, artist_type, max_articles)

    log.info("Loaded %d artist(s) from '%s'", len(artists), filepath)
    return artists
