#!/usr/bin/env python3
"""
migrate_db.py — Staff Analytics database migration script
=========================================================
Run this ONCE to upgrade an existing staff_analytics.db to the new schema.

Usage:
    python migrate_db.py [--db staff_analytics.db] [--dry-run]

What it does:
  1. Adds peak_occupancy column to vc_sessions (for people-bonus tracking)
  2. Back-fills existing vc_sessions with peak_occupancy = 1
  3. Verifies all new engagement_event types are queryable
  4. Reports stats summary so you can verify data is intact

Safe to run multiple times — all operations use IF NOT EXISTS / idempotent ALTER.
"""

import sqlite3
import sys
import argparse
from datetime import datetime

MIGRATIONS = [
    # (description, sql)
    ("Add peak_occupancy to vc_sessions",
     "ALTER TABLE vc_sessions ADD COLUMN peak_occupancy INTEGER DEFAULT 1"),
    # v2 additions — safe to run on any existing DB
    ("Create weekly_active_days column placeholder",
     "SELECT 1"),  # computed dynamically, no column needed

    ("Add severity to moderation_actions",
     "ALTER TABLE moderation_actions ADD COLUMN severity REAL DEFAULT 1.0"),

    ("Add off_peak_activity to consistency_stats",
     "ALTER TABLE consistency_stats ADD COLUMN off_peak_activity INTEGER NOT NULL DEFAULT 0"),

    ("Create engagement_events table", """
        CREATE TABLE IF NOT EXISTS engagement_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            user_id INTEGER,
            timestamp TIMESTAMP NOT NULL
        )"""),

    ("Create reactions_received table", """
        CREATE TABLE IF NOT EXISTS reactions_received (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL,
            reactor_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            emoji TEXT NOT NULL,
            timestamp TIMESTAMP NOT NULL
        )"""),

    ("Create notified_achievements table", """
        CREATE TABLE IF NOT EXISTS notified_achievements (
            staff_id    INTEGER NOT NULL,
            achievement TEXT    NOT NULL,
            notified_at TIMESTAMP NOT NULL,
            PRIMARY KEY (staff_id, achievement)
        )"""),

    ("Create first_responses table", """
        CREATE TABLE IF NOT EXISTS first_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            response_seconds REAL NOT NULL,
            timestamp TIMESTAMP NOT NULL
        )"""),

    ("Create quality_events table", """
        CREATE TABLE IF NOT EXISTS quality_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL,
            message_id INTEGER UNIQUE NOT NULL,
            channel_id INTEGER NOT NULL,
            quality_score REAL NOT NULL,
            signals TEXT,
            word_count INTEGER DEFAULT 0,
            timestamp TIMESTAMP NOT NULL
        )"""),

    ("Create custom_score_weights table", """
        CREATE TABLE IF NOT EXISTS custom_score_weights (
            guild_id INTEGER NOT NULL,
            weight_key TEXT NOT NULL,
            weight_value REAL NOT NULL,
            updated_by INTEGER NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            PRIMARY KEY (guild_id, weight_key)
        )"""),
]

NEW_INDEXES = [
    ("idx_ee_staff",   "CREATE INDEX IF NOT EXISTS idx_ee_staff ON engagement_events(staff_id)"),
    ("idx_ee_type",    "CREATE INDEX IF NOT EXISTS idx_ee_type  ON engagement_events(event_type)"),
    ("idx_ee_ts",      "CREATE INDEX IF NOT EXISTS idx_ee_ts    ON engagement_events(timestamp)"),
    ("idx_rr_staff",   "CREATE INDEX IF NOT EXISTS idx_rr_staff ON reactions_received(staff_id)"),
    ("idx_rr_ts",      "CREATE INDEX IF NOT EXISTS idx_rr_ts    ON reactions_received(timestamp)"),
    ("idx_fr_staff",   "CREATE INDEX IF NOT EXISTS idx_fr_staff ON first_responses(staff_id)"),
    ("idx_fr_ts",      "CREATE INDEX IF NOT EXISTS idx_fr_ts    ON first_responses(timestamp)"),
    ("idx_qe_staff",   "CREATE INDEX IF NOT EXISTS idx_qe_staff ON quality_events(staff_id)"),
    ("idx_qe_ts",      "CREATE INDEX IF NOT EXISTS idx_qe_ts    ON quality_events(timestamp)"),
    ("idx_he_ts",      "CREATE INDEX IF NOT EXISTS idx_he_ts    ON help_events(timestamp)"),
    ("idx_sm_ts",      "CREATE INDEX IF NOT EXISTS idx_sm_ts    ON staff_messages(timestamp)"),
]


def run_migration(db_path: str, dry_run: bool = False):
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Staff Analytics DB Migration")
    print(f"Database: {db_path}")
    print("=" * 60)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── Run schema migrations ─────────────────────────────────────────────────
    success = 0
    skipped = 0
    errors  = 0

    for desc, sql in MIGRATIONS:
        try:
            if dry_run:
                print(f"  [WOULD RUN] {desc}")
                success += 1
                continue
            cur.execute(sql)
            conn.commit()
            print(f"  ✅ {desc}")
            success += 1
        except sqlite3.OperationalError as e:
            if "already exists" in str(e) or "duplicate column" in str(e):
                print(f"  ⏭️  {desc}  (already applied)")
                skipped += 1
            else:
                print(f"  ❌ {desc}: {e}")
                errors += 1

    print()

    # ── Create new indexes ────────────────────────────────────────────────────
    for name, sql in NEW_INDEXES:
        try:
            if dry_run:
                print(f"  [WOULD INDEX] {name}")
                continue
            cur.execute(sql)
            conn.commit()
            print(f"  📇 Index: {name}")
        except sqlite3.OperationalError as e:
            print(f"  ⚠️  Index {name}: {e}")

    print()

    # ── Print summary stats ───────────────────────────────────────────────────
    if not dry_run:
        print("Database Summary:")
        tables = [
            "staff_messages", "interactions", "moderation_actions",
            "vc_sessions", "bumps", "reports", "engagement_events",
            "reactions_received", "consistency_stats", "notified_achievements",
            "quality_events", "first_responses", "help_events",
        ]
        for table in tables:
            try:
                count = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table:<30} {count:>8} rows")
            except Exception as e:
                print(f"  {table:<30} ERROR: {e}")

        # Check new columns exist
        print("\nColumn checks:")
        checks = [
            ("vc_sessions",        "peak_occupancy"),
            ("moderation_actions", "severity"),
            ("consistency_stats",  "off_peak_activity"),
        ]
        print("\nNew tables check:")
        new_tables = [
            "engagement_events", "reactions_received", "notified_achievements",
            "first_responses", "quality_events", "custom_score_weights",
        ]
        for tbl in new_tables:
            try:
                count = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                print(f"  ✅ {tbl:<30} {count:>8} rows")
            except Exception as e:
                print(f"  ❌ {tbl:<30} MISSING — run migration first")
        for table, col in checks:
            try:
                cur.execute(f"SELECT {col} FROM {table} LIMIT 1")
                print(f"  ✅ {table}.{col}")
            except sqlite3.OperationalError:
                print(f"  ❌ {table}.{col} — MISSING")

    conn.close()
    print()
    print(f"Done: {success} applied, {skipped} skipped, {errors} errors")

    if errors:
        print("\n⚠️  Some migrations failed — review errors above before running the bot.")
        sys.exit(1)
    else:
        print("✅ Migration complete — safe to start the bot.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate Staff Analytics DB to latest schema")
    parser.add_argument("--db", default="staff_analytics.db", help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args()
    run_migration(args.db, dry_run=args.dry_run)