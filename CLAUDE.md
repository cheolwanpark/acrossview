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
└── services/       # Diverse view finder services
    ├── embedding.py           # Google GenAI embeddings
    ├── vector_db.py           # sqlite-vss vector search
    ├── keyword_generator.py   # LLM keyword generation (KeywordGeneratorService)
    ├── objection_extractor.py # LLM different view extraction (DifferentViewExtractorService)
    └── opposing_finder.py     # Orchestrator (DiverseViewFinder)

scripts/
└── cli.py          # Diverse view finder CLI
```

---

# Diverse View Finder (다양한 관점 찾기)

하나의 주제에 대해 **다양한 시각과 관점**을 찾아주는 도구입니다.
독자가 여러 관점을 접하고, 보다 균형 잡힌 이해를 가질 수 있도록 돕습니다.

> "반박"이나 "반대"가 아닌, **"이런 관점도 있어요"**라는 친절한 정보 제공에 초점을 맞춥니다.

## Quick Start

```bash
# Set up API key
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY

# Index articles into vector DB (required once)
uv run python scripts/cli.py index

# Find diverse perspectives
uv run python scripts/cli.py find
```

## Pipeline Overview

```
User Input (multiline text)
    ↓
[1] Generate 3-5 keywords for diverse perspectives
    └── KeywordGeneratorService.generate_diverse_keywords()
    ↓
[2] Vector search per keyword (sqlite-vss + gemini-embedding-001)
    ├── Max 10 articles per keyword
    └── Deduplicate by article_id
    ↓
[3] Pre-filter top 15 by distance
    ↓
[4] LLM extraction
    └── DifferentViewExtractorService.extract_different_views()
        ├── Identify claims from input text
        ├── Find different perspectives with article quotes
        └── Validate quotes against source articles
    ↓
[5] Display "함께 생각해볼 다른 관점" with references
```

## Core Classes

| Class | Description |
|-------|-------------|
| `DiverseViewFinder` | 전체 파이프라인 오케스트레이터 |
| `KeywordGeneratorService` | 다양한 관점 검색을 위한 키워드 생성 |
| `DifferentViewExtractorService` | 기사에서 다른 관점 추출 및 인용문 검증 |
| `DiverseViewResult` | 최종 결과 데이터 클래스 |
| `DifferentViewResult` | 개별 다른 관점 항목 |

## Output Format

```python
DiverseViewResult(
    input_summary="주제 요약",
    keywords_used=["키워드1", "키워드2", ...],
    different_views=[
        DifferentViewResult(
            exact_text="원문에서 다른 관점을 제시할 문장",
            different_view="풍부한 맥락과 함께 상세한 다른 관점 설명 (4-6문장)",
            reference=[
                ArticleReference(
                    title="기사 제목",
                    quote="기사에서 인용한 문장",
                    url="https://..."
                )
            ]
        )
    ],
    total_candidates_found=15,
    errors=[]
)
```

## Response Quality

각 `different_view`는 다음 요소를 포함하여 **4-6문장**으로 상세히 작성됩니다:

| 요소 | 설명 | 예시 |
|------|------|------|
| **누가** | 어떤 사람들/단체가 이 시각을 가지는지 | "경제학자들과 시민단체에서는..." |
| **왜** | 이런 다른 시각이 존재하는 배경과 이유 | "급격한 변화가 ... 때문에" |
| **핵심 포인트** | 이 관점에서 우려하거나 강조하는 점 | "소비자 보호가 약화될 수 있다고 우려" |
| **맥락** | 더 넓은 사회적/경제적 연결점 | "과거 유사한 정책 시행 당시의 사례를 근거로" |

### Example Response

```
"이 정책에 대해 경제학자들과 시민단체에서는 다른 시각을 제시하고 있습니다.
일부 전문가들은 급격한 규제 완화가 단기적인 경제 성장에는 도움이 될 수 있지만,
장기적으로는 소비자 보호 장치가 약화될 수 있다고 우려합니다.
특히 중소기업 협회에서는 대기업에 유리한 환경이 조성되면서
시장 내 경쟁이 오히려 줄어들 수 있다는 점을 지적하고 있습니다.
또한 노동계에서는 근로자의 권익 보호가 후순위로 밀릴 수 있다는
염려를 표명하고 있으며, 이는 과거 유사한 정책 시행 당시의
부작용 사례를 근거로 들고 있습니다."
```

### Tone Guidelines

- ✅ "~라는 의견도 있습니다"
- ✅ "~를 우려하는 시각도 있습니다"
- ✅ "~라고 주장합니다"
- ✅ "이런 관점도 있어요"
- ❌ "반박", "반대", "틀렸다"

## CLI Commands

```bash
# Check indexing status
uv run python scripts/cli.py status

# Index articles (with rate limiting)
uv run python scripts/cli.py index
uv run python scripts/cli.py index --batch-size 50
uv run python scripts/cli.py index --force  # Re-index all

# Find diverse perspectives (interactive)
uv run python scripts/cli.py find
```

## CLI Output Example

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  함께 생각해볼 다른 관점
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Total unique candidates: 15
────────────────────────────────────────────────────────────

1. 원문에서:
   "규제 완화가 경제 성장의 핵심이다"

   💬 이런 관점도 있어요:
   이 정책에 대해 경제학자들과 시민단체에서는 다른 시각을 제시하고 있습니다...

   📚 참고 기사:
   - [한겨레 - 규제 완화의 그늘]
     "중소기업들은 오히려 경쟁력을 잃을 수 있다는 우려를 표명했다"
     https://...
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
