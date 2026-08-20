# -*- coding: utf-8 -*-
"""
New-season reset: archive last season's tables inside the DB, then
recreate fresh empty tables for the new season.

For each season table (standings_history, fixture_results,
team_league_standings, team_league_matches, the100_*), this:
  1. Renames the table to <table>_<label>       (e.g. standings_history_2025_26)
  2. Renames its indexes to <index>_<label>     (pkey + unique constraints,
     required in Postgres because index names are schema-wide unique)
  3. Renames its id sequence to <seq>_<label>
  4. After all renames, runs db.create_all() to recreate empty tables.

Old data stays in the database, queryable from the Render shell:
    SELECT * FROM team_league_standings_2025_26 WHERE gameweek = 38;

Safety:
  - DRY-RUN by default: prints the plan and row counts, writes nothing.
  - Aborts if any archive target name already exists (pick another --label).
  - --apply prompts for typed confirmation before touching anything.

Usage on Render shell:
    python new_season_reset.py                      # dry-run, label 2025_26
    python new_season_reset.py --label 2025_26      # explicit label
    python new_season_reset.py --apply              # do it
"""

import re
import sys
from sqlalchemy import text
from app import app, db

# Every season-scoped table in the app
TABLES = [
    'standings_history',
    'fixture_results',
    'team_league_standings',
    'team_league_matches',
    'the100_qualified',
    'the100_eliminations',
    'the100_championship',
    'the100_gw_rankings',
]


def parse_arg_value(flag, default=None):
    try:
        i = sys.argv.index(flag)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    except ValueError:
        return default


def scalar(sql, **params):
    return db.session.execute(text(sql), params).scalar()


def main():
    apply_mode = '--apply' in sys.argv
    label = parse_arg_value('--label', '2025_26')

    if not re.fullmatch(r'[A-Za-z0-9_]+', label):
        print(f"Invalid label '{label}' — letters, digits, underscores only.")
        return

    with app.app_context():
        dialect = db.engine.dialect.name
        if dialect != 'postgresql':
            print(f"This script targets Postgres (Render DB). Current dialect: {dialect}. Aborting.")
            return

        print(f"New-season reset  [{'APPLY' if apply_mode else 'DRY-RUN'}]  archive label: _{label}")
        print("=" * 70)

        plan = []       # (table, rowcount, indexes, sequence)
        conflicts = []

        for table in TABLES:
            exists = scalar("SELECT to_regclass(:t)", t=f"public.{table}")
            if not exists:
                print(f"  {table:30s}  (does not exist — skipped)")
                continue

            target = f"{table}_{label}"
            if scalar("SELECT to_regclass(:t)", t=f"public.{target}"):
                conflicts.append(target)
                continue

            rows = scalar(f"SELECT COUNT(*) FROM {table}")
            idx_rows = db.session.execute(text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = :t"
            ), {'t': table}).fetchall()
            indexes = [r[0] for r in idx_rows]
            seq = scalar("SELECT pg_get_serial_sequence(:t, 'id')", t=f"public.{table}")

            plan.append((table, rows, indexes, seq))
            print(f"  {table:30s}  {rows:>6} rows  ->  {target}")
            for idx in indexes:
                print(f"      index    {idx}  ->  {idx}_{label}")
            if seq:
                seq_name = seq.split('.')[-1]
                print(f"      sequence {seq_name}  ->  {seq_name}_{label}")

        if conflicts:
            print(f"\nABORT: archive name(s) already exist: {conflicts}")
            print("Pick a different --label or drop/rename those first.")
            return

        if not plan:
            print("\nNothing to archive.")
            return

        total_rows = sum(p[1] for p in plan)
        print("=" * 70)
        print(f"  {len(plan)} table(s), {total_rows} total rows to archive.")

        if not apply_mode:
            print("\nDry run. Rerun with --apply to execute.")
            return

        confirm = input(f"\nType 'yes' to archive {len(plan)} tables with label _{label} "
                        f"and recreate empty ones: ")
        if confirm.strip().lower() != 'yes':
            print("Aborted. No changes.")
            return

        # All renames in one transaction
        for table, _, indexes, seq in plan:
            db.session.execute(text(f'ALTER TABLE {table} RENAME TO {table}_{label}'))
            for idx in indexes:
                db.session.execute(text(f'ALTER INDEX {idx} RENAME TO {idx}_{label}'))
            if seq:
                seq_name = seq.split('.')[-1]
                db.session.execute(text(f'ALTER SEQUENCE {seq_name} RENAME TO {seq_name}_{label}'))
            print(f"  archived {table} -> {table}_{label}")
        db.session.commit()

        # Recreate fresh empty tables from the models
        db.create_all()
        print("\nRecreated fresh tables via db.create_all().")

        # Verify
        print("\nVerification:")
        for table in TABLES:
            fresh = scalar("SELECT to_regclass(:t)", t=f"public.{table}")
            archived = scalar("SELECT to_regclass(:t)", t=f"public.{table}_{label}")
            n_fresh = scalar(f"SELECT COUNT(*) FROM {table}") if fresh else '-'
            n_arch = scalar(f"SELECT COUNT(*) FROM {table}_{label}") if archived else '-'
            print(f"  {table:30s}  fresh={n_fresh:>6}  archived({label})={n_arch:>6}")

        print("\nDone. New season starts with empty tables; last season is in *_" + label + " tables.")


if __name__ == '__main__':
    main()
