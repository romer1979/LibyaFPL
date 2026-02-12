# -*- coding: utf-8 -*-
"""
The 100 League - Three Phase Competition

Phase 1: Qualification (GW1-19)
- Top 99 + defending champion qualify (100 total)
- Standings frozen at end of GW19

Phase 2: Elimination (GW20-33)
- 100 qualified managers compete
- 6 managers eliminated each gameweek (bottom 6 by GW points)
- After 14 gameweeks: 100 - (14 x 6) = 16 remain

Phase 3: Championship (GW34-37)
- 16 managers in knockout bracket
"""

import requests
import os
from datetime import datetime
import time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from config import get_chip_arabic
from core.fpl_api import get_bootstrap_data, build_player_info

# Configuration
THE100_LEAGUE_ID = 8921
TIMEOUT = 15
LIVE_CALC_LIMIT = 150  # Max managers to calculate live for in large leagues
LARGE_LEAGUE_THRESHOLD = 200  # Above this, only top N get live

# Phase boundaries
QUALIFICATION_END_GW = 19
ELIMINATION_START_GW = 20
ELIMINATION_END_GW = 33
CHAMPIONSHIP_START_GW = 34
CHAMPIONSHIP_END_GW = 37

# Eliminations per gameweek
ELIMINATIONS_PER_GW = 6

# Last season winner - auto-qualifies regardless of position
WINNER_ENTRY_ID = 49250

# Cache
_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 120,  # 2 minutes
    'qualification_standings': None,  # Frozen GW19 standings
}

# Try to import database models (may not be available in all contexts)
try:
    from models import (
        db,
        The100QualifiedManager,
        The100EliminationResult,
        get_the100_qualified_managers,
        save_the100_qualified_managers,
        save_the100_elimination
    )
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False


def get_cookies():
    return {
        'sessionid': os.environ.get('FPL_SESSION_ID', ''),
        'csrftoken': os.environ.get('FPL_CSRF_TOKEN', '')
    }


def fetch_json(url, cookies=None):
    """Simple fetch with timeout"""
    try:
        r = requests.get(url, cookies=cookies, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"Fetch error: {e}")
        return None


def fetch_multiple_parallel(urls, cookies=None, max_workers=15):
    """Fetch multiple URLs in parallel"""
    results = {}
    if not urls:
        return results

    def fetch_one(url):
        try:
            r = requests.get(url, cookies=cookies, timeout=TIMEOUT)
            if r.status_code == 200:
                return url, r.json()
        except:
            pass
        return url, None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, url): url for url in urls}
        for future in as_completed(futures):
            url, data = future.result()
            if data:
                results[url] = data

    return results


def fetch_all_picks(entry_ids, gw, cookies):
    """Fetch picks for multiple managers in parallel (keyed by entry_id)"""
    results = {}
    if not entry_ids:
        return results

    def fetch_one(eid):
        url = f"https://fantasy.premierleague.com/api/entry/{eid}/event/{gw}/picks/"
        data = fetch_json(url, cookies)
        return eid, data

    try:
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(fetch_one, eid) for eid in entry_ids]
            for future in as_completed(futures):
                try:
                    eid, data = future.result()
                    if data:
                        results[eid] = data
                except Exception:
                    pass
    except Exception as e:
        print(f"Parallel picks fetch error: {e}")

    return results


def build_player_info(bootstrap):
    """Build player info dictionary"""
    return {
        player['id']: {
            'name': player['web_name'],
            'position': player['element_type'],
            'team': player['team']
        }
        for player in bootstrap.get('elements', [])
    }


def get_qualification_standings(league_id=THE100_LEAGUE_ID):
    """
    Fetch qualification phase standings from FPL API.
    """
    cookies = get_cookies()

    # Fetch all standings (paginated)
    standings = []
    page = 1
    while True:
        url = f"https://fantasy.premierleague.com/api/leagues-classic/{league_id}/standings/?page_standings={page}"
        data = fetch_json(url, cookies)
        if not data:
            break

        block = data.get("standings", {})
        rows = block.get("results", [])
        standings.extend(rows)

        if not block.get("has_next"):
            break
        page += 1

    return standings


def calculate_projected_bonus(live_data, fixtures):
    """Calculate projected bonus points (DGW-safe: uses per-fixture BPS from fixtures endpoint)"""
    # Build per-fixture BPS lookup from fixtures data
    fixture_bps = {}
    for fix in fixtures:
        fix_id = fix.get('id')
        if fix_id is None:
            continue
        fixture_bps[fix_id] = {}
        for stat_group in fix.get('stats', []):
            if stat_group.get('identifier') == 'bps':
                for entry in stat_group.get('h', []):
                    fixture_bps[fix_id][entry['element']] = entry['value']
                for entry in stat_group.get('a', []):
                    fixture_bps[fix_id][entry['element']] = entry['value']

    # Build player list for bonus calculation
    players = []
    for player_data in live_data['elements']:
        player_id = player_data['id']

        for fixture_info in player_data.get('explain', []):
            fixture_id = fixture_info['fixture']
            player_fix_bps = fixture_bps.get(fixture_id, {}).get(player_id, 0)
            player_fix_mins = any(
                s.get('value', 0) > 0
                for s in fixture_info.get('stats', [])
                if s.get('identifier') == 'minutes'
            )
            if player_fix_bps > 0 or player_fix_mins:
                players.append({
                    'player_id': player_id,
                    'fixture_id': fixture_id,
                    'bps': player_fix_bps,
                    'total_points': player_data['stats']['total_points'],
                    'bonus': 0
                })

    if not players:
        return {}

    def assign_bonus_points(group):
        """Assign bonus points based on BPS"""
        group = group.copy()
        group['bonus'] = 0
        group = group.sort_values(by='bps', ascending=False)
        unique_bps = group['bps'].unique()
        position = 1

        for bps_score in unique_bps:
            if position > 3:
                break
            bps_players = group[group['bps'] == bps_score]
            num = len(bps_players)

            if position == 1:
                group.loc[bps_players.index, 'bonus'] = 3
                position += 2 if num > 1 else 1
            elif position == 2:
                group.loc[bps_players.index, 'bonus'] = 2
                position = min(position + num, 4)
            elif position == 3:
                group.loc[bps_players.index, 'bonus'] = 1
                position += 1

        return group

    df = pd.DataFrame(players)
    df = df.groupby('fixture_id', group_keys=False).apply(assign_bonus_points)
    # Sum bonus across fixtures for DGW players
    return df.groupby('player_id')['bonus'].sum().to_dict()


def calculate_live_points(picks_data, live_elements, player_info, fixtures):
    """Calculate live points for a single manager (DGW-safe)"""
    if not picks_data:
        return 0

    picks = picks_data.get('picks', [])
    chip = picks_data.get('active_chip')
    hits = picks_data.get('entry_history', {}).get('event_transfers_cost', 0)

    # Helper: check if ALL of a team's fixtures are complete or postponed (DGW-safe)
    def are_all_team_fixtures_done(team_id):
        team_fixtures = [f for f in fixtures if f['team_h'] == team_id or f['team_a'] == team_id]
        if not team_fixtures:
            return True
        for f in team_fixtures:
            if not (f.get('started', False) or f.get('kickoff_time') is None):
                return False
        return True

    # Captain/VC info
    captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
    vice_captain_id = next((p['element'] for p in picks if p.get('is_vice_captain')), None)
    captain_minutes = live_elements.get(captain_id, {}).get('minutes', 0) if captain_id else 0
    captain_team = player_info.get(captain_id, {}).get('team') if captain_id else None
    captain_played = captain_minutes > 0
    captain_team_done = are_all_team_fixtures_done(captain_team) if captain_team else False

    # Determine which players count (bench boost = all 15, else starting 11)
    active_picks = picks[:15] if chip == 'bboost' else picks[:11]

    # Calculate points for active picks
    total_points = 0
    for pick in active_picks:
        pid = pick['element']
        pts = live_elements.get(pid, {}).get('total_points', 0)

        if pick.get('is_captain'):
            if captain_played:
                mult = 3 if chip == '3xc' else 2
            elif captain_team_done:
                mult = 0  # Captain DNP, all fixtures done -> VC takes over
            else:
                mult = 1  # Captain's team has unfinished fixtures
        elif pick.get('is_vice_captain'):
            if captain_team_done and not captain_played:
                vc_minutes = live_elements.get(pid, {}).get('minutes', 0)
                vc_team = player_info.get(pid, {}).get('team')
                vc_team_done = are_all_team_fixtures_done(vc_team) if vc_team else False

                if vc_minutes > 0:
                    mult = 3 if chip == '3xc' else 2
                elif vc_team_done:
                    mult = 0
                else:
                    mult = 1
            else:
                mult = 1
        else:
            mult = 1

        total_points += pts * mult

    # Auto-subs (only if not bench boost)
    if chip != 'bboost':
        total_points += calculate_auto_subs(picks, live_elements, player_info, fixtures, are_all_team_fixtures_done)

    return total_points - hits


def calculate_auto_subs(picks, live_elements, player_info, fixtures, team_done_fn):
    """
    FPL auto-subs (DGW-safe):
    - For each non-playing starter whose team is done, scan bench in order.
    - Bench player not played + team not done -> RESERVE (adds 0 now).
    - Bench player not played + team done -> DNP, skip.
    - Bench player played -> check GK<->GK and formation validity.
    """
    def pos_of(eid):
        return player_info.get(eid, {}).get('position', 0)

    def formation_ok(d, m, f, g):
        return g == 1 and 3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3

    starters = picks[:11]
    bench = picks[11:]

    d = sum(1 for p in starters if pos_of(p['element']) == 2)
    m = sum(1 for p in starters if pos_of(p['element']) == 3)
    f = sum(1 for p in starters if pos_of(p['element']) == 4)
    g = sum(1 for p in starters if pos_of(p['element']) == 1)

    non_playing = [
        p for p in starters
        if live_elements.get(p['element'], {}).get('minutes', 0) == 0
        and team_done_fn(player_info.get(p['element'], {}).get('team'))
    ]

    used_bench_ids = set()
    sub_points = 0

    for starter in non_playing:
        s_id = starter['element']
        s_pos = pos_of(s_id)

        for b in bench:
            b_id = b['element']
            if b_id in used_bench_ids:
                continue

            b_pos = pos_of(b_id)
            b_min = live_elements.get(b_id, {}).get('minutes', 0)
            b_played = b_min > 0
            b_done = team_done_fn(player_info.get(b_id, {}).get('team'))

            # GK <-> GK only
            if (s_pos == 1 and b_pos != 1) or (s_pos != 1 and b_pos == 1):
                continue

            # Not played + team not done -> reserve
            if not b_played and not b_done:
                used_bench_ids.add(b_id)
                break

            # Not played + team done -> DNP, skip
            if not b_played and b_done:
                continue

            # Played -> check formation
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
            used_bench_ids.add(b_id)
            d, m, f, g = d2, m2, f2, g2
            break

    return sub_points


def get_elimination_standings(current_gw, qualified_managers):
    """
    Calculate elimination phase standings with live GW points (DGW-safe).
    """
    cookies = get_cookies()

    # Fetch bootstrap data
    bootstrap = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/", cookies)
    if not bootstrap:
        return None

    player_info = build_player_info(bootstrap)

    # Get fixtures for current GW
    fixtures = fetch_json(f"https://fantasy.premierleague.com/api/fixtures/?event={current_gw}", cookies) or []

    # Check if any fixture has started
    gw_started = any(f.get('started', False) for f in fixtures)
    # Check if all fixtures finished
    all_fixtures_finished = all(f.get('finished', False) or f.get('finished_provisional', False) for f in fixtures) if fixtures else False

    # Use the shared 24-hour buffer check ONLY for auto-saving eliminations
    from core.fpl_api import is_gameweek_finished as check_gw_finished
    gw_finished_for_save = check_gw_finished(current_gw, fixtures)

    # For display: GW is finished when all matches are done
    gw_finished_display = all_fixtures_finished

    # Get live data
    live_data = fetch_json(f"https://fantasy.premierleague.com/api/event/{current_gw}/live/", cookies)
    if not live_data:
        return None

    # Build live elements dictionary with projected bonus (DGW-safe)
    live_elements = {}
    bonus_points = calculate_projected_bonus(live_data, fixtures) if gw_started else {}

    for elem in live_data['elements']:
        elem_id = elem['id']
        official_bonus = elem['stats'].get('bonus', 0)
        projected_bonus = bonus_points.get(elem_id, 0)

        # Use projected bonus if official isn't set yet
        actual_bonus = official_bonus if official_bonus > 0 else projected_bonus
        base_points = elem['stats']['total_points'] - official_bonus

        live_elements[elem_id] = {
            'total_points': base_points + actual_bonus,
            'minutes': elem['stats']['minutes'],
            'bonus': actual_bonus
        }

    # Helper: check if ALL team fixtures done (DGW-safe)
    def are_all_team_fixtures_done(team_id):
        team_fixtures = [fx for fx in fixtures if fx['team_h'] == team_id or fx['team_a'] == team_id]
        if not team_fixtures:
            return True
        for fx in team_fixtures:
            if not (fx.get('started', False) or fx.get('kickoff_time') is None):
                return False
        return True

    # Get all qualified entry IDs
    entry_ids = [m['entry_id'] for m in qualified_managers]

    # Fetch picks for all managers in parallel
    pick_urls = [f"https://fantasy.premierleague.com/api/entry/{eid}/event/{current_gw}/picks/" for eid in entry_ids]
    picks_data = fetch_multiple_parallel(pick_urls, cookies)

    # Calculate live points for each manager
    standings = []
    for manager in qualified_managers:
        entry_id = manager['entry_id']
        url = f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{current_gw}/picks/"

        gw_data = picks_data.get(url, {})
        picks = gw_data.get('picks', [])
        chip = gw_data.get('active_chip')

        if gw_started and gw_data.get('picks'):
            live_gw_points = calculate_live_points(
                gw_data, live_elements, player_info, fixtures
            )
        else:
            live_gw_points = gw_data.get('entry_history', {}).get('points', 0)

        # Get captain name
        captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
        captain_name = player_info.get(captain_id, {}).get('name', '-') if captain_id else '-'

        # Build player picks list with status
        player_picks = []
        if picks:
            # Determine auto-subs
            auto_subbed_in = set()
            auto_subbed_out = set()

            if gw_started and chip != 'bboost':
                # Calculate which players got auto-subbed
                starters = picks[:11]
                bench = picks[11:15]

                for starter in starters:
                    s_id = starter['element']
                    s_data = live_elements.get(s_id, {})
                    s_team = player_info.get(s_id, {}).get('team')
                    # DGW-safe: check if ANY fixture started for this team
                    s_team_played = any(
                        (fx.get('team_h') == s_team or fx.get('team_a') == s_team) and
                        (fx.get('started', False) or fx.get('finished', False))
                        for fx in fixtures
                    ) if s_team else True

                    # Starter didn't play but team has played
                    if s_data.get('minutes', 0) == 0 and s_team_played:
                        auto_subbed_out.add(s_id)

                        # Find the bench player who came in
                        for b in bench:
                            b_id = b['element']
                            if b_id in auto_subbed_in:
                                continue
                            b_data = live_elements.get(b_id, {})
                            if b_data.get('minutes', 0) > 0:
                                # Check position compatibility (simplified)
                                s_pos = player_info.get(s_id, {}).get('position', 0)
                                b_pos = player_info.get(b_id, {}).get('position', 0)
                                # GK can only be replaced by GK
                                if (s_pos == 1 and b_pos == 1) or (s_pos != 1 and b_pos != 1):
                                    auto_subbed_in.add(b_id)
                                    break

            for i, pick in enumerate(picks):
                p_id = pick['element']
                p_info = player_info.get(p_id, {})
                p_data = live_elements.get(p_id, {})

                minutes = p_data.get('minutes', 0)
                points = p_data.get('total_points', 0)

                # Determine player status (DGW-safe: use any/all instead of break)
                p_team = p_info.get('team')
                team_started = any(
                    (fx.get('team_h') == p_team or fx.get('team_a') == p_team) and fx.get('started', False)
                    for fx in fixtures
                ) if p_team else False
                team_finished = all(
                    fx.get('finished', False) or fx.get('finished_provisional', False)
                    for fx in fixtures
                    if fx.get('team_h') == p_team or fx.get('team_a') == p_team
                ) if p_team else False

                if minutes > 0:
                    if team_finished:
                        status = 'played'  # Game finished, player played
                    else:
                        status = 'playing'  # Currently on pitch
                elif team_started or team_finished:
                    status = 'benched'  # Team played but player didn't
                else:
                    status = 'pending'  # Team hasn't played yet

                # Is this a starter or bench?
                is_starter = i < 11
                is_auto_sub_in = p_id in auto_subbed_in
                is_auto_sub_out = p_id in auto_subbed_out

                # Calculate display points
                is_captain = pick.get('is_captain', False)
                is_vice = pick.get('is_vice_captain', False)

                # Captain team check (DGW-safe: all fixtures must be done)
                captain_team = player_info.get(captain_id, {}).get('team') if captain_id else None
                captain_team_done = are_all_team_fixtures_done(captain_team) if captain_team else False
                captain_minutes = live_elements.get(captain_id, {}).get('minutes', 0) if captain_id else 0

                if minutes > 0 or status == 'benched':
                    if is_captain and minutes > 0:
                        display_points = points * (3 if chip == '3xc' else 2)
                    elif is_vice and captain_team_done and captain_minutes == 0 and minutes > 0:
                        # Vice becomes captain ONLY if ALL captain's fixtures done AND captain didn't play
                        display_points = points * (3 if chip == '3xc' else 2)
                    else:
                        display_points = points
                else:
                    display_points = None  # Will show as "-"

                player_picks.append({
                    'id': p_id,
                    'name': p_info.get('name', 'Unknown'),
                    'position': p_info.get('position', 0),
                    'points': display_points,
                    'minutes': minutes,
                    'status': status,
                    'is_captain': is_captain,
                    'is_vice': is_vice,
                    'is_starter': is_starter,
                    'is_auto_sub_in': is_auto_sub_in,
                    'is_auto_sub_out': is_auto_sub_out,
                })

        standings.append({
            'entry_id': entry_id,
            'manager_name': manager['manager_name'],
            'team_name': manager['team_name'],
            'qualification_rank': manager['qualification_rank'],
            'qualification_total': manager.get('qualification_total', 0),
            'live_gw_points': live_gw_points,
            'captain': captain_name,
            'chip': chip,
            'is_winner': manager.get('is_winner', False),
            'players': player_picks,
        })

    # Sort by GW points (highest first)
    standings.sort(key=lambda x: (-x['live_gw_points'], x['qualification_rank']))

    # Assign live ranks
    for i, team in enumerate(standings, 1):
        team['live_rank'] = i

    return {
        'standings': standings,
        'gameweek': current_gw,
        'gw_started': gw_started,
        'gw_finished': gw_finished_display,  # For display
        'gw_finished_for_save': gw_finished_for_save,  # For auto-save logic
        'is_live': gw_started and not all_fixtures_finished,
    }


def get_the100_standings(league_id=THE100_LEAGUE_ID):
    """
    Main function to get The 100 standings based on current phase.

    - GW1-19: Qualification phase (live FPL standings)
    - GW20-33: Elimination phase (live GW points, bottom 6 eliminated each week)
    - GW34-37: Championship phase (knockout bracket)
    """
    global _cache

    now = time.time()

    # Return cached data if valid
    if _cache['data'] and (now - _cache['timestamp']) < _cache['ttl']:
        return _cache['data']

    try:
        cookies = get_cookies()

        # Get current gameweek
        bootstrap = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/", cookies)
        if not bootstrap:
            raise RuntimeError("Failed to fetch bootstrap data")

        events = bootstrap["events"]
        current_gw = next((e["id"] for e in events if e.get("is_current")), None)
        if not current_gw:
            finished = [e for e in events if e.get("finished")]
            current_gw = max(finished, key=lambda e: e["id"])["id"] if finished else 1

        # Determine current phase
        if current_gw <= QUALIFICATION_END_GW:
            phase = 'qualification'
        elif current_gw <= ELIMINATION_END_GW:
            phase = 'elimination'
        else:
            phase = 'championship'

        # ============================================
        # QUALIFICATION PHASE (GW1-19)
        # ============================================
        if phase == 'qualification':
            standings = get_qualification_standings(league_id)

            if not standings:
                raise RuntimeError("No standings found")

            # Check if GW is live
            fixtures = fetch_json(f"https://fantasy.premierleague.com/api/fixtures/?event={current_gw}", cookies) or []
            any_started = any(f.get('started', False) for f in fixtures)
            all_finished = all(f.get('finished') or f.get('finished_provisional') for f in fixtures) if fixtures else False
            is_live = any_started and not all_finished

            if is_live:
                # Fetch live data
                live_data = fetch_json(f"https://fantasy.premierleague.com/api/event/{current_gw}/live/", cookies)
                if not live_data:
                    raise RuntimeError("Failed to fetch live data")

                # Build player info
                player_info = {
                    p["id"]: {
                        "name": p["web_name"],
                        "team": p["team"],
                        "position": p["element_type"],
                    } for p in bootstrap["elements"]
                }

                # Build live elements with projected bonus (DGW-safe)
                bonus_points = calculate_projected_bonus(live_data, fixtures)
                live_elements = {}
                for elem in live_data['elements']:
                    elem_id = elem['id']
                    official_bonus = elem['stats'].get('bonus', 0)
                    projected_bonus = bonus_points.get(elem_id, 0)
                    actual_bonus = official_bonus if official_bonus > 0 else projected_bonus
                    base_points = elem['stats']['total_points'] - official_bonus

                    live_elements[elem_id] = {
                        'total_points': base_points + actual_bonus,
                        'minutes': elem['stats']['minutes'],
                        'bonus': actual_bonus
                    }

                # Determine which managers get live calculation
                total_managers = len(standings)
                if total_managers > LARGE_LEAGUE_THRESHOLD:
                    live_calc_entries = set(
                        row['entry'] for row in standings[:LIVE_CALC_LIMIT]
                    )
                else:
                    live_calc_entries = set(row['entry'] for row in standings)

                # Fetch picks for live-calculated managers in parallel
                all_picks = fetch_all_picks(list(live_calc_entries), current_gw, cookies)

                # Build final standings with live points
                final_rows = []
                for row in standings:
                    entry_id = row.get('entry')
                    is_winner = (entry_id == WINNER_ENTRY_ID)
                    api_total = row.get('total', 0)
                    api_gw = row.get('event_total', 0)
                    last_rank = row.get('last_rank') or row.get('rank', 0)

                    if entry_id in live_calc_entries and entry_id in all_picks:
                        # Calculate live points
                        picks_data = all_picks[entry_id]
                        live_gw_pts = calculate_live_points(
                            picks_data, live_elements, player_info, fixtures
                        )
                        base_total = api_total - api_gw  # Total before this GW
                        live_total = base_total + live_gw_pts

                        final_rows.append({
                            'manager_name': row.get('player_name', ''),
                            'team_name': row.get('entry_name', ''),
                            'live_total': live_total,
                            'live_gw_points': live_gw_pts,
                            'last_rank': last_rank,
                            'entry_id': entry_id,
                            'is_winner': is_winner,
                        })
                    else:
                        # Use API values as-is
                        final_rows.append({
                            'manager_name': row.get('player_name', ''),
                            'team_name': row.get('entry_name', ''),
                            'live_total': api_total,
                            'live_gw_points': api_gw,
                            'last_rank': last_rank,
                            'entry_id': entry_id,
                            'is_winner': is_winner,
                        })

                # Sort by live_total descending, then by GW points
                final_rows.sort(key=lambda x: (-x['live_total'], -x['live_gw_points']))

                # Assign live ranks and calculate rank changes
                for i, row in enumerate(final_rows, 1):
                    row['live_rank'] = i
                    row['rank_change'] = row['last_rank'] - i
                    del row['last_rank']

            else:
                # GW not live - use official API standings
                is_live = False
                final_rows = []
                for row in standings:
                    entry_id = row.get('entry')
                    current_rank = row.get('rank', 0)
                    last_rank = row.get('last_rank') or current_rank
                    rank_change = last_rank - current_rank
                    is_winner = (entry_id == WINNER_ENTRY_ID)

                    final_rows.append({
                        'live_rank': current_rank,
                        'manager_name': row.get('player_name', ''),
                        'team_name': row.get('entry_name', ''),
                        'live_total': row.get('total', 0),
                        'live_gw_points': row.get('event_total', 0),
                        'rank_change': rank_change,
                        'entry_id': entry_id,
                        'is_winner': is_winner
                    })

            # Find winner's rank
            winner_rank = None
            for row in final_rows:
                if row.get('entry_id') == WINNER_ENTRY_ID:
                    winner_rank = row.get('live_rank', 0)
                    break

            result = {
                'phase': 'qualification',
                'standings': final_rows,
                'gameweek': current_gw,
                'total_managers': len(final_rows),
                'is_live': is_live,
                'qualification_cutoff': 99,
                'winner_entry_id': WINNER_ENTRY_ID,
                'winner_rank': winner_rank,
                'last_updated': datetime.now().strftime('%H:%M'),
                'phase_info': {
                    'name': '\u0645\u0631\u062d\u0644\u0629 \u0627\u0644\u062a\u0623\u0647\u0644',
                    'name_en': 'Qualification Phase',
                    'gw_range': f'GW1-{QUALIFICATION_END_GW}',
                    'description': '\u0623\u0641\u0636\u0644 99 + \u0627\u0644\u0628\u0637\u0644 \u0627\u0644\u0633\u0627\u0628\u0642 \u064a\u062a\u0623\u0647\u0644\u0648\u0646',
                }
            }

        # ============================================
        # ELIMINATION PHASE (GW20-33)
        # ============================================
        elif phase == 'elimination':
            # Try to get qualified managers from database first
            qualified = []

            if DB_AVAILABLE:
                try:
                    # Get from database (includes tracking of who's been eliminated)
                    db_managers = The100QualifiedManager.query.filter(
                        The100QualifiedManager.eliminated_gw.is_(None)
                    ).order_by(The100QualifiedManager.qualification_rank).all()

                    if db_managers:
                        qualified = [{
                            'entry_id': m.entry_id,
                            'manager_name': m.manager_name,
                            'team_name': m.team_name,
                            'qualification_rank': m.qualification_rank,
                            'qualification_total': m.qualification_total,
                            'is_winner': m.is_winner
                        } for m in db_managers]
                except Exception as e:
                    print(f"Error fetching from database: {e}")

            # If database is empty or not available, fetch from FPL API
            if not qualified:
                qual_standings = get_qualification_standings(league_id)

                if not qual_standings:
                    raise RuntimeError("No qualification standings found")

                # Determine qualified managers (top 99 + winner)
                winner_in_top_99 = False

                # First pass: check if winner is in top 99
                for row in qual_standings:
                    if row.get('entry') == WINNER_ENTRY_ID and row.get('rank', 0) <= 99:
                        winner_in_top_99 = True
                        break

                # Second pass: build qualified list
                count_non_winner = 0
                for row in qual_standings:
                    entry_id = row.get('entry')
                    rank = row.get('rank', 0)
                    is_winner = (entry_id == WINNER_ENTRY_ID)

                    # If winner is in top 99, just take top 100
                    if winner_in_top_99:
                        if rank <= 100:
                            qualified.append({
                                'entry_id': entry_id,
                                'manager_name': row.get('player_name', ''),
                                'team_name': row.get('entry_name', ''),
                                'qualification_rank': rank,
                                'qualification_total': row.get('total', 0),
                                'is_winner': is_winner
                            })
                    else:
                        # Winner not in top 99: take top 99 + winner
                        if is_winner:
                            qualified.append({
                                'entry_id': entry_id,
                                'manager_name': row.get('player_name', ''),
                                'team_name': row.get('entry_name', ''),
                                'qualification_rank': rank,
                                'qualification_total': row.get('total', 0),
                                'is_winner': True
                            })
                        elif count_non_winner < 99:
                            qualified.append({
                                'entry_id': entry_id,
                                'manager_name': row.get('player_name', ''),
                                'team_name': row.get('entry_name', ''),
                                'qualification_rank': rank,
                                'qualification_total': row.get('total', 0),
                                'is_winner': False
                            })
                            count_non_winner += 1

                    if len(qualified) >= 100:
                        break

                # Save to database if available and this is the first time
                if DB_AVAILABLE and qualified:
                    try:
                        existing = The100QualifiedManager.query.first()
                        if not existing:
                            save_the100_qualified_managers(qualified)
                    except Exception as e:
                        print(f"Error saving to database: {e}")

            # Calculate how many GWs of elimination have passed
            elimination_gws_completed = current_gw - ELIMINATION_START_GW
            total_eliminated = elimination_gws_completed * ELIMINATIONS_PER_GW
            remaining_managers = max(16, 100 - total_eliminated)  # Minimum 16 for championship

            # Get elimination standings for current GW
            elim_data = get_elimination_standings(current_gw, qualified)

            if elim_data:
                standings = elim_data['standings']
                is_live = elim_data['is_live']
                gw_started = elim_data['gw_started']
                gw_finished = elim_data['gw_finished']
                gw_finished_for_save = elim_data.get('gw_finished_for_save', False)

                # AUTO-PROCESS ELIMINATIONS when 24 hours have passed
                if gw_finished_for_save and DB_AVAILABLE and standings:
                    try:
                        # Check if this GW's eliminations have already been processed
                        existing_elim = The100EliminationResult.query.filter_by(
                            gameweek=current_gw
                        ).first()

                        if not existing_elim:
                            # Get bottom 6 (to be eliminated)
                            # But only from managers who haven't been eliminated yet
                            active_standings = [s for s in standings if s.get('live_rank')]

                            if len(active_standings) > ELIMINATIONS_PER_GW:
                                eliminated = active_standings[-ELIMINATIONS_PER_GW:]

                                eliminated_list = [{
                                    'entry_id': m['entry_id'],
                                    'manager_name': m['manager_name'],
                                    'team_name': m['team_name'],
                                    'gw_points': m['live_gw_points'],
                                    'gw_rank': m['live_rank']
                                } for m in eliminated]

                                save_the100_elimination(current_gw, eliminated_list)
                                print(f"Auto-processed GW{current_gw} eliminations: {[m['manager_name'] for m in eliminated_list]}")
                    except Exception as e:
                        print(f"Error auto-processing eliminations: {e}")
            else:
                standings = qualified
                is_live = False
                gw_started = False
                gw_finished = False

            # Mark elimination zone (bottom 6)
            safe_count = remaining_managers - ELIMINATIONS_PER_GW
            elimination_zone_start = safe_count + 1

            for team in standings:
                rank = team.get('live_rank', 0)
                team['in_elimination_zone'] = rank >= elimination_zone_start
                team['is_safe'] = rank <= safe_count

            result = {
                'phase': 'elimination',
                'standings': standings,
                'gameweek': current_gw,
                'total_managers': len(standings),
                'remaining_managers': remaining_managers,
                'is_live': is_live,
                'gw_started': gw_started,
                'gw_finished': gw_finished,
                'safe_count': safe_count,
                'elimination_zone_start': elimination_zone_start,
                'eliminations_per_gw': ELIMINATIONS_PER_GW,
                'total_eliminated': total_eliminated,
                'gws_remaining': ELIMINATION_END_GW - current_gw,
                'winner_entry_id': WINNER_ENTRY_ID,
                'last_updated': datetime.now().strftime('%H:%M'),
                'phase_info': {
                    'name': '\u0645\u0631\u062d\u0644\u0629 \u0627\u0644\u0625\u0642\u0635\u0627\u0621',
                    'name_en': 'Elimination Phase',
                    'gw_range': f'GW{ELIMINATION_START_GW}-{ELIMINATION_END_GW}',
                    'description': f'{ELIMINATIONS_PER_GW} \u064a\u062e\u0631\u062c\u0648\u0646 \u0643\u0644 \u062c\u0648\u0644\u0629',
                }
            }

        # ============================================
        # CHAMPIONSHIP PHASE (GW34-37)
        # ============================================
        else:
            # Championship bracket - to be implemented
            result = {
                'phase': 'championship',
                'standings': [],
                'gameweek': current_gw,
                'total_managers': 16,
                'is_live': False,
                'winner_entry_id': WINNER_ENTRY_ID,
                'last_updated': datetime.now().strftime('%H:%M'),
                'phase_info': {
                    'name': '\u0645\u0631\u062d\u0644\u0629 \u0627\u0644\u0628\u0637\u0648\u0644\u0629',
                    'name_en': 'Championship Phase',
                    'gw_range': f'GW{CHAMPIONSHIP_START_GW}-{CHAMPIONSHIP_END_GW}',
                    'description': '16 \u0645\u062a\u0646\u0627\u0641\u0633 \u0641\u064a \u0646\u0638\u0627\u0645 \u062e\u0631\u0648\u062c \u0627\u0644\u0645\u063a\u0644\u0648\u0628',
                },
                'bracket': {
                    'round_of_16': [],
                    'quarter_finals': [],
                    'semi_finals': [],
                    'final': []
                }
            }

        # Cache the result
        _cache['data'] = result
        _cache['timestamp'] = now

        return result

    except Exception as e:
        print(f"Error fetching The 100 standings: {e}")
        import traceback
        traceback.print_exc()

        # Return cached data if available
        if _cache['data']:
            return _cache['data']

        return {
            'phase': 'unknown',
            'standings': [],
            'gameweek': None,
            'total_managers': 0,
            'is_live': False,
            'error': str(e)
        }


def get_the100_stats():
    """
    Get statistics for The 100 league
    """
    try:
        # Get current standings data
        standings_data = get_the100_standings()

        if not standings_data or standings_data.get('error'):
            return {
                'success': False,
                'error': standings_data.get('error', 'Failed to fetch standings')
            }

        standings = standings_data.get('standings', [])
        phase = standings_data.get('phase', 'unknown')
        gameweek = standings_data.get('gameweek')
        is_live = standings_data.get('is_live', False)

        if not standings:
            return {
                'success': False,
                'error': 'No standings data available'
            }

        # Get bootstrap data for player info
        bootstrap_data = get_bootstrap_data()
        player_info = build_player_info(bootstrap_data)

        # Initialize collectors
        gw_points = []
        manager_points = {}
        captains = []
        chips_used = []
        player_ownership = Counter()

        for team in standings:
            manager_name = team.get('manager_name', 'Unknown')
            points = team.get('live_gw_points', 0)

            gw_points.append(points)
            manager_points[manager_name] = points

            # Captain
            captain_name = team.get('captain', '-')
            if captain_name and captain_name != '-':
                captains.append({
                    'manager': manager_name,
                    'captain_name': captain_name
                })

            # Chips
            chip = team.get('chip')
            if chip:
                chips_used.append({
                    'manager': manager_name,
                    'chip': chip,
                    'chip_ar': get_chip_arabic(chip)
                })

            # Player ownership from players list
            players = team.get('players', [])
            for i, player in enumerate(players):
                if i >= 11 and not player.get('is_auto_sub_in'):
                    continue  # Skip bench players unless they auto-subbed in

                p_id = player.get('id')
                if not p_id:
                    continue

                if player.get('is_captain'):
                    if chip == '3xc':
                        player_ownership[p_id] += 3
                    else:
                        player_ownership[p_id] += 2
                else:
                    player_ownership[p_id] += 1

        # Calculate captain stats
        captain_counts = Counter([c['captain_name'] for c in captains])
        captain_stats = [
            {'name': name, 'count': count}
            for name, count in captain_counts.most_common()
        ]

        # Calculate points stats
        if gw_points:
            n = len(gw_points)
            min_points = min(gw_points)
            max_points = max(gw_points)

            min_managers = [name for name, pts in manager_points.items() if pts == min_points]
            max_managers = [name for name, pts in manager_points.items() if pts == max_points]

            points_stats = {
                'min': min_points,
                'min_managers': min_managers,
                'max': max_points,
                'max_managers': max_managers,
                'avg': round(sum(gw_points) / n, 1),
                'total_managers': n
            }
        else:
            points_stats = {
                'min': 0, 'min_managers': [],
                'max': 0, 'max_managers': [],
                'avg': 0, 'total_managers': 0
            }

        # Calculate effective ownership (top 15 players)
        effective_ownership = []
        total_managers = len(standings)

        for element_id, count in player_ownership.most_common(15):
            player = player_info.get(element_id, {})
            team_id = player.get('team', 0)
            team_name = ''
            for t in bootstrap_data.get('teams', []):
                if t['id'] == team_id:
                    team_name = t['short_name']
                    break

            percentage = round((count / total_managers) * 100, 1) if total_managers > 0 else 0

            effective_ownership.append({
                'name': player.get('name', 'Unknown'),
                'team': team_name,
                'count': count,
                'percentage': percentage
            })

        # Elimination phase specific stats
        elimination_stats = None
        if phase == 'elimination':
            remaining = standings_data.get('remaining_managers', 100)
            eliminated_count = 100 - remaining
            gws_remaining = standings_data.get('gws_remaining', 0)

            # Get managers in danger zone
            danger_zone = [
                team['manager_name']
                for team in standings
                if team.get('in_elimination_zone')
            ]

            elimination_stats = {
                'remaining': remaining,
                'eliminated': eliminated_count,
                'gws_remaining': gws_remaining,
                'danger_zone': danger_zone
            }

        return {
            'success': True,
            'gameweek': gameweek,
            'phase': phase,
            'is_live': is_live,
            'captain_stats': captain_stats,
            'chips_used': chips_used,
            'points_stats': points_stats,
            'effective_ownership': effective_ownership,
            'total_managers': total_managers,
            'elimination_stats': elimination_stats,
            'last_updated': standings_data.get('last_updated')
        }

    except Exception as e:
        print(f"Error getting The 100 stats: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'error': str(e)
        }
