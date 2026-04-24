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
from core.fpl_api import (
    FPL_BASE_URL,
    fetch_data,
    fetch_multiple_parallel,
    get_multiple_entry_history,
)

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


def compute_team_totals_for_gw(gw, teams, player_info, histories, return_raw=False):
    """Return {team_name: int} of recomputed points for every team.

    If picks are missing after retries, cross-check the manager's history:
      - If history shows points > 0 for that GW -> real fetch failure, abort.
      - If history shows 0 or no entry -> manager didn't play, treat as 0.

    histories: dict {entry_id: entry_history_dict} fetched once by caller.
    """
    import time as _time
    live = get_live_data(gw)
    if not live:
        return (None, None, None) if return_raw else None
    live_elements = build_live_elements(live)

    all_entries = [eid for entries in teams.values() for eid in entries]
    urls = [f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/" for eid in all_entries]
    picks = fetch_multiple_parallel(urls)
    picks_by_entry = {
        eid: picks.get(f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/")
        for eid in all_entries
    }

    missing = [eid for eid, pd in picks_by_entry.items() if not pd]
    if missing:
        print(f"  retrying {len(missing)} missing pick(s) serially...")
        for eid in missing:
            for attempt in range(2):
                try:
                    retry = fetch_data(f"{FPL_BASE_URL}/entry/{eid}/event/{gw}/picks/")
                    if retry:
                        picks_by_entry[eid] = retry
                        break
                except Exception:
                    pass
                _time.sleep(1.5)

        # For entries still missing, check their history to see if they actually
        # played this GW. If history has positive points for this GW -> real API
        # failure (abort). If history is 0 or absent -> manager didn't play
        # (legit 0 contribution).
        still_missing = [eid for eid, pd in picks_by_entry.items() if not pd]
        real_failures = []
        for eid in still_missing:
            h = histories.get(eid) or {}
            gw_entry = next(
                (g for g in h.get('current', []) if g.get('event') == gw),
                None,
            )
            gross = (gw_entry or {}).get('points', 0) or 0
            if gross > 0:
                real_failures.append((eid, gross))
        if real_failures:
            print(f"  ABORT GW{gw}: {len(real_failures)} manager(s) have scores in "
                  f"history but picks unavailable: {real_failures}. Skipping this GW.")
            return (None, None, None) if return_raw else None
        if still_missing:
            print(f"  {len(still_missing)} manager(s) have no picks and no history "
                  f"for this GW (didn't play) -> treated as 0.")

    mgr_points = {
        eid: (calculate_manager_points(pd, live_elements, player_info) if pd else 0)
        for eid, pd in picks_by_entry.items()
    }
    totals = {
        team: sum(mgr_points.get(e, 0) for e in entries)
        for team, entries in teams.items()
    }
    if return_raw:
        return totals, live_elements, picks_by_entry
    return totals


POSITIONS = {1: 'GK', 2: 'DEF', 3: 'MID', 4: 'FWD'}


def print_team_debug(gw, team_name, entries, live_elements, picks_by_entry, bootstrap_elements):
    """Print full per-player breakdown for each of the 3 managers in `team_name` for `gw`."""
    players_by_id = {p['id']: p for p in bootstrap_elements}
    print(f"\n===== DEBUG {team_name} at GW{gw} =====")
    team_total = 0
    for eid in entries:
        pd = picks_by_entry.get(eid)
        if not pd:
            print(f"  entry={eid}: NO picks data")
            continue
        chip = pd.get('active_chip') or '-'
        hit = pd.get('entry_history', {}).get('event_transfers_cost', 0) or 0
        cap_id = next((p['element'] for p in pd.get('picks', []) if p.get('is_captain')), None)
        cap_min = live_elements.get(cap_id, {}).get('minutes', 0) if cap_id else 0
        cap_played = cap_min > 0

        print(f"\n  entry={eid}  chip={chip}  hit={hit}  "
              f"captain_id={cap_id} played={cap_played}")
        print(f"  {'slot':>4}  {'pos':>3}  {'player':22s}  "
              f"{'raw':>4}  {'min':>4}  {'fpl_mult':>8}  "
              f"{'custom':>6}  flag")

        mgr_total = 0
        sum_starters = 0
        for pick in pd.get('picks', []):
            pid = pick['element']
            info = players_by_id.get(pid, {})
            pos_id = info.get('element_type')
            pos = POSITIONS.get(pos_id, '?')
            raw = live_elements.get(pid, {}).get('total_points', 0)
            mins = live_elements.get(pid, {}).get('minutes', 0)
            slot = pick.get('position', 99)
            fpl_mult = pick.get('multiplier', 0)
            is_starter = slot <= 11

            if not is_starter:
                custom = 0
                flag = "bench (ignored, no BB)"
            elif pick.get('is_captain'):
                custom = raw * 2 if cap_played else 0
                flag = "C (2x)" if cap_played else "C DNP"
            elif pick.get('is_vice_captain') and not cap_played:
                custom = raw * 2 if mins > 0 else 0
                flag = "VC takes over" if mins > 0 else "VC DNP"
            else:
                custom = raw
                flag = ""
                if pick.get('is_vice_captain'):
                    flag = "VC (cap played)"

            print(f"  {slot:>4}  {pos:>3}  {info.get('web_name', '?'):22s}  "
                  f"{raw:>4}  {mins:>4}  {fpl_mult:>8}  "
                  f"{custom:>6}  {flag}")
            if is_starter:
                sum_starters += custom

        # Compute full function result for verification
        full = calculate_manager_points(pd, live_elements, {
            pid: {'position': POSITIONS.get(p.get('element_type')), 'name': p.get('web_name')}
            for pid, p in players_by_id.items()
        })
        print(f"  Starters sum (2x cap, no BB, no subs): {sum_starters}")
        print(f"  calculate_manager_points() returns:     {full}")
        print(f"  (diff from starters sum = auto_subs − hits = {full - sum_starters})")
        mgr_total = full
        team_total += mgr_total

    print(f"\n  Team total (sum of 3 managers' calc): {team_total}")


def parse_arg_value(flag):
    """Return the value after `--flag` on argv, or None."""
    try:
        i = sys.argv.index(flag)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    except ValueError:
        return None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    apply_mode = '--apply' in sys.argv
    debug_team = parse_arg_value('--debug')
    gw_filter = parse_arg_value('--gw')
    gw_filter = int(gw_filter) if gw_filter and gw_filter.isdigit() else None

    # When --debug is given as a positional (no team), swap from args list
    if debug_team and debug_team in ('libyan', 'arab', 'cities'):
        # Mis-parse protection: --debug should take a team name
        debug_team = None

    if not args or args[0] not in ('libyan', 'arab', 'cities'):
        print("Usage: python audit_team_league.py {libyan|arab|cities} "
              "[--gw <N>] [--apply] [--debug <team_name>]")
        return
    league_type = args[0]
    teams = get_teams(league_type)

    mode_bits = []
    if gw_filter: mode_bits.append(f"GW{gw_filter} only")
    if debug_team: mode_bits.append(f"debug={debug_team}")
    if apply_mode: mode_bits.append("APPLY")
    mode_label = " | ".join(mode_bits) if mode_bits else "dry run"
    print(f"Auditing {league_type} league: {len(teams)} teams, "
          f"{sum(len(v) for v in teams.values())} managers  [{mode_label}]")

    with app.app_context():
        saved_gws = sorted({
            r[0] for r in db.session.query(TeamLeagueMatches.gameweek)
            .filter_by(league_type=league_type).distinct().all()
        })
        if gw_filter:
            saved_gws = [g for g in saved_gws if g == gw_filter]
        if not saved_gws:
            print(f"No saved matches for {league_type}"
                  f"{' at GW' + str(gw_filter) if gw_filter else ''}")
            return
        print(f"GWs to audit: {saved_gws}")

        print("\nFetching bootstrap...")
        bootstrap = get_bootstrap_data()
        if not bootstrap:
            print("Failed to fetch bootstrap")
            return
        player_info = build_player_info(bootstrap)
        bootstrap_elements = bootstrap.get('elements', [])

        all_entries = [eid for entries in teams.values() for eid in entries]
        print(f"Fetching histories for {len(all_entries)} managers (for fallback checks)...")
        histories = get_multiple_entry_history(all_entries)

        points_diffs = []    # (gw, team, stored, recomputed, delta)
        winner_diffs = []    # (gw, team1, team2, s_p1, s_p2, n_p1, n_p2, s_res, n_res)
        match_updates = []   # (match_row, new_t1, new_t2) — rows to rewrite under --apply
        debug_printed = set()  # (gw, team) already dumped so we don't print twice

        for gw in saved_gws:
            print(f"\nGW{gw}... fetching {sum(len(v) for v in teams.values())} picks")
            recomputed, live_elements, picks_by_entry = compute_team_totals_for_gw(
                gw, teams, player_info, histories, return_raw=True
            )
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
                if n1 != m.team1_points or n2 != m.team2_points:
                    match_updates.append((m, n1, n2))

                # --debug: dump per-player breakdown for flagged team(s)
                if debug_team:
                    for team_name in (m.team1_name, m.team2_name):
                        if team_name != debug_team:
                            continue
                        if (gw, team_name) in debug_printed:
                            continue
                        stored_val = m.team1_points if team_name == m.team1_name else m.team2_points
                        new_val = n1 if team_name == m.team1_name else n2
                        if stored_val == new_val:
                            continue
                        print_team_debug(gw, team_name, teams[team_name],
                                         live_elements, picks_by_entry,
                                         bootstrap_elements)
                        debug_printed.add((gw, team_name))

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
            return

        if debug_team:
            if not debug_printed:
                print(f"\n  No flagged rows involving '{debug_team}' in audited range.")
            print("\n  --debug mode: no writes. Compare the breakdown above against FPL app.")
            return

        if winner_diffs:
            print("\n  At least one match has a different winner under recomputed points.")
            print("  --apply will NOT write in that case (would change standings). Aborting.")
            return

        if not apply_mode:
            print(f"\n  Dry run. Rerun with --apply to rewrite {len(match_updates)} match rows")
            print("  (winners are unchanged; only team1_points/team2_points get corrected).")
            return

        confirm = input(f"\nType 'yes' to rewrite {len(match_updates)} team_league_matches rows: ")
        if confirm.strip().lower() != 'yes':
            print("Aborted.")
            return

        for m, n1, n2 in match_updates:
            m.team1_points = n1
            m.team2_points = n2
        db.session.commit()
        print(f"Committed {len(match_updates)} match-row updates.")


if __name__ == '__main__':
    main()
