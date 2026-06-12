"""Top-level runner for the Luxembourg property monitor.

    python -m src.lux_monitor run                full pipeline + shortlist
    python -m src.lux_monitor run --no-scrape    recompute offline stages only
    python -m src.lux_monitor run --max-pages 1  quick first test (few requests)
    python -m src.lux_monitor shortlist          show the current ranked shortlist
    python -m src.lux_monitor init-db            create tables (dev fallback)

`run` executes scrape -> save -> dedup -> commute -> analyze -> score, then prints
the shortlist. Everything except scraping is offline: commute is an estimate and
description analysis is heuristic, so **no API keys are required**. Email/Telegram
alerts are not implemented yet.

The canonical database is built by Alembic (`alembic upgrade head`). If the
`listings` table is missing, `run`/`shortlist` say so; `--init-db` (or the
`init-db` command) is a dev convenience that calls ``create_all`` instead.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import inspect

from src.lux_monitor.db import get_engine, init_db, session_scope
from src.lux_monitor.scoring import render_shortlist_table, top_listings


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    try:
        from rich.logging import RichHandler

        logging.basicConfig(
            level=level, format="%(message)s", datefmt="[%X]",
            handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
        )
    except Exception:  # pragma: no cover - rich is a declared dep
        logging.basicConfig(level=level)


def _has_listings(engine) -> bool:
    return inspect(engine).has_table("listings")


def _show(session, *, top: int, listing_type: str | None) -> None:
    rows = top_listings(session, limit=top, listing_type=listing_type)
    if not rows:
        print("No scored listings yet — run `python -m src.lux_monitor run` first.")
        return
    render_shortlist_table(rows)


def cmd_run(args: argparse.Namespace) -> int:
    from src.config import load_config
    from src.scrapers.luxembourg import run_luxembourg

    config = load_config(args.config)
    if args.max_pages is not None:
        config.scrapers.max_pages_per_source = args.max_pages

    engine = get_engine(args.db)
    if not _has_listings(engine):
        if args.init_db:
            init_db(engine)
        else:
            print("No 'listings' table. Run `alembic upgrade head` (recommended), "
                  "or re-run with --init-db.")
            return 1

    with session_scope(engine) as session:
        totals = asyncio.run(run_luxembourg(config, session, scrape=not args.no_scrape))
        print("pipeline: " + ", ".join(f"{k}={v}" for k, v in totals.items()))
        _show(session, top=args.top, listing_type=args.type)
    return 0


def cmd_shortlist(args: argparse.Namespace) -> int:
    engine = get_engine(args.db)
    if not _has_listings(engine):
        print("No database yet — run `python -m src.lux_monitor run` first.")
        return 1
    with session_scope(engine) as session:
        _show(session, top=args.top, listing_type=args.type)
    return 0


def cmd_initdb(args: argparse.Namespace) -> int:
    init_db(get_engine(args.db))
    print("Schema created (dev convenience; production uses `alembic upgrade head`).")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    import importlib.util
    import os
    import subprocess
    import sys
    from pathlib import Path

    if importlib.util.find_spec("streamlit") is None:
        print("Streamlit isn't installed. Run:  pip install streamlit")
        return 1
    if args.db:
        os.environ["LUX_MONITOR_DB_URL"] = f"sqlite:///{args.db}"
    app = Path(__file__).with_name("dashboard.py")
    print(f"Starting dashboard on http://{args.host}:{args.port} "
          f"(open it on your phone via this machine's LAN IP). Ctrl-C to stop.")
    return subprocess.call([
        sys.executable, "-m", "streamlit", "run", str(app),
        "--server.address", args.host, "--server.port", str(args.port),
    ])


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="SQLite path (default data/monitor.db or $LUX_MONITOR_DB_URL)")
    common.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    parser = argparse.ArgumentParser(
        prog="python -m src.lux_monitor", description="Luxembourg property monitor"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", parents=[common], help="full pipeline, then show the shortlist")
    run.add_argument("--config", "-c", help="path to config.yaml (optional; LU is auto-registered)")
    run.add_argument("--no-scrape", action="store_true",
                     help="skip scraping; recompute dedup/commute/analysis/score on existing data")
    run.add_argument("--max-pages", type=int,
                     help="override scrapers.max_pages_per_source (e.g. 1 for a quick first test)")
    run.add_argument("--init-db", action="store_true",
                     help="create tables if missing (otherwise require `alembic upgrade head`)")
    run.add_argument("--top", type=int, default=20, help="how many to show (default 20)")
    run.add_argument("--type", choices=("furnished", "rent", "buy"), help="restrict shortlist to one type")
    run.set_defaults(func=cmd_run)

    shortlist = sub.add_parser("shortlist", parents=[common], help="show the ranked shortlist")
    shortlist.add_argument("--top", type=int, default=20)
    shortlist.add_argument("--type", choices=("furnished", "rent", "buy"))
    shortlist.set_defaults(func=cmd_shortlist)

    initdb = sub.add_parser("init-db", parents=[common],
                            help="create tables (dev; prefer `alembic upgrade head`)")
    initdb.set_defaults(func=cmd_initdb)

    dash = sub.add_parser("dashboard", parents=[common],
                          help="launch the browser dashboard (needs `pip install streamlit`)")
    dash.add_argument("--host", default="0.0.0.0",
                      help="bind address (default 0.0.0.0 so a phone on the same Wi-Fi can reach it)")
    dash.add_argument("--port", type=int, default=8501)
    dash.set_defaults(func=cmd_dashboard)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
