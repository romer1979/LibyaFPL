# -*- coding: utf-8 -*-
"""
GW36 diagnostic for cities / arab / libyan leagues.

Prints, for each league:
  1. Saved matches in team_league_matches (GW36)        -> what's stored
  2. Saved standings in team_league_standings (GW35, GW36) -> base + current
  3. Live-recomputed team GW36 totals from FPL picks    -> what's correct
  4. Per-team delta (stored vs recomputed) and any winner flips

Read-only. Does not write anything.

Usage on Render shell:
    python diagnose_gw36.py
    python diagnose_gw36.py cities          # one league only
    python diagnose_gw36.py --gw 35         # different GW
"""

import sys
from app import app, db
from models import TeamLeagueMatches, TeamLeagueStandings
from core.fpl_api import (
    FPL_BASE_URL,
    fetch_data,
    fetch_multiple_parallel,
    get_multiple_entry_history,
)
from fix_gw24_libyan import (
    calculate_manager_points,
    build_live_elements,
    build_player_info,
    get_bootstrap_data,
    get_live_data,
)

LEAGUES = ('cities', 'arab', 'libyan')


def get_teams(league):
    if league == 'libyan':
        from core.libyan_league import TEAMS_FPL_IDS
    elif league == 'arab':
        from core.arab_league import TEAMS_FPL_IDS
    elif league == 'cities':
        from core.cities_league import TEAMS_FPL_IDS
    return TEAMS_FPL_IDS


def recompute_team_totals(gw, teams, player_info, histories):
    """Recompute each team's GW total live from FPL picks. Returns {team: int} or None."""
    import time as _time
    live = get_live_data(gw)
    if not live:
        return None
    live_elements = build_live_elements(live)

    all_entries = [eid for ents in teams.values() for eid in ents]
    urls = [f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/" for eid in all_entries]
    picks_map = fetch_multiple_parallel(urls)
    picks_by_entry = {
        eid: picks_map.get(f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/")
        for eid in all_entries
    }

    missing = [eid for eid, pd in picks_by_entry.items() if not pd]
    for eid in missing:
        for _ in range(2):
            try:
                retry = fetch_data(f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/")
                if retry:
                    picks_by_entry[eid] = retry
                    break
            except Exception:
                pass
            _time.sleep(1.5)

    # If still missing, check history: 0 pts / no entry = didn't play; >0 = real failure
    real_failures = []
    confirmed_absent = []
    for eid, pd in picks_by_entry.items():
        if pd:
            continue
        h = histories.get(eid)
        if not h:
            real_failures.append(eid)
            continue
        gw_entry = next((g for g in h.get('current', []) if g.get('event') == gw), None)
        if gw_entry is None or (gw_entry.get('points', 0) or 0) == 0:
            confirmed_absent.append(eid)
        else:
            real_failures.append(eid)

    if real_failures:
        print(f"  ABORT: picks missing AND history shows points for entries {real_failures}")
        return None
    if confirmed_absent:
        print(f"  {len(confirmed_absent)} manager(s) didn't play this GW (treated as 0): "
              f"{confirmed_absent}")

    mgr_pts = {
        eid: (calculate_manager_points(pd, live_elements, player_info) if pd else 0)
        for eid, pd in picks_by_entry.items()
    }
    return {team: sum(mgr_pts.get(e, 0) for e in ents) for team, ents in teams.items()}


def winner_of(p1, p2):
    if p1 > p2:
        return 'team1'
    if p2 > p1:
        return 'team2'
    return 'draw'


def diagnose_league(league, gw, player_info):
    print("\n" + "=" * 78)
    print(f"  {league.upper()}  (GW{gw})")
    print("=" * 78)

    teams = get_teams(league)

    matches = TeamLeagueMatches.query.filter_by(
        league_type=league, gameweek=gw
    ).order_by(TeamLeagueMatches.id).all()
    print(f"\n[1] Saved matches in team_league_matches (GW{gw}): {len(matches)} rows")
    if not matches:
        print("    (nothing saved)")
    for m in matches:
        s = winner_of(m.team1_points, m.team2_points)
        print(f"    {m.team1_name[:22]:22s} {m.team1_points:>3} - "
              f"{m.team2_points:<3} {m.team2_name[:22]:22s}  [{s}]")

    for label, ggw in (('base (prev)', gw - 1), ('current', gw)):
        rows = TeamLeagueStandings.query.filter_by(
            league_type=league, gameweek=ggw
        ).order_by(TeamLeagueStandings.league_points.desc()).all()
        print(f"\n[2{'.a' if label=='base (prev)' else '.b'}] Saved standings GW{ggw} "
              f"({label}): {len(rows)} rows")
        for r in rows:
            print(f"    {r.team_name[:22]:22s}  league_pts={r.league_points:>3}  "
                  f"fpl_total={r.total_fpl_points or 0:>5}")

    print(f"\n[3] Recomputing live team totals for GW{gw} from FPL picks...")
    all_entries = [eid for ents in teams.values() for eid in ents]
    histories = get_multiple_entry_history(all_entries)
    recomputed = recompute_team_totals(gw, teams, player_info, histories)
    if recomputed is None:
        print("    Could not recompute (live data missing or pick fetches failed).")
        return

    if matches:
        print(f"\n[4] Stored vs recomputed (per match):")
        any_diff = False
        for m in matches:
            n1 = recomputed.get(m.team1_name, 0)
            n2 = recomputed.get(m.team2_name, 0)
            d1 = n1 - m.team1_points
            d2 = n2 - m.team2_points
            s_res = winner_of(m.team1_points, m.team2_points)
            n_res = winner_of(n1, n2)
            flag = ''
            if d1 != 0 or d2 != 0:
                flag += ' POINTS_DIFF'
                any_diff = True
            if s_res != n_res:
                flag += ' WINNER_FLIP'
                any_diff = True
            print(f"    {m.team1_name[:20]:20s} stored={m.team1_points:>3} new={n1:>3} "
                  f"({d1:+d})   "
                  f"{m.team2_name[:20]:20s} stored={m.team2_points:>3} new={n2:>3} "
                  f"({d2:+d})   stored={s_res} new={n_res}{flag}")
        if not any_diff:
            print("    Clean: all stored points match recomputed values.")
    else:
        print(f"\n[4] No saved matches for GW{gw} — showing recomputed totals only:")
        for team in sorted(teams.keys()):
            print(f"    {team[:22]:22s}  recomputed_gw_pts={recomputed.get(team, 0)}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    gw = 36
    if '--gw' in sys.argv:
        i = sys.argv.index('--gw')
        if i + 1 < len(sys.argv):
            gw = int(sys.argv[i + 1])

    leagues = [a for a in args if a in LEAGUES] or list(LEAGUES)

    with app.app_context():
        print(f"Diagnosing GW{gw} for: {', '.join(leagues)}")
        print("Fetching bootstrap...")
        bootstrap = get_bootstrap_data()
        if not bootstrap:
            print("Failed to fetch bootstrap. Aborting.")
            return
        player_info = build_player_info(bootstrap)

        for league in leagues:
            diagnose_league(league, gw, player_info)

        print("\n" + "=" * 78)
        print("  Done. Read-only — no DB changes were made.")
        print("=" * 78)


if __name__ == '__main__':
    main()
