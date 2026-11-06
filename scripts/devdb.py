"""Local embedded Postgres (pgvector included) for development and tests, no Docker needed.

Usage:
  uv run python scripts/devdb.py            # start (or reuse) and print the asyncpg URL
  uv run python scripts/devdb.py --db NAME  # also (re)create database NAME
"""
import argparse
from pathlib import Path

import pgserver

DATA = Path(__file__).resolve().parent.parent / ".devdata" / "pg"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None, help="drop and recreate this database")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(str(DATA), cleanup_mode=None)
    if args.db:
        srv.psql(f'DROP DATABASE IF EXISTS "{args.db}";')
        srv.psql(f'CREATE DATABASE "{args.db}";')
    print(f"postgresql+asyncpg://postgres@/{args.db or 'postgres'}?host={DATA}")


if __name__ == "__main__":
    main()
