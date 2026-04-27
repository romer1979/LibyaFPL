# -*- coding: utf-8 -*-
"""
Rebuild Elite league saved standings from FPL net data.

Two independent operations:

  1. Fix gw_points for every StandingsHistory row whose saved value differs
     from the correct net (gross - event_transfers_cost). Cosmetic only —
     does not affect cumulative league_points or H2H result fields.

  2. For each --gw N specified, re-derive the H2H match result/opponent
     from net points and recompute cumulative league_points as
     `prev_GW_corrected_cumulative + delta`. When multiple --gw flags are
     passed they are processed in ascending order, so a downstream GW
     reads the corrected upstream cumulative.

Dry-run by default. --apply to commit.

Examples:
    python rebuild_elite_standings.py
    python rebuild_elite_standings.py --gw 34
    python rebuild_elite_standings.py --gw 33 --gw 34 --apply
"""

import argparse
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


def derive_h2h_results(gw, net_by_entry, entry_to_info):
    """Return {entry_id: {'result','opponent','delta'}} for a GW."""
    out = {}
    matches = fetch_h2h_matches_for_gw(gw)
    for m in matches:
        e1 = m.get('entry_1_entry')
        e2 = m.get('entry_2_entry')
        if not e1 or not e2:
            continue
        if e1 not in entry_to_info or e2 not in entry_to_info:
            continue
        n1 = net_by_entry.get(e1)
        n2 = net_by_entry.get(e2)
        if n1 is None or n2 is None:
            continue
        if n1 > n2:
            out[e1] = {'result': 'W', 'opponent': entry_to_info[e2]['name'], 'delta': 3}
            out[e2] = {'result': 'L', 'opponent': entry_to_info[e1]['name'], 'delta': 0}
        elif n2 > n1:
            out[e1] = {'result': 'L', 'opponent': entry_to_info[e2]['name'], 'delta': 0}
            out[e2] = {'result': 'W', 'opponent': entry_to_info[e1]['name'], 'delta': 3}
        else:
            out[e1] = {'result': 'D', 'opponent': entry_to_info[e2]['name'], 'delta': 1}
            out[e2] = {'result': 'D', 'opponent': entry_to_info[e1]['name'], 'delta': 1}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gw', type=int, action='append', default=[],
                        help='GW(s) whose H2H result + cumulative league_points to rebuild. Repeatable.')
    parser.add_argument('--apply', action='store_true',
                        help='Commit changes. Without this flag, performs a dry run.')
    args = parser.parse_args()

    rebuild_gws = sorted(set(args.gw))
    apply_mode = args.apply

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
        # 1) gw_points discrepancies (all saved GWs)
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
        # 2) H2H + cumulative rebuild for each --gw N (ascending)
        # -----------------------------------------------------------------
        # In-memory cumulative cache so downstream rebuilds read the
        # corrected upstream value, even within a single dry-run.
        corrected_cum = {}  # entry_id -> latest known cumulative

        h2h_updates_by_gw = {}  # {gw: [(row, info, new_lp)]}
        for gw in rebuild_gws:
            if gw not in saved_gws:
                print(f"  ! GW{gw} has no saved standings; skipping")
                continue

            # Seed corrected_cum from prev GW (use already-corrected if available)
            prev_gw = gw - 1
            prev_cum = {}
            if prev_gw in h2h_updates_by_gw:
                # already rebuilt earlier in this run
                for r, _info, new_lp in h2h_updates_by_gw[prev_gw]:
                    prev_cum[r.entry_id] = new_lp
            else:
                # read from DB
                for r in StandingsHistory.query.filter_by(gameweek=prev_gw).all():
                    prev_cum[r.entry_id] = r.league_points or 0

            net_for_gw = net_by_gw_entry.get(gw, {})
            results = derive_h2h_results(gw, net_for_gw, entry_to_info)

            updates = []
            for r in StandingsHistory.query.filter_by(gameweek=gw).all():
                info = results.get(r.entry_id)
                if not info:
                    continue
                base = prev_cum.get(r.entry_id, 0) or 0
                new_lp = base + info['delta']
                if (r.result != info['result']
                        or r.opponent != info['opponent']
                        or r.league_points != new_lp):
                    updates.append((r, info, new_lp))
            h2h_updates_by_gw[gw] = updates

        # -----------------------------------------------------------------
        # Report
        # -----------------------------------------------------------------
        print("=" * 60)
        print(f"  gw_points to fix:       {len(points_fixes)} rows")
        for gw in rebuild_gws:
            n = len(h2h_updates_by_gw.get(gw, []))
            print(f"  GW{gw} H2H+league_pts:    {n} rows")
        print("=" * 60)

        if points_fixes:
            print("\nSample gw_points fixes (first 10):")
            for r, new in points_fixes[:10]:
                print(f"  GW{r.gameweek:>2}  {r.player_name[:25]:25s}  "
                      f"{r.gw_points:>3} -> {new:>3}")
            if len(points_fixes) > 10:
                print(f"  ... and {len(points_fixes) - 10} more")

        for gw in rebuild_gws:
            updates = h2h_updates_by_gw.get(gw, [])
            if not updates:
                continue
            print(f"\nGW{gw} rebuild (all {len(updates)} rows):")
            for r, info, new_lp in updates:
                old = f"{r.result or '-'}/{r.league_points}"
                new = f"{info['result']}/{new_lp}"
                print(f"  {r.player_name[:25]:25s}  {old:>10}  ->  {new:<10}  "
                      f"(+{info['delta']}) vs {info['opponent'][:20]}")

        if not apply_mode:
            print("\nDry run. Rerun with --apply to commit.")
            return

        confirm = input("\nType 'yes' to write these changes: ")
        if confirm.strip().lower() != 'yes':
            print("Aborted.")
            return

        for r, new in points_fixes:
            r.gw_points = new
        for gw in rebuild_gws:
            for r, info, new_lp in h2h_updates_by_gw.get(gw, []):
                r.result = info['result']
                r.opponent = info['opponent']
                r.league_points = new_lp
        db.session.commit()

        print(f"\nCommitted: {len(points_fixes)} gw_points fixes, "
              f"{sum(len(h2h_updates_by_gw.get(g, [])) for g in rebuild_gws)} "
              f"H2H/league_points rows.")


if __name__ == '__main__':
    main()
