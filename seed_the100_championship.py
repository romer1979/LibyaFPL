# -*- coding: utf-8 -*-
"""
Seed The 100 championship bracket.

Step 1: Fetch the 16 alive managers (eliminated_gw IS NULL).
Step 2: Fetch each one's total FPL season points from the API.
Step 3: Rank them by total points desc (tiebreak entry_id asc) and print.
Step 4: Build the 15-match bracket and save it to the_100 championship table.

Run from Render Shell after the elimination replay has been applied:
    python seed_the100_championship.py

Safe to rerun: the script drops and recreates the championship table
every time, so running it twice produces the same final state.
"""

from app import app, db
from models import (
    The100QualifiedManager,
    The100ChampionshipMatch,
    generate_the100_bracket,
)
from core.the100 import fetch_multiple_parallel, get_cookies


def main():
    with app.app_context():
        # Step 1: alive managers
        alive = The100QualifiedManager.query.filter(
            The100QualifiedManager.eliminated_gw.is_(None)
        ).all()
        print(f"Alive managers: {len(alive)}")
        if len(alive) != 16:
            print("Need exactly 16 alive managers. Run replay_the100_eliminations.py "
                  "--apply first so GW33 eliminations are settled.")
            return

        # Step 2: fetch total season points for each (parallel)
        cookies = get_cookies()
        urls = [
            f"https://fantasy.premierleague.com/api/entry/{m.entry_id}/"
            for m in alive
        ]
        print(f"Fetching total_points for {len(urls)} managers...")
        data = fetch_multiple_parallel(urls, cookies)

        totals = {}
        for m in alive:
            url = f"https://fantasy.premierleague.com/api/entry/{m.entry_id}/"
            d = data.get(url) or {}
            totals[m.entry_id] = d.get('summary_overall_points', 0)
            if d.get('summary_overall_points') is None:
                print(f"  WARN: no total for entry {m.entry_id} ({m.manager_name})")

        # Step 3: rank and print
        ranked = sorted(
            alive,
            key=lambda m: (-int(totals.get(m.entry_id, 0)), int(m.entry_id)),
        )
        print("\nFinal seedings (by total season points desc):")
        print(f"  {'Seed':>4}  {'Entry':>8}  {'Total':>5}  Manager  (Team)")
        for i, m in enumerate(ranked, start=1):
            print(f"  {i:>4}  {m.entry_id:>8}  {totals.get(m.entry_id, 0):>5}  "
                  f"{m.manager_name}  ({m.team_name or ''})")

        # Step 4: build bracket
        survivors = [{
            'entry_id': m.entry_id,
            'manager_name': m.manager_name,
            'team_name': m.team_name,
            'total_points': totals.get(m.entry_id, 0),
        } for m in alive]

        print("\nGenerating bracket (drops + recreates the_100_championship)...")
        generate_the100_bracket(survivors)
        count = The100ChampionshipMatch.query.count()
        print(f"Bracket rows: {count}")

        # Print the R16 pairings
        r16 = The100ChampionshipMatch.query.filter_by(round_name='round_16').order_by(
            The100ChampionshipMatch.match_number.asc()
        ).all()
        print("\nR16 matches (standard seeding):")
        for m in r16:
            print(f"  M{m.match_number}: "
                  f"({m.entry_1_seed}) {m.entry_1_name}  vs  "
                  f"({m.entry_2_seed}) {m.entry_2_name}")


if __name__ == '__main__':
    main()
