"""Tiny idempotent schema fixes for databases created by an older version (create_all never alters tables)."""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger("odeslo.migrations")


def run_migrations(engine: Engine) -> None:
    try:
        _residents_many_homes(engine)
        _jobs_survive_society_delete(engine)
        _deal_subcategory(engine)
        _file_original_name(engine)
    except Exception:  # never block start-up
        log.exception("Schema migration failed")


def _residents_many_homes(engine: Engine) -> None:
    """residents.mobile used to be UNIQUE. A person may now have one row per home (unique per mobile + flat)."""
    insp = inspect(engine)
    if "residents" not in insp.get_table_names():
        return
    old = [ix for ix in insp.get_indexes("residents") if ix["column_names"] == ["mobile"] and ix.get("unique")]
    with engine.begin() as conn:
        for ix in old:
            log.warning("Dropping unique index %s on residents.mobile (multi-home support)", ix["name"])
            conn.execute(text(f'DROP INDEX IF EXISTS "{ix["name"]}"'))
        if old:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_residents_mobile ON residents (mobile)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_resident_mobile_unit ON residents (mobile, unit_id)"))


def _jobs_survive_society_delete(engine: Engine) -> None:
    """service_jobs.society_id becomes optional and society_name is added (vendor history outlives a deleted society)."""
    insp = inspect(engine)
    if "service_jobs" not in insp.get_table_names():
        return
    cols = {c["name"]: c for c in insp.get_columns("service_jobs")}
    with engine.begin() as conn:
        if "society_name" not in cols:
            conn.execute(text("ALTER TABLE service_jobs ADD COLUMN society_name VARCHAR(190)"))
        if engine.dialect.name == "postgresql" and not cols["society_id"]["nullable"]:
            conn.execute(text("ALTER TABLE service_jobs ALTER COLUMN society_id DROP NOT NULL"))


def _deal_subcategory(engine: Engine) -> None:
    """group_deals.subcategory was added with editable categories."""
    insp = inspect(engine)
    if "group_deals" not in insp.get_table_names():
        return
    if "subcategory" not in {c["name"] for c in insp.get_columns("group_deals")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE group_deals ADD COLUMN subcategory VARCHAR(50)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_group_deals_subcategory ON group_deals (subcategory)"))


def _file_original_name(engine: Engine) -> None:
    """stored_files.original_name keeps the real file name (shown when a file is opened instead of its id)."""
    insp = inspect(engine)
    if "stored_files" not in insp.get_table_names():
        return
    if "original_name" not in {c["name"] for c in insp.get_columns("stored_files")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE stored_files ADD COLUMN original_name VARCHAR(190)"))
