"""Async SQLite database operations for the news crawler."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from .config import DB_PATH, DATA_DIR
from .logger import logger

# SQL schema
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT UNIQUE,
    url TEXT UNIQUE NOT NULL,
    domain TEXT NOT NULL,

    -- From CSV
    date TEXT,
    media_outlet TEXT,
    author TEXT,
    title TEXT,
    categories TEXT,
    keywords TEXT,
    body_preview TEXT,

    -- Crawled data
    title_crawled TEXT,
    body_crawled TEXT,
    published_at TEXT,

    -- Status tracking
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'success', 'failed', 'blocked')),
    error_message TEXT,
    attempts INTEGER DEFAULT 0,
    crawled_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_INDEXES_SQL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_url ON articles(url);",
    "CREATE INDEX IF NOT EXISTS idx_domain_status ON articles(domain, status);",
    "CREATE INDEX IF NOT EXISTS idx_status ON articles(status);",
]


class Database:
    """Async SQLite database wrapper."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Connect to the database and create tables if needed."""
        # Create parent directory for custom db paths
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrent write performance
        await self._connection.execute("PRAGMA journal_mode=WAL")

        # Create tables
        await self._connection.execute(CREATE_TABLE_SQL)
        for index_sql in CREATE_INDEXES_SQL:
            await self._connection.execute(index_sql)
        await self._connection.commit()

        logger.info(f"Connected to database: {self.db_path}")

    async def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed")

    async def insert_article(self, article: dict[str, Any]) -> bool:
        """Insert a new article record.

        Args:
            article: Article data dictionary

        Returns:
            True if inserted, False if duplicate
        """
        async with self._lock:
            try:
                await self._connection.execute(
                    """
                    INSERT INTO articles (
                        news_id, url, domain, date, media_outlet, author,
                        title, categories, keywords, body_preview
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article.get("news_id"),
                        article["url"],
                        article["domain"],
                        article.get("date"),
                        article.get("media_outlet"),
                        article.get("author"),
                        article.get("title"),
                        article.get("categories"),
                        article.get("keywords"),
                        article.get("body_preview"),
                    ),
                )
                await self._connection.commit()
                return True
            except aiosqlite.IntegrityError:
                # Duplicate URL
                return False

    async def insert_articles_batch(self, articles: list[dict[str, Any]]) -> int:
        """Insert multiple articles in a batch.

        Args:
            articles: List of article data dictionaries

        Returns:
            Number of articles inserted
        """
        inserted = 0
        async with self._lock:
            for article in articles:
                try:
                    cursor = await self._connection.execute(
                        """
                        INSERT OR IGNORE INTO articles (
                            news_id, url, domain, date, media_outlet, author,
                            title, categories, keywords, body_preview
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            article.get("news_id"),
                            article["url"],
                            article["domain"],
                            article.get("date"),
                            article.get("media_outlet"),
                            article.get("author"),
                            article.get("title"),
                            article.get("categories"),
                            article.get("keywords"),
                            article.get("body_preview"),
                        ),
                    )
                    # Use rowcount to check if insert succeeded (not ignored)
                    if cursor.rowcount > 0:
                        inserted += 1
                except Exception as e:
                    logger.warning(f"Failed to insert article: {e}")
            await self._connection.commit()
        return inserted

    async def get_pending_urls(
        self, domain: str | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Get URLs that haven't been crawled yet.

        Args:
            domain: Filter by domain (optional)
            limit: Maximum number of URLs to return (optional)

        Returns:
            List of article records with pending status
        """
        query = "SELECT id, url, domain FROM articles WHERE status = 'pending'"
        params: list[Any] = []

        if domain:
            query += " AND domain = ?"
            params.append(domain)

        query += " ORDER BY id"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        async with self._connection.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_failed_urls(
        self, max_attempts: int = 3, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Get URLs that failed but can be retried.

        Args:
            max_attempts: Maximum retry attempts
            limit: Maximum number of URLs to return

        Returns:
            List of article records that can be retried
        """
        query = """
            SELECT id, url, domain, attempts
            FROM articles
            WHERE status = 'failed' AND attempts < ?
            ORDER BY attempts, id
        """
        params: list[Any] = [max_attempts]

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        async with self._connection.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def update_crawl_result(
        self,
        article_id: int,
        success: bool,
        title: str | None = None,
        body: str | None = None,
        published_at: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update article with crawl results.

        Args:
            article_id: Article database ID
            success: Whether crawl was successful
            title: Crawled title
            body: Crawled body text
            published_at: Published date string
            error_message: Error message if failed
        """
        now = datetime.now().isoformat()
        status = "success" if success else "failed"

        async with self._lock:
            await self._connection.execute(
                """
                UPDATE articles
                SET status = ?,
                    title_crawled = ?,
                    body_crawled = ?,
                    published_at = ?,
                    error_message = ?,
                    attempts = attempts + 1,
                    crawled_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, title, body, published_at, error_message, now, now, article_id),
            )
            await self._connection.commit()

    async def mark_blocked(self, article_id: int, error_message: str) -> None:
        """Mark article as permanently blocked (403, 404, etc.).

        Args:
            article_id: Article database ID
            error_message: Reason for blocking
        """
        now = datetime.now().isoformat()
        async with self._lock:
            await self._connection.execute(
                """
                UPDATE articles
                SET status = 'blocked',
                    error_message = ?,
                    attempts = attempts + 1,
                    crawled_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_message, now, now, article_id),
            )
            await self._connection.commit()

    async def get_stats(self) -> dict[str, int]:
        """Get crawl statistics.

        Returns:
            Dictionary with counts by status
        """
        async with self._connection.execute(
            """
            SELECT status, COUNT(*) as count
            FROM articles
            GROUP BY status
            """
        ) as cursor:
            rows = await cursor.fetchall()
            stats = {row["status"]: row["count"] for row in rows}

        # Add total
        stats["total"] = sum(stats.values())
        return stats

    async def get_domain_stats(self) -> list[dict[str, Any]]:
        """Get statistics by domain.

        Returns:
            List of domain stats
        """
        async with self._connection.execute(
            """
            SELECT domain,
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                   SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                   SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) as blocked
            FROM articles
            GROUP BY domain
            ORDER BY total DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_successful_articles(self) -> list[dict[str, Any]]:
        """Get all successfully crawled articles.

        Returns:
            List of article dictionaries
        """
        async with self._connection.execute(
            """
            SELECT news_id, url, domain, date, media_outlet, author,
                   title, title_crawled, body_crawled, published_at,
                   keywords, crawled_at
            FROM articles
            WHERE status = 'success'
            ORDER BY id
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
