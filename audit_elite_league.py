# -*- coding: utf-8 -*-
"""
Read-only audit for the Elite league (H2H, league ID from config.LEAGUE_ID).

Same bug pattern as The 100: once a GW crosses the 12h post-finish buffer,
core/dashboard.py:887 reads gross points from entry_history.points (FPL's
"before transfer cost" value) and saves that into StandingsHistory.gw_points.
For hit-takers this inflates their GW score and can flip H2H match winners
and, by extension, cumulative league_points.

This script:
  1. Pulls each manager's FPL history once (one /history/ call per manager).
  2. Computes the correct net GW points (history.points - event_transfers_cost).
  3. For every GW already saved in StandingsHistory:
       - Compares saved gw_points to correct net and lists every disagreement.
       - Refetches H2H matches and recomputes each match's winner from net
         points. Lists every match whose stored result is wrong.

Nothing is written. Run from Render Shell:
    python audit_elite_league.py
"""

import sys
from app import app, db
from models import StandingsHistory
from config import LEAGUE_ID, EXCLUDED_PLAYERS
from core.fpl_api import (
    FPL_BASE_URL,
    fetch_data,
    fetch_multiple_parallel,
    get_league_standings,
    get_multiple_entry_history,
)


def fetch_h2h_matches_for_gw(gw):
    """Fetch all H2H match rows for a GW, paginating if needed."""
    rows = []
    page = 1
    while True:
        data = fetch_data(f"{FPL_BASE_URL}/leagues-h2h-matches/league/{LEAGUE_ID}/?event={gw}&page={page}")
        if not data:
            break
        results = data.get('results', [])
        rows.extend(results)
        if not data.get('has_next'):
            break
        page += 1
    return rows


def main():
    with app.app_context():
        # 1. Pull league roster (entries + names)
        print("Fetching league standings...")
        standings_data = get_league_standings(LEAGUE_ID)
        if not standings_data:
            print("Failed to fetch league standings. Aborting.")
            return
        entries = standings_data.get('standings', {}).get('results', [])
        entry_to_info = {}
        for e in entries:
            if e.get('player_name') in EXCLUDED_PLAYERS:
                continue
            entry_to_info[e['entry']] = {
                'name': e.get('player_name', ''),
                'team_name': e.get('entry_name', ''),
            }
        print(f"  roster size: {len(entry_to_info)} (after excluding {EXCLUDED_PLAYERS})")

        # 2. Per-manager history → net GW points lookup
        print("Fetching per-manager histories in parallel...")
        histories = get_multiple_entry_history(list(entry_to_info.keys()))
        print(f"  got history for {len(histories)} managers")

        net_by_gw_entry = {}  # {gw: {entry_id: {'gross':..,'hit':..,'net':..}}}
        for eid, h in histories.items():
            if not h:
                continue
            for gw_entry in h.get('current', []):
                gw = gw_entry.get('event')
                gross = gw_entry.get('points', 0) or 0
                hit = gw_entry.get('event_transfers_cost', 0) or 0
                net_by_gw_entry.setdefault(gw, {})[eid] = {
                    'gross': gross,
                    'hit': hit,
                    'net': gross - hit,
                }

        # 3. GWs to audit: those saved in StandingsHistory
        saved_gws = sorted({r[0] for r in db.session.query(StandingsHistory.gameweek).distinct().all()})
        if not saved_gws:
            print("No saved standings in StandingsHistory. Nothing to audit.")
            return
        print(f"Saved GWs in StandingsHistory: {saved_gws[0]}..{saved_gws[-1]} ({len(saved_gws)} GWs)")

        saved_rows = StandingsHistory.query.filter(
            StandingsHistory.gameweek.in_(saved_gws)
        ).all()
        saved_by_gw_entry = {}  # {(gw, entry_id): row}
        for r in saved_rows:
            saved_by_gw_entry[(r.gameweek, r.entry_id)] = r

        # 4a. gw_points discrepancies
        points_diffs = []
        for (gw, eid), row in saved_by_gw_entry.items():
            ni = net_by_gw_entry.get(gw, {}).get(eid)
            if not ni:
                continue
            if row.gw_points != ni['net']:
                points_diffs.append({
                    'gw': gw,
                    'entry_id': eid,
                    'name': row.player_name or entry_to_info.get(eid, {}).get('name', '?'),
                    'saved': row.gw_points,
                    'net': ni['net'],
                    'gross': ni['gross'],
                    'hit': ni['hit'],
                })

        # 4b. H2H match winner discrepancies
        print("\nFetching H2H matches per GW...")
        matches_by_gw = {}
        for gw in saved_gws:
            matches_by_gw[gw] = fetch_h2h_matches_for_gw(gw)
            print(f"  GW{gw}: {len(matches_by_gw[gw])} matches")

        winner_diffs = []
        for gw in saved_gws:
            for m in matches_by_gw[gw]:
                e1 = m.get('entry_1_entry')
                e2 = m.get('entry_2_entry')
                if not e1 or not e2:
                    continue
                if e1 not in entry_to_info or e2 not in entry_to_info:
                    continue
                n1 = net_by_gw_entry.get(gw, {}).get(e1, {}).get('net')
                n2 = net_by_gw_entry.get(gw, {}).get(e2, {}).get('net')
                if n1 is None or n2 is None:
                    continue

                if n1 > n2:
                    correct_for_e1 = 'W'
                elif n2 > n1:
                    correct_for_e1 = 'L'
                else:
                    correct_for_e1 = 'D'

                row1 = saved_by_gw_entry.get((gw, e1))
                if not row1:
                    continue
                stored_for_e1 = row1.result  # 'W'/'L'/'D'

                if stored_for_e1 != correct_for_e1:
                    winner_diffs.append({
                        'gw': gw,
                        'e1': e1, 'e1_name': entry_to_info[e1]['name'],
                        'e2': e2, 'e2_name': entry_to_info[e2]['name'],
                        'n1': n1, 'n2': n2,
                        'hit1': net_by_gw_entry[gw][e1]['hit'],
                        'hit2': net_by_gw_entry[gw][e2]['hit'],
                        'stored': stored_for_e1,
                        'correct': correct_for_e1,
                    })

        # 5. Report
        print("\n" + "=" * 72)
        print("  ELITE LEAGUE AUDIT")
        print("=" * 72)
        print(f"  GW points discrepancies: {len(points_diffs)}")
        print(f"  H2H match winner flips:  {len(winner_diffs)}")

        if points_diffs:
            print("\n--- Saved gw_points differ from correct net ---")
            print(f"  {'GW':>3}  {'entry':>8}  {'name':25s}  {'saved':>5}  {'net':>3}  "
                  f"{'gross':>5}  {'hit':>3}")
            for d in sorted(points_diffs, key=lambda x: (x['gw'], x['entry_id'])):
                print(f"  {d['gw']:>3}  {d['entry_id']:>8}  {d['name'][:25]:25s}  "
                      f"{d['saved']:>5}  {d['net']:>3}  {d['gross']:>5}  {d['hit']:>3}")

        if winner_diffs:
            print("\n--- H2H matches with wrong stored winner (net-points disagrees) ---")
            for d in sorted(winner_diffs, key=lambda x: x['gw']):
                print(f"  GW{d['gw']:>2}  "
                      f"{d['e1_name'][:20]:20s} (net={d['n1']}, hit={d['hit1']}) vs "
                      f"{d['e2_name'][:20]:20s} (net={d['n2']}, hit={d['hit2']})  "
                      f"stored result for e1: {d['stored']}  correct: {d['correct']}")

        if not points_diffs and not winner_diffs:
            print("\n  Clean. No discrepancies found.")
        else:
            print("\n  These are READ-ONLY findings. Nothing has been written.")
            print("  If you want to fix: we can build a rebuild script that rewrites")
            print("  StandingsHistory.gw_points / result / cumulative league_points.")


if __name__ == '__main__':
    main()
