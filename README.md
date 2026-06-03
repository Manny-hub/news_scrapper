# MightyCiti GNews Scraper

Fetches news articles for African artists from the GNews API,
applies a strict relevance filter, and saves results to CSV.

## Project structure

```
news_scraper/
├── main.py                       ← entry point (run this)
├── artists.txt                   ← input: one artist per line
├── requirements.txt
├── .env.example                  ← copy to .env, add API key
├── output/                       ← CSV files written here
└── news_scraper/
    ├── config.py                 ← all settings + relevance tuning
    ├── loader.py                 ← reads artists.txt
    ├── gnews.py                  ← GNews API client + error handling
    ├── relevance.py              ← strict relevance scoring engine
    ├── writer.py                 ← writes output CSV
    └── logger.py                 ← shared logger
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set GNEWS_API_KEY=your_key_here
```

Get your API key at https://gnews.io

## Input file format (artists.txt)

One artist per line, fields separated by `|`:

```
artist_name | country | artist_type | max_articles
```

- `country` and `artist_type` are optional (defaults: Nigeria, Music Artist)
- `max_articles` overrides the global limit for that artist only
- Lines starting with `#` are comments and are ignored

Example:
```
Burna Boy          | Nigeria | Music Artist | 5
Davido             | Nigeria | Music Artist | 5
Genevieve Nnaji    | Nigeria | Actress      | 3
Dauda Kahutu Rarara| Nigeria | Music Artist | 5
```

## Usage

```bash
# Always test first — prints accepted articles, no CSV written
python main.py artists.txt --dry-run

# Dry-run with rejection reasons visible
python main.py artists.txt --dry-run --verbose

# Live run — first 3 artists only
python main.py artists.txt --limit 3

# Full run — all artists in artists.txt
python main.py artists.txt

# Use a different input file
python main.py my_artists.txt
```

## Output CSV columns

| Column | Description |
|---|---|
| artist_name | From artists.txt |
| stage_name | From artists.txt |
| country | From artists.txt |
| artist_type | From artists.txt |
| title | Article title from GNews |
| description | Article description from GNews |
| content_preview | First 300 chars of content |
| url | Full article URL |
| image | Article image URL |
| published_at | Publication date (MM/DD/YYYY) |
| source_name | News source name (e.g. BellaNaija) |
| source_url | News source homepage |
| source_country | Country of the source |
| news_type | Inferred: Update / Artist / Music / Movie / Album / Event |
| relevance_score | Score 0–5 from the relevance filter |
| relevance_reason | Why the article was accepted |
| query_used | The GNews query that found it |

## How the relevance filter works

Every article must pass two hard gates before it is scored:

**Hard gate 1 — Name check (non-negotiable)**
The artist's name must appear in the article title OR description.
If absent entirely the article is rejected regardless of anything else.
This prevents general entertainment news from slipping through.

**Hard gate 2 — Domain block**
Articles from blocked domains (social platforms, lyrics sites) are rejected.

**Scoring (must reach MIN_RELEVANCE_SCORE = 2 to pass):**
- `+2` name appears in the article TITLE (primary subject signal)
- `+1` name appears in the description only
- `+1` entertainment keyword found (music, album, concert, film, etc.)
- `+1` source is a known African/Nigerian press outlet
- `-1` description is very short (stub/redirect page)
- `-2` title matches roundup/listicle patterns (Top 10, Best of, etc.)

## Tuning

All thresholds are in `news_scraper/config.py`:

| Setting | Default | Effect |
|---|---|---|
| `MIN_RELEVANCE_SCORE` | 2 | Lower = more results, less precise |
| `MAX_ARTICLES_PER_ARTIST` | 5 | Global cap (overrideable per artist) |
| `GNEWS_MAX_PER_REQUEST` | 10 | GNews free plan max |
| `REQUEST_DELAY_SECONDS` | 1.2 | Stay under GNews free plan rate limit |
| `ENTERTAINMENT_KEYWORDS` | set of ~40 words | Add domain-specific keywords here |
| `PREFERRED_DOMAINS` | Nigerian/African press | Add trusted sources here |
| `BLOCKED_DOMAINS` | Social/lyrics/shop sites | Add noise sources here |

## Error handling

| GNews Error | What happens |
|---|---|
| 400 Bad Request | Logged, skips to next query template |
| 401 Unauthorized | Raises immediately — check your API key |
| 403 Quota exceeded | Stops entire run — resets at 00:00 UTC |
| 429 Too Many Requests | Retries with backoff: 2s → 5s → 10s |
| 500 / 503 Server Error | Retries with backoff: 2s → 5s → 10s |
| Network error | Retries with backoff |



# Always test first — prints accepted articles, no CSV written
python main.py artists.txt --dry-run

# Dry-run with rejection reasons visible
python main.py artists.txt --dry-run --verbose

# Live run — first 3 artists only
python main.py artists.txt --limit 3

# Full run — all artists in artists.txt
python main.py artists.txt

# Use a different input file
python main.py my_artists.txt