"""
Run every query in queries.txt through crawler.py, skipping queries whose
output folder already exists, with a delay between runs.

Usage:
    python3 run_queries.py
    python3 run_queries.py --queries-file queries.txt --max 200 --min-pixels 250000 --interval 180
"""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_QUERIES_FILE = Path("queries.txt")
DEFAULT_OUTPUT = Path("output")


def load_queries(path: Path) -> list[str]:
    queries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        queries.append(line)
    return queries


def query_folder_name(query: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", query).strip("_")


def main():
    parser = argparse.ArgumentParser(description="Batch-run crawler.py over a query list")
    parser.add_argument("--queries-file", type=Path, default=DEFAULT_QUERIES_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max", type=int, default=200)
    parser.add_argument("--min-pixels", type=int, default=250000)
    parser.add_argument("--interval", type=float, default=180, help="Seconds to wait between queries")
    args = parser.parse_args()

    queries = load_queries(args.queries_file)
    print(f"Loaded {len(queries)} queries from {args.queries_file}")

    pending = []
    for query in queries:
        folder = args.output / query_folder_name(query)
        if folder.exists():
            print(f"SKIP (already crawled): {query!r} → {folder}")
        else:
            pending.append(query)

    print(f"\n{len(pending)} queries to run.\n")

    for i, query in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] Running query: {query!r}")
        cmd = [
            sys.executable, "crawler.py",
            "--query", query,
            "--max", str(args.max),
            "--min-pixels", str(args.min_pixels),
            "--output", str(args.output),
        ]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"  WARNING: crawler.py exited with code {result.returncode} for query {query!r}")

        if i < len(pending):
            print(f"  Waiting {args.interval:.0f}s before next query...\n")
            time.sleep(args.interval)

    print("\nAll queries processed.")


if __name__ == "__main__":
    main()
