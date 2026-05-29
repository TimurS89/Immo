"""Tests for the top-level orchestrator CLI (src/lux_monitor/__main__.py).

Uses a file-based SQLite DB via --db (the CLI opens its own engine), exercising
the offline pipeline path (--no-scrape) so no network is needed.
"""

from __future__ import annotations

import pytest

from src.lux_monitor.__main__ import build_parser, main
from src.lux_monitor.db import get_engine, init_db, session_scope
from src.lux_monitor.models import Listing
from src.lux_monitor.schemas import ListingCreate


def _seed(db_path) -> None:
    engine = init_db(get_engine(str(db_path)))
    with session_scope(engine) as session:
        session.add(
            ListingCreate(
                portal="athome",
                portal_listing_id="cli1",
                url="https://athome.lu/x",
                commune="Luxembourg",
                listing_type="rent",
                bedrooms=4,
                surface_m2=120.0,
                rent_eur=3000.0,
                description_raw="entierement renove, lumineux et calme",
                description_lang="fr",
                title="Appartement",
            ).to_orm()
        )


def test_parser_defaults_and_required_subcommand():
    args = build_parser().parse_args(["run"])
    assert args.command == "run" and args.top == 20 and args.no_scrape is False
    with pytest.raises(SystemExit):
        build_parser().parse_args([])  # subcommand required


def test_run_requires_schema(tmp_path, capsys):
    rc = main(["run", "--no-scrape", "--db", str(tmp_path / "m.db")])
    assert rc == 1
    assert "alembic upgrade head" in capsys.readouterr().out


def test_run_init_db_on_empty(tmp_path, capsys):
    rc = main(["run", "--no-scrape", "--init-db", "--db", str(tmp_path / "m.db")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pipeline:" in out and "No scored listings" in out


def test_run_scores_seeded_listing(tmp_path, capsys):
    db = tmp_path / "m.db"
    _seed(db)
    rc = main(["run", "--no-scrape", "--db", str(db)])
    assert rc == 0
    assert "scored=1" in capsys.readouterr().out

    with session_scope(get_engine(str(db))) as session:
        listing = session.query(Listing).one()
        assert listing.score_total is not None
        assert listing.llm_quality_score is not None  # analysis ran
        assert listing.drive_time_rush_min is not None  # commute ran


def test_shortlist_without_db(tmp_path, capsys):
    rc = main(["shortlist", "--db", str(tmp_path / "none.db")])
    assert rc == 1
    assert "No database" in capsys.readouterr().out


def test_initdb_command(tmp_path, capsys):
    db = tmp_path / "m.db"
    rc = main(["init-db", "--db", str(db)])
    assert rc == 0 and "Schema created" in capsys.readouterr().out
    from sqlalchemy import inspect

    assert inspect(get_engine(str(db))).has_table("listings")
