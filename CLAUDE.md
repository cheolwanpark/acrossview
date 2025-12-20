# Korean News Crawler

## Quick Start

```bash
# Install dependencies
uv sync

# Run complete pipeline (init + crawl)
uv run python -m src.main run

# Or with options
uv run python -m src.main run --limit 100 --concurrent 10
```

## Crawling Logic

### Pipeline Overview

```
CSV File (news-list.csv)
    ↓
[1] Parse CSV & Extract URLs
    ↓
[2] Insert into SQLite (status='pending')
    ↓
[3] Async Crawl Engine
    ├── Per-domain rate limiting (1 req/sec)
    ├── Global concurrency limit (default: 5)
    └── Retry with exponential backoff (max 3)
    ↓
[4] Extract Content (trafilatura)
    ↓
[5] Update SQLite (status='success'|'failed'|'blocked')
```

### Key Components

**Rate Limiting**: Each domain has independent rate limiting. Requests to `khan.co.kr` don't block requests to `hani.co.kr`.

**Content Extraction**: Uses `trafilatura` library for robust article body extraction. Falls back to BeautifulSoup for title/date metadata.

**Error Classification**:
- `success`: Article extracted successfully
- `failed`: Temporary error (5xx, timeout) - can retry
- `blocked`: Permanent error (403, 404, parse failure) - don't retry

### Retry Logic

```
Attempt 1 → fail → wait 2s
Attempt 2 → fail → wait 4s
Attempt 3 → fail → mark as failed
```

## Database Schema

```sql
CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identifiers
    news_id TEXT UNIQUE,          -- Original news ID from CSV
    url TEXT UNIQUE NOT NULL,     -- Source URL
    domain TEXT NOT NULL,         -- Extracted domain (e.g., khan.co.kr)

    -- Metadata from CSV
    date TEXT,                    -- Publication date (YYYYMMDD)
    media_outlet TEXT,            -- News outlet name (e.g., 경향신문)
    author TEXT,                  -- Author name
    title TEXT,                   -- Original title from CSV
    categories TEXT,              -- JSON array of categories
    keywords TEXT,                -- Keywords
    body_preview TEXT,            -- Truncated body from CSV (~200 chars)

    -- Crawled Content
    title_crawled TEXT,           -- Full title from webpage
    body_crawled TEXT,            -- Full article body (trafilatura)
    published_at TEXT,            -- Extracted publish date

    -- Status Tracking
    status TEXT DEFAULT 'pending',  -- pending|success|failed|blocked
    error_message TEXT,             -- Error details if failed
    attempts INTEGER DEFAULT 0,     -- Retry count
    crawled_at DATETIME,            -- When crawled
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE UNIQUE INDEX idx_url ON articles(url);
CREATE INDEX idx_domain_status ON articles(domain, status);
CREATE INDEX idx_status ON articles(status);
```

## CLI Commands

```bash
# Initialize database from CSV
uv run python -m src.main init

# Start crawling
uv run python -m src.main crawl --limit 100 --concurrent 5

# Crawl specific domain
uv run python -m src.main crawl --domain khan.co.kr

# Retry failed URLs
uv run python -m src.main crawl --retry-failed

# Check progress
uv run python -m src.main status

# Export results
uv run python -m src.main export -o results.csv

# All-in-one (init + crawl)
uv run python -m src.main run
```

## Project Structure

```
src/
├── config.py       # Constants (timeouts, headers, rate limits)
├── logger.py       # Logging setup
├── database.py     # Async SQLite operations
├── csv_reader.py   # CSV parsing
├── crawler/
│   └── base.py     # Crawler with trafilatura
├── engine.py       # Async scheduler + rate limiter
├── main.py         # CLI entry point
└── services/       # Opposing view finder services
    ├── embedding.py          # Google GenAI embeddings
    ├── vector_db.py          # sqlite-vss vector search
    ├── keyword_generator.py  # LLM keyword generation
    ├── article_ranker.py     # LLM article ranking
    └── opposing_finder.py    # Orchestrator

scripts/
└── cli.py          # Opposing view finder CLI
```

---

# Opposing View Finder

Find articles with opposing/challenging views using vector search and LLM ranking.

## Quick Start

```bash
# Set up API key
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY

# Index articles into vector DB (required once)
uv run python scripts/cli.py index

# Find opposing views
uv run python scripts/cli.py find
```

## Pipeline Overview

```
User Input (multiline text)
    ↓
[1] Generate 3-5 opposing keywords (gemini-2.5-flash-lite)
    ↓
[2] Vector search per keyword (sqlite-vss + gemini-embedding-001)
    ├── Max 10 articles per keyword
    └── Deduplicate by article_id
    ↓
[3] Pre-filter top 15 by distance
    ↓
[4] LLM ranking for final 5 (gemini-2.5-flash-lite)
    ↓
[5] Display results with opposition reasoning
```

## CLI Commands

```bash
# Check indexing status
uv run python scripts/cli.py status

# Index articles (with rate limiting)
uv run python scripts/cli.py index
uv run python scripts/cli.py index --batch-size 50
uv run python scripts/cli.py index --force  # Re-index all

# Find opposing views (interactive)
uv run python scripts/cli.py find
```

## Vector Database

Uses `sqlite-vss` extension for vector similarity search:

```sql
-- Virtual table for embeddings (768-dim from gemini-embedding-001)
CREATE VIRTUAL TABLE vss_articles USING vss0(
    embedding(768)
);
-- rowid maps to articles.id
```

## Environment Variables

```bash
GEMINI_API_KEY=your-api-key  # Required for embedding and LLM
```

## Rate Limiting

- **Batch size**: 100 texts per API request
- **Batch delay**: 2 seconds between batches
- **Retry**: Exponential backoff (2s → 4s → 8s → 16s → 32s)
- **Max retries**: 5 attempts per batch
