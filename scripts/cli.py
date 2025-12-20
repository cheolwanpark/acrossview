#!/usr/bin/env python3
"""CLI for opposing view finder."""

import argparse
import asyncio
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from tqdm import tqdm

from src.config import DB_PATH
from src.services.article_ranker import ArticleRankerService
from src.services.embedding import EmbeddingService
from src.services.keyword_generator import KeywordGeneratorService
from src.services.opposing_finder import OpposingViewFinder
from src.services.vector_db import SearchResult, VectorDBService

# Load environment variables
load_dotenv()


def print_header(text: str) -> None:
    """Print a formatted header."""
    print("\n" + "━" * 60)
    print(f"  {text}")
    print("━" * 60)


def print_separator() -> None:
    """Print a separator line."""
    print("─" * 60)


def truncate_text(text: str, max_len: int = 50) -> str:
    """Truncate text with ellipsis if too long."""
    if not text:
        return "(제목 없음)"
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


async def cmd_index(args: argparse.Namespace) -> int:
    """Index articles into the vector database.

    Args:
        args: Command line arguments.

    Returns:
        Exit code.
    """
    print_header("Article Indexing")

    try:
        embedding_service = EmbeddingService()
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        print("\nTo set up your API key:")
        print("  1. Copy .env.example to .env")
        print("  2. Add your GEMINI_API_KEY to .env")
        return 1

    with VectorDBService(DB_PATH) as vector_db:
        # Get statistics
        stats = vector_db.get_index_stats()
        print(f"\nTotal successful articles: {stats['total_successful_articles']:,}")
        print(f"Already indexed: {stats['indexed_articles']:,}")
        print(f"Pending indexing: {stats['pending_indexing']:,}")

        if not args.force and stats["pending_indexing"] == 0:
            print("\n✅ All articles are already indexed!")
            print("   Use --force to re-index all articles.")
            return 0

        # Get articles to index
        if args.force:
            # Force re-index: get all successful articles
            articles = vector_db.get_all_successful_articles(limit=args.limit)
        else:
            articles = vector_db.get_unindexed_articles(limit=args.limit)

        if not articles:
            print("\n⚠️  No articles to index.")
            return 0

        print(f"\nIndexing {len(articles):,} articles...")
        print(f"Batch size: {args.batch_size}")

        # Create progress bar
        pbar = tqdm(total=len(articles), desc="Indexing", unit="articles")
        indexed_count = 0
        error_count = 0

        # Process in batches
        for i in range(0, len(articles), args.batch_size):
            batch = articles[i : i + args.batch_size]
            article_ids = [a[0] for a in batch]
            texts = [a[1] for a in batch]

            try:
                # Generate embeddings for batch
                embeddings = await embedding_service.embed_batch(
                    texts, task_type="RETRIEVAL_DOCUMENT"
                )

                # Index the batch
                batch_data = list(zip(article_ids, embeddings))
                count = vector_db.index_batch(batch_data, force=args.force)
                indexed_count += count

            except Exception as e:
                error_count += len(batch)
                tqdm.write(f"⚠️  Batch error: {e}")

            pbar.update(len(batch))

        pbar.close()

        print(f"\n✅ Indexing complete!")
        print(f"   Indexed: {indexed_count:,} articles")
        if error_count > 0:
            print(f"   Errors: {error_count:,} articles")

    return 0


async def cmd_find(args: argparse.Namespace) -> int:
    """Find opposing views for input text.

    Args:
        args: Command line arguments.

    Returns:
        Exit code.
    """
    print_header("Opposing View Finder")

    # Get input text
    print("\nEnter your text (press Ctrl+D on Unix, Ctrl+Z on Windows when done):")
    print()

    try:
        lines = []
        while True:
            try:
                line = input("> ")
                lines.append(line)
            except EOFError:
                break
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        return 1

    input_text = "\n".join(lines).strip()

    if not input_text:
        print("\n❌ No input provided.")
        return 1

    print(f"\n📝 Input received ({len(input_text)} characters)")

    # Initialize services
    try:
        embedding_service = EmbeddingService()
        keyword_generator = KeywordGeneratorService()
        article_ranker = ArticleRankerService()
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        return 1

    with VectorDBService(DB_PATH) as vector_db:
        # Check if articles are indexed
        stats = vector_db.get_index_stats()
        if stats["indexed_articles"] == 0:
            print("\n❌ No articles indexed!")
            print("   Run 'uv run python scripts/cli.py index' first.")
            return 1

        print(f"\n🔍 Searching in {stats['indexed_articles']:,} indexed articles...")

        # Define callbacks for observability
        def on_keywords_generated(topic: str, keywords: list[str]) -> None:
            print(f"\n📌 Topic: {topic}")
            print(f"\n🔑 Generated {len(keywords)} opposing keywords:")
            for i, kw in enumerate(keywords, 1):
                print(f"   {i}. {kw}")

        def on_keyword_search_start(keyword: str) -> None:
            print(f"\n🔎 Searching: \"{keyword}\"")

        def on_keyword_search_complete(
            keyword: str, results: list[SearchResult]
        ) -> None:
            if not results:
                print(f"   └─ No results found")
            else:
                print(f"   └─ Found {len(results)} articles:")
                for j, r in enumerate(results[:5], 1):  # Show top 5
                    title = truncate_text(r.title, 45)
                    print(f"      {j}. [{r.media_outlet or '?'}] {title}")
                if len(results) > 5:
                    print(f"      ... and {len(results) - 5} more")

        def on_ranking_start(candidate_count: int) -> None:
            print(f"\n⚖️  Ranking {candidate_count} candidates with LLM...")

        # Create finder and run
        finder = OpposingViewFinder(
            embedding_service=embedding_service,
            vector_db=vector_db,
            keyword_generator=keyword_generator,
            article_ranker=article_ranker,
        )

        result = await finder.find_opposing_views(
            input_text,
            on_keywords_generated=on_keywords_generated,
            on_keyword_search_start=on_keyword_search_start,
            on_keyword_search_complete=on_keyword_search_complete,
            on_ranking_start=on_ranking_start,
        )

        # Display final results
        print_header("Final Results: Top 5 Opposing Articles")

        print(f"\n📊 Total unique candidates: {result.total_candidates_found}")

        if result.errors:
            print("\n⚠️  Warnings:")
            for error in result.errors:
                print(f"   - {error}")

        if not result.articles:
            print("\n❌ No opposing articles found.")
            return 0

        print()
        for i, article in enumerate(result.articles, 1):
            print_separator()
            print(f"\n{i}. [{article.article_id}] {article.title}")
            print(f"\n   📰 Opposition: {article.opposition_reason}")
            print(f"   📈 Relevance: {article.relevance_score:.2f}")

        print_separator()
        print()

    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    """Show indexing status.

    Args:
        args: Command line arguments.

    Returns:
        Exit code.
    """
    print_header("Index Status")

    try:
        with VectorDBService(DB_PATH) as vector_db:
            stats = vector_db.get_index_stats()

            print(f"\nDatabase: {DB_PATH}")
            print(f"\nTotal successful articles: {stats['total_successful_articles']:,}")
            print(f"Indexed articles: {stats['indexed_articles']:,}")
            print(f"Pending indexing: {stats['pending_indexing']:,}")

            if stats["total_successful_articles"] > 0:
                percent = (
                    stats["indexed_articles"] / stats["total_successful_articles"]
                ) * 100
                print(f"\nProgress: {percent:.1f}%")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Opposing View Finder CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Index command
    index_parser = subparsers.add_parser(
        "index", help="Index articles into vector database"
    )
    index_parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of articles per batch (default: 100)",
    )
    index_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of articles to index",
    )
    index_parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-index all articles",
    )

    # Find command (renamed from find-opposing)
    find_parser = subparsers.add_parser(
        "find", help="Find opposing views for input text"
    )

    # Status command
    status_parser = subparsers.add_parser(
        "status", help="Show indexing status"
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    # Run the appropriate command
    if args.command == "index":
        return asyncio.run(cmd_index(args))
    elif args.command == "find":
        return asyncio.run(cmd_find(args))
    elif args.command == "status":
        return asyncio.run(cmd_status(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
