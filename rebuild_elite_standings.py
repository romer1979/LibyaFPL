# -*- coding: utf-8 -*-
"""
Rebuild Elite league saved standings to fix the two issues found by
audit_elite_league.py:

  1. 153 StandingsHistory rows have gross (pre-hit) gw_points; they should
     be net. Cosmetic (wrong GW score shown in history) -- standings table
     unaffected since H2H match `result` was saved during live play when
     the net path was used.

  2. GW33 H2H match results are not recorded (result='-', no cumulative
     league_points delta). We fill these in from FPL's H2H matches endpoint
     using the correct net points, and add 3/1/0 on top of each manager's
     GW32 cumulative league_points.

GW1-32 H2H winners are already correct per the audit, so cumulative
league_points through GW32 don't need rebuilding.

Dry-run by default. --apply to commit.

Run from Render Shell:
    python rebuild_elite_standings.py
    python rebuild_elite_standings.py --apply
"""

import sys
from app import app, db
from models import StandingsHistory
from config import LEAGUE_ID, EXCLUDED_PLAYERS
from core.fpl_api import (
    FPL_BASE_URL,
    fetch_data,
    get_league_standings,
    get_multiple_entry_history,
)

GW33 = 33


def fetch_h2h_matches_for_gw(gw):
    rows = []
    page = 1
    while True:
        data = fetch_data(
            f"{FPL_BASE_URL}/leagues-h2h-matches/league/{LEAGUE_ID}/?event={gw}&page={page}"
        )
        if not data:
            break
        rows.extend(data.get('results', []))
        if not data.get('has_next'):
            break
        page += 1
    return rows


def main():
    apply_mode = '--apply' in sys.argv

    with app.app_context():
        # Roster
        ld = get_league_standings(LEAGUE_ID)
        if not ld:
            print("Failed to fetch league standings")
            return
        entry_to_info = {}
        for e in ld['standings']['results']:
            if e.get('player_name') in EXCLUDED_PLAYERS:
                continue
            entry_to_info[e['entry']] = {
                'name': e.get('player_name', ''),
                'team_name': e.get('entry_name', ''),
            }

        # Histories -> net per (gw, entry)
        histories = get_multiple_entry_history(list(entry_to_info.keys()))
        net_by_gw_entry = {}
        for eid, h in histories.items():
            if not h:
                continue
            for ge in h.get('current', []):
                gw = ge['event']
                gross = ge.get('points', 0) or 0
                hit = ge.get('event_transfers_cost', 0) or 0
                net_by_gw_entry.setdefault(gw, {})[eid] = gross - hit

        saved_gws = sorted({r[0] for r in db.session.query(
            StandingsHistory.gameweek
        ).distinct().all()})
        if not saved_gws:
            print("No saved standings found")
            return

        # -----------------------------------------------------------------
        # Issue 1: wrong gw_points
        # -----------------------------------------------------------------
        rows = StandingsHistory.query.filter(
            StandingsHistory.gameweek.in_(saved_gws)
        ).all()
        points_fixes = []  # (row, new_net)
        for r in rows:
            correct = net_by_gw_entry.get(r.gameweek, {}).get(r.entry_id)
            if correct is None:
                continue
            if r.gw_points != correct:
                points_fixes.append((r, correct))

        # -----------------------------------------------------------------
        # Issue 2: GW33 H2H results missing
        # -----------------------------------------------------------------
        gw33_matches = fetch_h2h_matches_for_gw(GW33)
        gw33_results = {}  # entry_id -> {'result','opponent','delta'}
        for m in gw33_matches:
            e1 = m.get('entry_1_entry')
            e2 = m.get('entry_2_entry')
            if not e1 or not e2:
                continue
            if e1 not in entry_to_info or e2 not in entry_to_info:
                continue
            n1 = net_by_gw_entry.get(GW33, {}).get(e1)
            n2 = net_by_gw_entry.get(GW33, {}).get(e2)
            if n1 is None or n2 is None:
                continue
            if n1 > n2:
                gw33_results[e1] = {'result': 'W', 'opponent': entry_to_info[e2]['name'], 'delta': 3}
                gw33_results[e2] = {'result': 'L', 'opponent': entry_to_info[e1]['name'], 'delta': 0}
            elif n2 > n1:
                gw33_results[e1] = {'result': 'L', 'opponent': entry_to_info[e2]['name'], 'delta': 0}
                gw33_results[e2] = {'result': 'W', 'opponent': entry_to_info[e1]['name'], 'delta': 3}
            else:
                gw33_results[e1] = {'result': 'D', 'opponent': entry_to_info[e2]['name'], 'delta': 1}
                gw33_results[e2] = {'result': 'D', 'opponent': entry_to_info[e1]['name'], 'delta': 1}

        gw32_cum = {r.entry_id: r.league_points for r in StandingsHistory.query.filter_by(gameweek=32).all()}

        gw33_updates = []  # (row, info, new_league_pts)
        for r in StandingsHistory.query.filter_by(gameweek=GW33).all():
            info = gw33_results.get(r.entry_id)
            if not info:
                continue
            base = gw32_cum.get(r.entry_id, 0) or 0
            new_lp = base + info['delta']
            if r.result != info['result'] or r.opponent != info['opponent'] or r.league_points != new_lp:
                gw33_updates.append((r, info, new_lp))

        # -----------------------------------------------------------------
        # Report
        # -----------------------------------------------------------------
        print("=" * 60)
        print(f"  gw_points to fix:       {len(points_fixes)} rows")
        print(f"  GW33 H2H to fill in:    {len(gw33_updates)} rows")
        print("=" * 60)

        print("\nSample gw_points fixes (first 10):")
        for r, new in points_fixes[:10]:
            print(f"  GW{r.gameweek:>2}  {r.player_name[:25]:25s}  "
                  f"{r.gw_points:>3} -> {new:>3}")
        if len(points_fixes) > 10:
            print(f"  ... and {len(points_fixes) - 10} more")

        print("\nGW33 H2H fills (all):")
        for r, info, new_lp in gw33_updates:
            print(f"  {r.player_name[:25]:25s}  {info['result']}  vs {info['opponent'][:25]:25s}  "
                  f"+{info['delta']}  ->  league_pts {new_lp}")

        if not apply_mode:
            print("\nDry run. Rerun with --apply to commit.")
            return

        confirm = input("\nType 'yes' to write these changes: ")
        if confirm.strip().lower() != 'yes':
            print("Aborted.")
            return

        for r, new in points_fixes:
            r.gw_points = new
        for r, info, new_lp in gw33_updates:
            r.result = info['result']
            r.opponent = info['opponent']
            r.league_points = new_lp
        db.session.commit()
        print(f"\nCommitted: {len(points_fixes)} gw_points fixes, "
              f"{len(gw33_updates)} GW33 H2H rows filled in.")


if __name__ == '__main__':
    main()
