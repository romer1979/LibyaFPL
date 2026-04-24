# -*- coding: utf-8 -*-
"""
Read-only audit for team-based H2H leagues (libyan, arab, cities).

Recomputes each team's GW total from the 3 member managers' picks using
the custom rules (TC=2x, no BB, hits subtracted) and compares to the
stored team1_points / team2_points in team_league_matches.

Flags:
  - Any team whose stored match points differ from the recomputed value
  - Any match whose winner flips under recomputed points (W/L/D change)

Usage:
    python audit_team_league.py libyan
    python audit_team_league.py arab
    python audit_team_league.py cities
"""

import sys
from app import app, db
from models import TeamLeagueMatches
from core.fpl_api import FPL_BASE_URL, fetch_data, fetch_multiple_parallel

# Reuse the scoring logic that the Libyan fix script already uses.
# It implements the custom rules: TC=2x, Bench Boost ignored, hits subtracted,
# plus captain/vice-captain and auto-sub handling.
from fix_gw24_libyan import (
    calculate_manager_points,
    build_live_elements,
    build_player_info,
    get_bootstrap_data,
    get_live_data,
)


def get_teams(league):
    if league == 'libyan':
        from core.libyan_league import TEAMS_FPL_IDS
    elif league == 'arab':
        from core.arab_league import TEAMS_FPL_IDS
    elif league == 'cities':
        from core.cities_league import TEAMS_FPL_IDS
    else:
        raise SystemExit(f"Unknown league: {league}")
    return TEAMS_FPL_IDS


def compute_team_totals_for_gw(gw, teams, player_info):
    """Return {team_name: int} of recomputed points for every team in `teams` for GW `gw`."""
    live = get_live_data(gw)
    if not live:
        return None
    live_elements = build_live_elements(live)

    # All entry IDs across all teams
    all_entries = [eid for entries in teams.values() for eid in entries]

    # Parallel fetch of picks
    urls = [f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/" for eid in all_entries]
    picks = fetch_multiple_parallel(urls)

    # Per-manager custom points
    mgr_points = {}
    for eid in all_entries:
        pd = picks.get(f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/")
        mgr_points[eid] = calculate_manager_points(pd, live_elements, player_info) if pd else 0

    # Team totals
    return {
        team: sum(mgr_points.get(e, 0) for e in entries)
        for team, entries in teams.items()
    }


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('libyan', 'arab', 'cities'):
        print("Usage: python audit_team_league.py {libyan|arab|cities}")
        return
    league_type = sys.argv[1]
    teams = get_teams(league_type)
    print(f"Auditing {league_type} league: {len(teams)} teams, "
          f"{sum(len(v) for v in teams.values())} managers")

    with app.app_context():
        saved_gws = sorted({
            r[0] for r in db.session.query(TeamLeagueMatches.gameweek)
            .filter_by(league_type=league_type).distinct().all()
        })
        if not saved_gws:
            print(f"No saved matches for {league_type}")
            return
        print(f"Saved GWs in team_league_matches: {saved_gws[0]}..{saved_gws[-1]} "
              f"({len(saved_gws)} GWs)")

        print("\nFetching bootstrap...")
        bootstrap = get_bootstrap_data()
        if not bootstrap:
            print("Failed to fetch bootstrap")
            return
        player_info = build_player_info(bootstrap)

        points_diffs = []    # (gw, team, stored, recomputed, delta)
        winner_diffs = []    # (gw, team1, team2, s_p1, s_p2, n_p1, n_p2, s_res, n_res)

        for gw in saved_gws:
            print(f"\nGW{gw}... fetching {sum(len(v) for v in teams.values())} picks")
            recomputed = compute_team_totals_for_gw(gw, teams, player_info)
            if not recomputed:
                print(f"  skipped (no live data)")
                continue

            # Compare to stored matches
            matches = TeamLeagueMatches.query.filter_by(
                league_type=league_type, gameweek=gw
            ).all()
            for m in matches:
                n1 = recomputed.get(m.team1_name, 0)
                n2 = recomputed.get(m.team2_name, 0)
                if n1 != m.team1_points:
                    points_diffs.append((gw, m.team1_name, m.team1_points, n1, n1 - m.team1_points))
                if n2 != m.team2_points:
                    points_diffs.append((gw, m.team2_name, m.team2_points, n2, n2 - m.team2_points))

                def winner(p1, p2):
                    if p1 > p2: return 'team1'
                    if p2 > p1: return 'team2'
                    return 'draw'
                s_res = winner(m.team1_points, m.team2_points)
                n_res = winner(n1, n2)
                if s_res != n_res:
                    winner_diffs.append({
                        'gw': gw,
                        'team1': m.team1_name, 'team2': m.team2_name,
                        's_p1': m.team1_points, 's_p2': m.team2_points,
                        'n_p1': n1, 'n_p2': n2,
                        's_res': s_res, 'n_res': n_res,
                    })

        # Report
        print("\n" + "=" * 72)
        print(f"  {league_type.upper()} LEAGUE AUDIT")
        print("=" * 72)
        print(f"  Team GW points discrepancies: {len(points_diffs)}")
        print(f"  Match winner flips:           {len(winner_diffs)}")

        if points_diffs:
            print(f"\n--- Stored team GW points != recomputed ({len(points_diffs)}) ---")
            for gw, team, stored, new, delta in sorted(points_diffs):
                sign = '+' if delta >= 0 else ''
                print(f"  GW{gw:>2}  {team[:25]:25s}  stored={stored:>3}  recomputed={new:>3}  "
                      f"delta={sign}{delta}")

        if winner_diffs:
            print(f"\n--- Match winner would change under recomputed ({len(winner_diffs)}) ---")
            for d in winner_diffs:
                print(f"  GW{d['gw']:>2}  {d['team1'][:20]:20s} vs {d['team2'][:20]:20s}  "
                      f"stored {d['s_p1']}-{d['s_p2']} ({d['s_res']})  ->  "
                      f"new {d['n_p1']}-{d['n_p2']} ({d['n_res']})")

        if not points_diffs and not winner_diffs:
            print("\n  Clean. No discrepancies.")
        else:
            print("\n  READ-ONLY. Nothing written. If you want to fix, say the word and")
            print("  we'll write a rebuild script.")


if __name__ == '__main__':
    main()
