# -*- coding: utf-8 -*-
"""
Force-save GW36 results for cities / arab / libyan leagues.

Why this exists:
  The live dashboard path skipped saving GW36 (likely a transient pick-fetch
  failure during the post-GW window). That left team_league_matches and
  team_league_standings empty for GW36, and the dashboard fell back to
  recomputing live every 2 minutes — which uses a slightly different bonus
  formula and produces values that drift a few pts from the audit/FPL-final
  numbers (e.g. القطرون 219 vs 222).

What this does (per league):
  1. Reads GW35 standings from DB as the base.
  2. Computes each team's GW36 total using calculate_manager_points()
     (TC=2x, BB ignored, hits subtracted) over live data — same logic as
     audit_team_league.py.
  3. Fetches H2H matches for GW36 from the FPL API.
  4. Builds W/D/L points (3/1/0), adds them to the GW35 base.
  5. Writes team_league_matches (GW36) and team_league_standings (GW36).

Safety:
  - DRY-RUN by default: prints everything, writes nothing.
  - Aborts if any pick fetch is missing AND that manager's history shows
    they actually played the GW (real fetch failure, not just absence).
  - Aborts if GW35 base standings are not present in DB.
  - With --apply, prompts for typed confirmation before writing.

Usage:
    python force_save_gw36.py                 # dry-run all three leagues
    python force_save_gw36.py cities          # dry-run, one league
    python force_save_gw36.py --apply         # write all three
    python force_save_gw36.py cities --apply  # write only cities
    python force_save_gw36.py --gw 36         # explicit GW (default 36)
"""

import sys
import time
from app import app, db
from models import (
    TeamLeagueMatches, TeamLeagueStandings,
    save_team_league_standings, save_team_league_matches,
    get_team_league_standings_full,
)
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


def get_league_config(league):
    if league == 'cities':
        from core.cities_league import TEAMS_FPL_IDS, CITIES_H2H_LEAGUE_ID
        return TEAMS_FPL_IDS, CITIES_H2H_LEAGUE_ID
    if league == 'arab':
        from core.arab_league import TEAMS_FPL_IDS, ARAB_H2H_LEAGUE_ID
        return TEAMS_FPL_IDS, ARAB_H2H_LEAGUE_ID
    if league == 'libyan':
        from core.libyan_league import TEAMS_FPL_IDS, LIBYAN_H2H_LEAGUE_ID
        return TEAMS_FPL_IDS, LIBYAN_H2H_LEAGUE_ID
    raise SystemExit(f"Unknown league: {league}")


def compute_team_totals(gw, teams, player_info, histories):
    """Return ({team: int gw_points}, ok). ok=False means abort."""
    live = get_live_data(gw)
    if not live:
        return None, False
    live_elements = build_live_elements(live)

    all_entries = [eid for ents in teams.values() for eid in ents]
    urls = [f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/" for eid in all_entries]
    picks_map = fetch_multiple_parallel(urls)
    picks_by_entry = {
        eid: picks_map.get(f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/")
        for eid in all_entries
    }

    # Retry missing
    missing = [eid for eid, pd in picks_by_entry.items() if not pd]
    if missing:
        print(f"  retrying {len(missing)} missing picks serially...")
        for eid in missing:
            for _ in range(2):
                try:
                    retry = fetch_data(f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/")
                    if retry:
                        picks_by_entry[eid] = retry
                        break
                except Exception:
                    pass
                time.sleep(1.5)

    # Classify still-missing entries: real failure vs confirmed-absent
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
            real_failures.append((eid, gw_entry.get('points', 0)))

    if real_failures:
        print(f"  ABORT: picks missing AND history shows points for: {real_failures}")
        return None, False
    if confirmed_absent:
        print(f"  {len(confirmed_absent)} manager(s) confirmed absent (treated as 0): "
              f"{confirmed_absent}")

    mgr_pts = {
        eid: (calculate_manager_points(pd, live_elements, player_info) if pd else 0)
        for eid, pd in picks_by_entry.items()
    }
    totals = {team: sum(mgr_pts.get(e, 0) for e in ents) for team, ents in teams.items()}
    return totals, True


def fetch_h2h_matches(league_id, gw):
    url = f"{FPL_BASE_URL}/leagues-h2h-matches/league/{league_id}/?event={gw}"
    return fetch_data(url)


def build_matches_and_results(matches_raw, gw_team_points, entry_to_team):
    """Build list of unique team matchups and a {team: W/D/L} map."""
    matches = []
    seen = set()
    for m in matches_raw.get('results', []) if matches_raw else []:
        e1 = m.get('entry_1_entry')
        e2 = m.get('entry_2_entry')
        t1 = entry_to_team.get(e1)
        t2 = entry_to_team.get(e2)
        if not (t1 and t2):
            continue
        key = tuple(sorted((t1, t2)))
        if key in seen:
            continue
        seen.add(key)
        matches.append({
            'team1': t1,
            'team2': t2,
            'points1': gw_team_points.get(t1, 0),
            'points2': gw_team_points.get(t2, 0),
        })

    gw_league_points = {}
    for m in matches:
        p1, p2 = m['points1'], m['points2']
        if p1 > p2:
            gw_league_points[m['team1']] = 3
            gw_league_points[m['team2']] = 0
        elif p2 > p1:
            gw_league_points[m['team2']] = 3
            gw_league_points[m['team1']] = 0
        else:
            gw_league_points[m['team1']] = 1
            gw_league_points[m['team2']] = 1
    return matches, gw_league_points


def process_league(league, gw, player_info, apply_mode):
    print("\n" + "=" * 78)
    print(f"  {league.upper()}  (GW{gw})")
    print("=" * 78)

    teams, league_id = get_league_config(league)
    entry_to_team = {eid: tn for tn, ents in teams.items() for eid in ents}

    # 1) Base standings from GW(gw-1)
    base_data = get_team_league_standings_full(league, gw - 1)
    if not base_data:
        print(f"  ABORT: no GW{gw-1} standings in DB for {league}. Backfill GW{gw-1} first.")
        return None
    base_standings = {k: v['league_points'] for k, v in base_data.items()}
    base_fpl_totals = {k: v['total_fpl_points'] for k, v in base_data.items()}

    # 2) Recompute team totals
    print(f"  computing live team totals from {sum(len(v) for v in teams.values())} pick fetches...")
    histories = get_multiple_entry_history([eid for ents in teams.values() for eid in ents])
    gw_team_points, ok = compute_team_totals(gw, teams, player_info, histories)
    if not ok:
        return None

    # 3) Fetch H2H matches and build results
    matches_raw = fetch_h2h_matches(league_id, gw)
    matches, gw_league_points = build_matches_and_results(matches_raw, gw_team_points, entry_to_team)
    if not matches:
        print(f"  ABORT: no H2H matches found for {league} GW{gw}")
        return None

    # 4) Build new standings
    new_standings = {t: base_standings.get(t, 0) + gw_league_points.get(t, 0)
                     for t in teams.keys()}
    new_fpl_totals = {t: base_fpl_totals.get(t, 0) + gw_team_points.get(t, 0)
                      for t in teams.keys()}

    # 5) Existing rows preview
    existing_matches = TeamLeagueMatches.query.filter_by(
        league_type=league, gameweek=gw
    ).count()
    existing_standings = TeamLeagueStandings.query.filter_by(
        league_type=league, gameweek=gw
    ).count()

    print(f"\n  Matches to write: {len(matches)}  "
          f"(existing rows for GW{gw}: {existing_matches})")
    for m in matches:
        w = 'team1' if m['points1'] > m['points2'] else ('team2' if m['points2'] > m['points1'] else 'draw')
        print(f"    {m['team1'][:22]:22s} {m['points1']:>3} - "
              f"{m['points2']:<3} {m['team2'][:22]:22s}  [{w}]")

    print(f"\n  Standings to write (GW{gw}): {len(new_standings)} teams  "
          f"(existing rows for GW{gw}: {existing_standings})")
    sorted_new = sorted(new_standings.items(),
                        key=lambda x: (-x[1], -new_fpl_totals.get(x[0], 0)))
    for i, (team, pts) in enumerate(sorted_new, 1):
        gw_delta = gw_league_points.get(team, 0)
        gw_pts = gw_team_points.get(team, 0)
        print(f"    {i:>2}. {team[:22]:22s}  "
              f"league_pts={pts:>3} (base={base_standings.get(team,0)} +{gw_delta})  "
              f"fpl_total={new_fpl_totals[team]:>5} (gw={gw_pts})")

    return {
        'league': league,
        'gw': gw,
        'matches': matches,
        'standings': new_standings,
        'fpl_totals': new_fpl_totals,
        'existing_matches': existing_matches,
        'existing_standings': existing_standings,
    }


def write_league(plan):
    league = plan['league']
    gw = plan['gw']

    if plan['existing_matches']:
        deleted = TeamLeagueMatches.query.filter_by(
            league_type=league, gameweek=gw
        ).delete()
        print(f"  [{league}] deleted {deleted} existing match rows")
    if plan['existing_standings']:
        deleted = TeamLeagueStandings.query.filter_by(
            league_type=league, gameweek=gw
        ).delete()
        print(f"  [{league}] deleted {deleted} existing standings rows")
    db.session.commit()

    save_team_league_matches(league, gw, plan['matches'])
    print(f"  [{league}] saved {len(plan['matches'])} matches")

    save_team_league_standings(league, gw, plan['standings'], plan['fpl_totals'])
    print(f"  [{league}] saved {len(plan['standings'])} standings rows")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    apply_mode = '--apply' in sys.argv
    gw = 36
    if '--gw' in sys.argv:
        i = sys.argv.index('--gw')
        if i + 1 < len(sys.argv):
            gw = int(sys.argv[i + 1])

    leagues = [a for a in args if a in LEAGUES] or list(LEAGUES)
    mode = "APPLY" if apply_mode else "DRY-RUN"
    print(f"Force-save GW{gw} for {', '.join(leagues)}   [{mode}]")

    with app.app_context():
        print("\nFetching bootstrap...")
        bootstrap = get_bootstrap_data()
        if not bootstrap:
            print("Failed to fetch bootstrap. Aborting.")
            return
        player_info = build_player_info(bootstrap)

        plans = []
        for league in leagues:
            plan = process_league(league, gw, player_info, apply_mode)
            if plan:
                plans.append(plan)

        if not plans:
            print("\nNo league could be processed. Nothing to write.")
            return

        if not apply_mode:
            print("\n" + "=" * 78)
            print(f"  DRY-RUN complete. {len(plans)} league(s) ready: "
                  f"{', '.join(p['league'] for p in plans)}")
            print(f"  Rerun with --apply to write to DB.")
            print("=" * 78)
            return

        print("\n" + "=" * 78)
        print(f"  Ready to write {len(plans)} league(s): "
              f"{', '.join(p['league'] for p in plans)}")
        print("=" * 78)
        confirm = input(f"Type 'yes' to write GW{gw} for these leagues: ")
        if confirm.strip().lower() != 'yes':
            print("Aborted. No DB changes.")
            return

        for plan in plans:
            print(f"\n--- writing {plan['league']} GW{plan['gw']} ---")
            write_league(plan)

        print("\nAll done.")


if __name__ == '__main__':
    main()
