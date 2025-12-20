"""Vector database service using sqlite-vss."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import sqlite_vss

from ..config import DB_PATH, EMBEDDING_DIM


@dataclass
class SearchResult:
    """Result from vector similarity search."""

    article_id: int
    distance: float
    title: str | None
    body: str | None
    media_outlet: str | None
    date: str | None


class VectorDBService:
    """Service for vector storage and similarity search using sqlite-vss."""

    def __init__(self, db_path: Path | str | None = None):
        """Initialize the vector DB service.

        Args:
            db_path: Path to SQLite database. Defaults to config DB_PATH.
        """
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.conn: sqlite3.Connection | None = None
        self.dimensions = EMBEDDING_DIM

    def connect(self) -> None:
        """Connect to the database and load sqlite-vss extension."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.enable_load_extension(True)
        sqlite_vss.load(self.conn)
        self._create_tables()

    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> "VectorDBService":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

    def _create_tables(self) -> None:
        """Create the vector search virtual table if not exists."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        # Create virtual table for vector search
        self.conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vss_articles USING vss0(
                embedding({self.dimensions})
            )
        """)

        # Add indexed_at column to articles table if not exists
        try:
            self.conn.execute(
                "ALTER TABLE articles ADD COLUMN indexed_at DATETIME"
            )
        except sqlite3.OperationalError:
            # Column already exists
            pass

        self.conn.commit()

    def is_indexed(self, article_id: int) -> bool:
        """Check if an article is already indexed in the vector DB.

        Args:
            article_id: ID of the article to check.

        Returns:
            True if the article is indexed, False otherwise.
        """
        if not self.conn:
            raise RuntimeError("Database not connected")

        result = self.conn.execute(
            "SELECT 1 FROM vss_articles WHERE rowid = ?", (article_id,)
        ).fetchone()
        return result is not None

    def index_article(
        self, article_id: int, embedding: list[float], force: bool = False
    ) -> bool:
        """Index a single article's embedding.

        Args:
            article_id: ID of the article.
            embedding: Embedding vector.
            force: If True, re-index even if already indexed.

        Returns:
            True if indexed, False if skipped (already indexed).

        Note:
            This method commits the transaction. For batch operations,
            use index_batch() which commits once at the end.
        """
        if not self.conn:
            raise RuntimeError("Database not connected")

        if not force and self.is_indexed(article_id):
            return False

        # Delete existing if force re-indexing
        if force:
            self.conn.execute(
                "DELETE FROM vss_articles WHERE rowid = ?", (article_id,)
            )

        # Insert embedding
        self.conn.execute(
            "INSERT INTO vss_articles(rowid, embedding) VALUES (?, ?)",
            (article_id, json.dumps(embedding)),
        )

        # Update indexed_at timestamp
        self.conn.execute(
            "UPDATE articles SET indexed_at = ? WHERE id = ?",
            (datetime.now().isoformat(), article_id),
        )

        self.conn.commit()
        return True

    def index_batch(
        self,
        articles: list[tuple[int, list[float]]],
        force: bool = False,
    ) -> int:
        """Index multiple articles' embeddings.

        Args:
            articles: List of (article_id, embedding) tuples.
            force: If True, re-index even if already indexed.

        Returns:
            Number of articles indexed.
        """
        if not self.conn:
            raise RuntimeError("Database not connected")

        indexed_count = 0
        for article_id, embedding in articles:
            if self.index_article(article_id, embedding, force=force):
                indexed_count += 1

        self.conn.commit()
        return indexed_count

    def search(
        self, query_embedding: list[float], limit: int = 10
    ) -> list[SearchResult]:
        """Perform KNN search for similar articles.

        Args:
            query_embedding: Query embedding vector.
            limit: Maximum number of results.

        Returns:
            List of search results sorted by distance (ascending).
        """
        if not self.conn:
            raise RuntimeError("Database not connected")

        # sqlite-vss requires LIMIT directly on vss_search query
        # Use subquery to first get matching rowids, then join with articles
        results = self.conn.execute(
            """
            SELECT
                a.id,
                v.distance,
                a.title_crawled,
                a.body_crawled,
                a.media_outlet,
                a.date
            FROM (
                SELECT rowid, distance
                FROM vss_articles
                WHERE vss_search(embedding, ?)
                LIMIT ?
            ) v
            JOIN articles a ON a.id = v.rowid
            ORDER BY v.distance ASC
            """,
            (json.dumps(query_embedding), limit),
        ).fetchall()

        return [
            SearchResult(
                article_id=row[0],
                distance=row[1],
                title=row[2],
                body=row[3],
                media_outlet=row[4],
                date=row[5],
            )
            for row in results
        ]

    def get_unindexed_articles(
        self, limit: int | None = None
    ) -> list[tuple[int, str]]:
        """Get articles that haven't been indexed yet.

        Args:
            limit: Maximum number of articles to return.

        Returns:
            List of (article_id, body_text) tuples.
        """
        if not self.conn:
            raise RuntimeError("Database not connected")

        if limit:
            results = self.conn.execute(
                """
                SELECT id, COALESCE(body_crawled, title_crawled, title) as text
                FROM articles
                WHERE status = 'success'
                  AND indexed_at IS NULL
                  AND (body_crawled IS NOT NULL OR title_crawled IS NOT NULL)
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            results = self.conn.execute(
                """
                SELECT id, COALESCE(body_crawled, title_crawled, title) as text
                FROM articles
                WHERE status = 'success'
                  AND indexed_at IS NULL
                  AND (body_crawled IS NOT NULL OR title_crawled IS NOT NULL)
                """
            ).fetchall()

        return [(row[0], row[1]) for row in results if row[1]]

    def get_all_successful_articles(
        self, limit: int | None = None
    ) -> list[tuple[int, str]]:
        """Get all successfully crawled articles for indexing.

        Args:
            limit: Maximum number of articles to return.

        Returns:
            List of (article_id, body_text) tuples.
        """
        if not self.conn:
            raise RuntimeError("Database not connected")

        if limit:
            results = self.conn.execute(
                """
                SELECT id, COALESCE(body_crawled, title_crawled, title) as text
                FROM articles
                WHERE status = 'success'
                  AND (body_crawled IS NOT NULL OR title_crawled IS NOT NULL)
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            results = self.conn.execute(
                """
                SELECT id, COALESCE(body_crawled, title_crawled, title) as text
                FROM articles
                WHERE status = 'success'
                  AND (body_crawled IS NOT NULL OR title_crawled IS NOT NULL)
                """
            ).fetchall()

        return [(row[0], row[1]) for row in results if row[1]]

    def get_index_stats(self) -> dict:
        """Get statistics about the vector index.

        Returns:
            Dictionary with index statistics.
        """
        if not self.conn:
            raise RuntimeError("Database not connected")

        total_articles = self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE status = 'success'"
        ).fetchone()[0]

        indexed_articles = self.conn.execute(
            "SELECT COUNT(*) FROM vss_articles"
        ).fetchone()[0]

        return {
            "total_successful_articles": total_articles,
            "indexed_articles": indexed_articles,
            "pending_indexing": total_articles - indexed_articles,
        }
