# -*- coding: utf-8 -*-
"""
Automatic gameweek backfill for team-based leagues.

When a GW is missed (nobody visited the page during the save window),
this module detects the gap and reconstructs the missing GW(s) from
the FPL API historical data, then saves them to the database.
"""

import requests
import time
from models import (
    get_team_league_standings_full,
    save_team_league_standings,
    save_team_league_matches,
)

TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY = 2

# Concurrency guard: prevents duplicate backfill from concurrent requests
_backfill_in_progress = {}


def detect_missing_gameweeks(league_type, current_gw, standings_by_gw):
    """
    Detect gameweeks between the last known standings and current_gw - 1
    that are missing from both hardcoded standings and the database.

    Returns sorted list of missing GW numbers, e.g., [23, 24].
    Returns [] if no gaps detected.
    """
    if current_gw <= 1:
        return []

    max_hardcoded_gw = max(standings_by_gw.keys()) if standings_by_gw else 0

    missing = []
    for gw in range(current_gw - 1, max_hardcoded_gw, -1):
        if gw in standings_by_gw:
            break

        db_data = get_team_league_standings_full(league_type, gw)
        if db_data:
            break

        missing.append(gw)

    return sorted(missing)


def backfill_missing_gameweeks(league_type, missing_gws, teams_fpl_ids, h2h_league_id, standings_by_gw):
    """
    Reconstruct and save standings for missing GWs.

    For each missing GW:
    1. Find base standings (GW before it)
    2. Fetch live data + picks + H2H matches from FPL API
    3. Calculate team points, determine W/D/L
    4. Save cumulative standings + matches to database
    """
    if not missing_gws:
        return

    if _backfill_in_progress.get(league_type):
        print(f"[{league_type}] Backfill already in progress, skipping")
        return

    _backfill_in_progress[league_type] = True
    try:
        _do_backfill(league_type, missing_gws, teams_fpl_ids, h2h_league_id, standings_by_gw)
    finally:
        _backfill_in_progress[league_type] = False


def _do_backfill(league_type, missing_gws, teams_fpl_ids, h2h_league_id, standings_by_gw):
    """Internal backfill logic."""
    # Build reverse lookup
    entry_to_team = {}
    for team_name, ids in teams_fpl_ids.items():
        for entry_id in ids:
            entry_to_team[entry_id] = team_name

    # Fetch bootstrap data once (reused across GWs)
    bootstrap = _fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/")
    if not bootstrap:
        print(f"[{league_type}] Backfill failed: cannot fetch bootstrap data")
        return

    player_info = {
        p['id']: {
            'name': p['web_name'],
            'team': p['team'],
            'position': p['element_type'],
        }
        for p in bootstrap.get('elements', [])
    }

    # Find base standings for the first missing GW
    base_gw = missing_gws[0] - 1
    current_standings, current_fpl_totals = _get_base_for_backfill(
        league_type, base_gw, standings_by_gw, teams_fpl_ids
    )

    for gw in missing_gws:
        print(f"[{league_type}] Backfilling GW{gw}...")

        # 1. Fetch live data
        live_data = _fetch_json(
            f"https://fantasy.premierleague.com/api/event/{gw}/live/"
        )
        if not live_data:
            print(f"[{league_type}] Backfill GW{gw} failed: no live data")
            return

        live_elements = {
            elem['id']: {
                'total_points': elem['stats']['total_points'],
                'minutes': elem['stats']['minutes'],
            }
            for elem in live_data.get('elements', [])
        }

        # 2. Calculate team FPL points
        gw_team_points = {}
        for team_name, entry_ids in teams_fpl_ids.items():
            total = 0
            for entry_id in entry_ids:
                picks_data = _fetch_json(
                    f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gw}/picks/"
                )
                if picks_data:
                    total += _calculate_manager_points(picks_data, live_elements, player_info)
                time.sleep(0.1)
            gw_team_points[team_name] = total

        # 3. Get H2H matches and determine W/D/L
        matches_data = _fetch_json(
            f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{h2h_league_id}/?event={gw}"
        )

        matches = []
        gw_league_points = {team: 0 for team in teams_fpl_ids.keys()}

        if matches_data and 'results' in matches_data:
            for match in matches_data['results']:
                entry_1 = match.get('entry_1_entry')
                entry_2 = match.get('entry_2_entry')
                team_1 = entry_to_team.get(entry_1)
                team_2 = entry_to_team.get(entry_2)

                if team_1 and team_2:
                    already = any(
                        (m['team1'] == team_1 and m['team2'] == team_2) or
                        (m['team1'] == team_2 and m['team2'] == team_1)
                        for m in matches
                    )
                    if not already:
                        p1 = gw_team_points.get(team_1, 0)
                        p2 = gw_team_points.get(team_2, 0)
                        matches.append({
                            'team1': team_1, 'team2': team_2,
                            'points1': p1, 'points2': p2,
                        })
                        if p1 > p2:
                            gw_league_points[team_1] = 3
                        elif p2 > p1:
                            gw_league_points[team_2] = 3
                        else:
                            gw_league_points[team_1] = 1
                            gw_league_points[team_2] = 1

        # 4. Compute cumulative standings
        new_standings = {}
        new_fpl_totals = {}
        for team in teams_fpl_ids.keys():
            new_standings[team] = current_standings.get(team, 0) + gw_league_points.get(team, 0)
            new_fpl_totals[team] = current_fpl_totals.get(team, 0) + gw_team_points.get(team, 0)

        # 5. Save to database
        save_team_league_standings(league_type, gw, new_standings, new_fpl_totals)
        save_team_league_matches(league_type, gw, matches)

        print(f"[{league_type}] Backfilled GW{gw}: {len(matches)} matches saved")

        # Chain for next missing GW
        current_standings = new_standings
        current_fpl_totals = new_fpl_totals


def _get_base_for_backfill(league_type, base_gw, standings_by_gw, teams_fpl_ids):
    """Find base standings for backfill starting point."""
    # Try hardcoded first
    if base_gw in standings_by_gw:
        return standings_by_gw[base_gw].copy(), {team: 0 for team in teams_fpl_ids}

    # Try database
    db_data = get_team_league_standings_full(league_type, base_gw)
    if db_data:
        standings = {k: v['league_points'] for k, v in db_data.items()}
        fpl_totals = {k: v['total_fpl_points'] for k, v in db_data.items()}
        return standings, fpl_totals

    # Walk further back
    for gw in range(base_gw - 1, 0, -1):
        if gw in standings_by_gw:
            return standings_by_gw[gw].copy(), {team: 0 for team in teams_fpl_ids}
        db_data = get_team_league_standings_full(league_type, gw)
        if db_data:
            standings = {k: v['league_points'] for k, v in db_data.items()}
            fpl_totals = {k: v['total_fpl_points'] for k, v in db_data.items()}
            return standings, fpl_totals

    # Ultimate fallback
    if standings_by_gw:
        earliest = min(standings_by_gw.keys())
        return standings_by_gw[earliest].copy(), {team: 0 for team in teams_fpl_ids}
    return {team: 0 for team in teams_fpl_ids}, {team: 0 for team in teams_fpl_ids}


def _fetch_json(url, retries=MAX_RETRIES):
    """Fetch JSON with retries."""
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                time.sleep(RETRY_DELAY * 2)
            else:
                print(f"  Backfill HTTP {r.status_code} for {url}")
        except Exception as e:
            print(f"  Backfill fetch error: {e}")

        if attempt < retries - 1:
            time.sleep(RETRY_DELAY)

    return None


def _calculate_auto_subs(picks, live_elements, player_info):
    """Calculate auto-sub points for a finished GW."""
    def pos_of(eid):
        return player_info.get(eid, {}).get('position', 0)

    def formation_ok(d, m, f, g):
        return (g == 1 and 3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3)

    starters = picks[:11]
    bench = picks[11:]

    d = sum(1 for p in starters if pos_of(p['element']) == 2)
    m = sum(1 for p in starters if pos_of(p['element']) == 3)
    f = sum(1 for p in starters if pos_of(p['element']) == 4)
    g = sum(1 for p in starters if pos_of(p['element']) == 1)

    non_playing = [p for p in starters if live_elements.get(p['element'], {}).get('minutes', 0) == 0]

    used = set()
    sub_points = 0

    for starter in non_playing:
        s_id = starter['element']
        s_pos = pos_of(s_id)

        for b in bench:
            b_id = b['element']
            if b_id in used:
                continue

            b_pos = pos_of(b_id)
            b_min = live_elements.get(b_id, {}).get('minutes', 0)

            if (s_pos == 1 and b_pos != 1) or (s_pos != 1 and b_pos == 1):
                continue
            if b_min == 0:
                continue

            d2, m2, f2, g2 = d, m, f, g
            if s_pos == 2: d2 -= 1
            elif s_pos == 3: m2 -= 1
            elif s_pos == 4: f2 -= 1
            elif s_pos == 1: g2 -= 1

            if b_pos == 2: d2 += 1
            elif b_pos == 3: m2 += 1
            elif b_pos == 4: f2 += 1
            elif b_pos == 1: g2 += 1

            if not formation_ok(d2, m2, f2, g2):
                continue

            sub_points += live_elements.get(b_id, {}).get('total_points', 0)
            used.add(b_id)
            d, m, f, g = d2, m2, f2, g2
            break

    return sub_points


def _calculate_manager_points(picks_data, live_elements, player_info):
    """Calculate manager points using custom league rules (captain 2x, no bench boost, hits subtracted)."""
    if not picks_data:
        return 0

    picks = picks_data.get('picks', [])
    hits = picks_data.get('entry_history', {}).get('event_transfers_cost', 0)

    if not picks:
        return 0

    captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
    captain_minutes = live_elements.get(captain_id, {}).get('minutes', 0) if captain_id else 0
    captain_played = captain_minutes > 0

    total = 0
    for pick in picks[:11]:
        pid = pick['element']
        pts = live_elements.get(pid, {}).get('total_points', 0)

        if pick.get('is_captain'):
            pts = pts * 2 if captain_played else 0
        elif pick.get('is_vice_captain') and not captain_played:
            vc_min = live_elements.get(pid, {}).get('minutes', 0)
            if vc_min > 0:
                pts *= 2

        total += pts

    total += _calculate_auto_subs(picks, live_elements, player_info)
    return total - hits
