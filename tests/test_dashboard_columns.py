"""Static consistency checks for the Streamlit dashboard's columns.

The dashboard runs as a script (it calls ``st.*`` at import time) and Streamlit
isn't part of the lean install, so it can't be imported here. But its two column
lists are exactly where a renamed model field turns into a ``KeyError`` on the
operator's machine with nothing to catch it first — the dashboard is the surface
they actually use every day.

So read the source instead: parse ``dashboard.py`` and check that every amenity
names a real ``Listing`` column and every ordered column is one the row loader
actually produces.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.lux_monitor.models import Listing

DASHBOARD = Path(__file__).resolve().parent.parent / "src" / "lux_monitor" / "dashboard.py"

# Columns built after load_rows() returns (from the mortgage sliders).
DERIVED_COLUMNS = {"€/mo"}


def _module() -> ast.Module:
    return ast.parse(DASHBOARD.read_text(encoding="utf-8"))


def _assigned_list(tree: ast.Module, name: str) -> list:
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):  # AMENITIES carries a type annotation
            targets = [node.target]
        if node_value := (node.value if targets else None):
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                return list(ast.literal_eval(node_value))
    raise AssertionError(f"{name} not found in {DASHBOARD.name}")


def _load_rows_keys(tree: ast.Module) -> set[str]:
    """String keys of the record dict built inside load_rows()."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "load_rows":
            keys: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    keys |= {
                        k.value for k in sub.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }
            return keys
    raise AssertionError("load_rows() not found")


def test_amenities_name_real_listing_columns():
    amenities = _assigned_list(_module(), "AMENITIES")
    unknown = [attr for attr, _ in amenities if not hasattr(Listing, attr)]
    assert not unknown, f"AMENITIES references non-columns: {unknown}"


def test_amenity_labels_are_unique():
    labels = [label for _, label in _assigned_list(_module(), "AMENITIES")]
    assert len(labels) == len(set(labels)), "duplicate label would break the 'Must have' filter"


def test_every_ordered_column_is_produced():
    tree = _module()
    produced = _load_rows_keys(tree) | DERIVED_COLUMNS
    # The amenity attrs are splatted in via a comprehension, not literal keys.
    produced |= {attr for attr, _ in _assigned_list(tree, "AMENITIES")}
    missing = [c for c in _assigned_list(tree, "COLUMN_ORDER") if c not in produced]
    assert not missing, f"COLUMN_ORDER names columns load_rows() never builds: {missing}"
