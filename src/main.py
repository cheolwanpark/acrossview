"""CLI entry point for the news crawler."""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from .config import CSV_PATH, DB_PATH, DEFAULT_CONCURRENT
from .csv_reader import read_news_csv, get_unique_domains
from .database import Database
from .engine import CrawlEngine
from .logger import logger


async def cmd_init(args: argparse.Namespace) -> int:
    """Initialize database from CSV file.

    Args:
        args: Command line arguments

    Returns:
        Exit code
    """
    csv_path = Path(args.csv) if args.csv else CSV_PATH
    db_path = Path(args.db) if args.db else DB_PATH

    if not csv_path.exists():
        logger.error(f"CSV file not found: {csv_path}")
        return 1

    logger.info(f"Initializing database from {csv_path}")

    # Connect to database
    db = Database(db_path)
    await db.connect()

    try:
        # Read CSV and insert articles
        articles = list(read_news_csv(csv_path))
        logger.info(f"Read {len(articles)} articles from CSV")

        # Batch insert
        batch_size = 1000
        total_inserted = 0

        for i in range(0, len(articles), batch_size):
            batch = articles[i : i + batch_size]
            inserted = await db.insert_articles_batch(batch)
            total_inserted += inserted
            logger.info(f"Inserted batch {i // batch_size + 1}: {inserted} articles")

        logger.info(f"Total inserted: {total_inserted} articles")

        # Show domain stats
        domains = get_unique_domains(csv_path)
        logger.info(f"Found {len(domains)} unique domains:")
        for domain, count in list(domains.items())[:10]:
            logger.info(f"  {domain}: {count}")

        return 0

    finally:
        await db.close()


async def cmd_crawl(args: argparse.Namespace) -> int:
    """Start crawling pending URLs.

    Args:
        args: Command line arguments

    Returns:
        Exit code
    """
    db_path = Path(args.db) if args.db else DB_PATH

    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        logger.error("Run 'init' command first")
        return 1

    # Connect to database
    db = Database(db_path)
    await db.connect()

    try:
        async with CrawlEngine(
            db=db,
            concurrent=args.concurrent,
        ) as engine:
            if args.retry_failed:
                stats = await engine.retry_failed(limit=args.limit)
            else:
                stats = await engine.crawl_pending(
                    domain=args.domain,
                    limit=args.limit,
                )

        return 0

    finally:
        await db.close()


async def cmd_status(args: argparse.Namespace) -> int:
    """Show crawl status and statistics.

    Args:
        args: Command line arguments

    Returns:
        Exit code
    """
    db_path = Path(args.db) if args.db else DB_PATH

    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return 1

    db = Database(db_path)
    await db.connect()

    try:
        # Overall stats
        stats = await db.get_stats()
        print("\n=== Crawl Status ===")
        print(f"Total articles: {stats.get('total', 0)}")
        print(f"  Pending:  {stats.get('pending', 0)}")
        print(f"  Success:  {stats.get('success', 0)}")
        print(f"  Failed:   {stats.get('failed', 0)}")
        print(f"  Blocked:  {stats.get('blocked', 0)}")

        # Domain stats
        domain_stats = await db.get_domain_stats()
        print("\n=== Stats by Domain ===")
        print(f"{'Domain':<25} {'Total':>8} {'Pending':>8} {'Success':>8} {'Failed':>8}")
        print("-" * 65)
        for row in domain_stats:
            print(
                f"{row['domain']:<25} {row['total']:>8} {row['pending']:>8} "
                f"{row['success']:>8} {row['failed']:>8}"
            )

        return 0

    finally:
        await db.close()


async def cmd_run(args: argparse.Namespace) -> int:
    """Run complete pipeline: init + crawl + status.

    Args:
        args: Command line arguments

    Returns:
        Exit code
    """
    csv_path = Path(args.csv) if args.csv else CSV_PATH
    db_path = Path(args.db) if args.db else DB_PATH

    if not csv_path.exists():
        logger.error(f"CSV file not found: {csv_path}")
        return 1

    # Connect to database
    db = Database(db_path)
    await db.connect()

    try:
        # Step 1: Initialize - import CSV if database is empty
        stats = await db.get_stats()
        if stats.get("total", 0) == 0:
            logger.info("Initializing database from CSV...")
            articles = list(read_news_csv(csv_path))
            logger.info(f"Read {len(articles)} articles from CSV")

            batch_size = 1000
            for i in range(0, len(articles), batch_size):
                batch = articles[i : i + batch_size]
                await db.insert_articles_batch(batch)
            logger.info(f"Imported {len(articles)} articles")
        else:
            logger.info(f"Database already has {stats['total']} articles")

        # Step 2: Crawl pending URLs
        async with CrawlEngine(
            db=db,
            concurrent=args.concurrent,
        ) as engine:
            await engine.crawl_pending(
                domain=args.domain,
                limit=args.limit,
            )

        # Step 3: Show final status
        stats = await db.get_stats()
        print("\n=== Final Status ===")
        print(f"Total: {stats.get('total', 0)}")
        print(f"  Success: {stats.get('success', 0)}")
        print(f"  Failed:  {stats.get('failed', 0)}")
        print(f"  Blocked: {stats.get('blocked', 0)}")
        print(f"  Pending: {stats.get('pending', 0)}")

        return 0

    finally:
        await db.close()


async def cmd_export(args: argparse.Namespace) -> int:
    """Export crawled data to CSV.

    Args:
        args: Command line arguments

    Returns:
        Exit code
    """
    db_path = Path(args.db) if args.db else DB_PATH
    output_path = Path(args.output)

    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return 1

    db = Database(db_path)
    await db.connect()

    try:
        # Query successful articles using proper encapsulation
        rows = await db.get_successful_articles()

        if not rows:
            logger.warning("No successful crawls to export")
            return 0

        # Write to CSV
        try:
            with open(output_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "news_id",
                        "url",
                        "domain",
                        "date",
                        "media_outlet",
                        "author",
                        "title_original",
                        "title_crawled",
                        "body_crawled",
                        "published_at",
                        "keywords",
                        "crawled_at",
                    ]
                )
                for row in rows:
                    writer.writerow([row.get(k) for k in [
                        "news_id", "url", "domain", "date", "media_outlet", "author",
                        "title", "title_crawled", "body_crawled", "published_at",
                        "keywords", "crawled_at"
                    ]])
        except IOError as e:
            logger.error(f"Failed to write export file: {e}")
            return 1

        logger.info(f"Exported {len(rows)} articles to {output_path}")
        return 0

    finally:
        await db.close()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Korean News Article Crawler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize database from CSV")
    init_parser.add_argument("--csv", help=f"Path to CSV file (default: {CSV_PATH})")
    init_parser.add_argument("--db", help=f"Path to database (default: {DB_PATH})")

    # crawl command
    crawl_parser = subparsers.add_parser("crawl", help="Start crawling")
    crawl_parser.add_argument("--db", help=f"Path to database (default: {DB_PATH})")
    crawl_parser.add_argument("--domain", help="Only crawl specific domain")
    crawl_parser.add_argument(
        "--limit", type=int, help="Maximum URLs to crawl"
    )
    crawl_parser.add_argument(
        "--concurrent",
        type=int,
        default=DEFAULT_CONCURRENT,
        help=f"Concurrent requests (default: {DEFAULT_CONCURRENT})",
    )
    crawl_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry previously failed URLs",
    )

    # status command
    status_parser = subparsers.add_parser("status", help="Show crawl status")
    status_parser.add_argument("--db", help=f"Path to database (default: {DB_PATH})")

    # export command
    export_parser = subparsers.add_parser("export", help="Export crawled data")
    export_parser.add_argument("--db", help=f"Path to database (default: {DB_PATH})")
    export_parser.add_argument(
        "--output", "-o", required=True, help="Output CSV file path"
    )

    # run command (init + crawl in one step)
    run_parser = subparsers.add_parser("run", help="Run complete pipeline (init + crawl)")
    run_parser.add_argument("--csv", help=f"Path to CSV file (default: {CSV_PATH})")
    run_parser.add_argument("--db", help=f"Path to database (default: {DB_PATH})")
    run_parser.add_argument("--domain", help="Only crawl specific domain")
    run_parser.add_argument("--limit", type=int, help="Maximum URLs to crawl")
    run_parser.add_argument(
        "--concurrent",
        type=int,
        default=DEFAULT_CONCURRENT,
        help=f"Concurrent requests (default: {DEFAULT_CONCURRENT})",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Run appropriate command
    if args.command == "init":
        return asyncio.run(cmd_init(args))
    elif args.command == "crawl":
        return asyncio.run(cmd_crawl(args))
    elif args.command == "status":
        return asyncio.run(cmd_status(args))
    elif args.command == "export":
        return asyncio.run(cmd_export(args))
    elif args.command == "run":
        return asyncio.run(cmd_run(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
