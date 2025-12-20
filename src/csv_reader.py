"""CSV reader for parsing news-list.csv file."""

import csv
import json
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from .config import CSV_PATH
from .logger import logger


def extract_domain(url: str) -> str | None:
    """Extract root domain from URL.

    Args:
        url: Full URL string

    Returns:
        Root domain without www prefix, or None if invalid
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain if domain else None
    except Exception:
        return None


def parse_categories(cat1: str, cat2: str, cat3: str) -> str | None:
    """Parse category columns into JSON string.

    Args:
        cat1, cat2, cat3: Category column values

    Returns:
        JSON string of non-empty categories
    """
    categories = [c.strip() for c in [cat1, cat2, cat3] if c and c.strip()]
    return json.dumps(categories, ensure_ascii=False) if categories else None


def read_news_csv(csv_path: Path = CSV_PATH) -> Iterator[dict[str, Any]]:
    """Read news articles from CSV file.

    CSV columns (0-indexed):
        0: 뉴스 식별자 (news_id)
        1: 일자 (date)
        2: 언론사 (media_outlet)
        3: 기고자 (author)
        4: 제목 (title)
        5-7: 통합 분류1,2,3 (categories)
        8-10: 사건/사고 분류1,2,3 (incident types)
        11: 인물 (people)
        12: 위치 (location)
        13: 기관 (organizations)
        14: 키워드 (keywords)
        15: 특성추출 (features)
        16: 본문 (body_preview)
        17: URL
        18: 분석제외 여부 (exclusion flag)

    Args:
        csv_path: Path to the CSV file

    Yields:
        Article dictionaries with parsed fields
    """
    logger.info(f"Reading CSV file: {csv_path}")

    seen_urls: set[str] = set()
    total_rows = 0
    valid_rows = 0
    duplicate_rows = 0

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)

        # Skip header
        header = next(reader, None)
        if header:
            logger.debug(f"CSV header: {header}")

        for row in reader:
            total_rows += 1

            # Ensure we have enough columns
            if len(row) < 18:
                logger.warning(f"Row {total_rows} has insufficient columns: {len(row)}")
                continue

            # Extract URL (column 17)
            url = row[17].strip()
            if not url or not url.startswith("http"):
                continue

            # Skip duplicates
            if url in seen_urls:
                duplicate_rows += 1
                continue
            seen_urls.add(url)

            # Extract domain
            domain = extract_domain(url)
            if not domain:
                continue

            # Build article record
            article = {
                "news_id": row[0].strip() if row[0] else None,
                "date": row[1].strip() if row[1] else None,
                "media_outlet": row[2].strip() if row[2] else None,
                "author": row[3].strip() if row[3] else None,
                "title": row[4].strip() if row[4] else None,
                "categories": parse_categories(
                    row[5] if len(row) > 5 else "",
                    row[6] if len(row) > 6 else "",
                    row[7] if len(row) > 7 else "",
                ),
                "keywords": row[14].strip() if len(row) > 14 and row[14] else None,
                "body_preview": row[16].strip() if len(row) > 16 and row[16] else None,
                "url": url,
                "domain": domain,
            }

            valid_rows += 1
            yield article

    logger.info(
        f"CSV parsing complete: {valid_rows} valid articles from {total_rows} rows "
        f"({duplicate_rows} duplicates skipped)"
    )


def get_unique_domains(csv_path: Path = CSV_PATH) -> dict[str, int]:
    """Get unique domains and their counts from CSV.

    Args:
        csv_path: Path to CSV file

    Returns:
        Dictionary mapping domain to article count
    """
    domains: dict[str, int] = {}

    for article in read_news_csv(csv_path):
        domain = article["domain"]
        domains[domain] = domains.get(domain, 0) + 1

    # Sort by count descending
    return dict(sorted(domains.items(), key=lambda x: x[1], reverse=True))
