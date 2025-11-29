# -*- coding: utf-8 -*-
"""
The 100 League - Live Standings for Classic League
Uses sync requests with ThreadPoolExecutor for concurrent fetching
"""

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from datetime import datetime

# Configuration
THE100_LEAGUE_ID = 8921
MAX_WORKERS = 10  # Number of concurrent threads
TIMEOUT = 20

# Get cookies from environment
def get_cookies():
    return {
        'sessionid': os.environ.get('FPL_SESSION_ID', ''),
        'csrftoken': os.environ.get('FPL_CSRF_TOKEN', '')
    }

# ------------ HTTP HELPERS --------------
def fetch_json(url, cookies=None, retries=3):
    """Fetch JSON with retry logic"""
    for attempt in range(retries):
        try:
            r = requests.get(url, cookies=cookies, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code in {429, 500, 502, 503, 504}:
                continue
            return None
        except Exception:
            if attempt < retries - 1:
                continue
            return None
    return None

# --------- CORE FETCHERS ---------
def get_bootstrap(cookies):
    """Get bootstrap data: current GW, player info"""
    data = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/", cookies)
    if not data:
        raise RuntimeError("bootstrap-static failed")
    
    events = data["events"]
    current = next((e for e in events if e.get("is_current")), None)
    if not current:
        finished = [e for e in events if e.get("finished")]
        current = max(finished, key=lambda e: e["id"]) if finished else events[0]
    
    player_info = {
        p["id"]: {
            "name": p["web_name"],
            "team": p["team"],
            "position": p["element_type"],
            "status": p["status"],
        } for p in data["elements"]
    }
    
    return current["id"], player_info, data

def get_all_standings(cookies, league_id):
    """Fetch all pages of classic league standings"""
    page, results = 1, []
    while True:
        url = f"https://fantasy.premierleague.com/api/leagues-classic/{league_id}/standings/?page_new_entries=1&page_standings={page}"
        data = fetch_json(url, cookies)
        if not data:
            break
        block = data.get("standings", {})
        rows = block.get("results", [])
        results.extend(rows)
        if not block.get("has_next"):
            break
        page += 1
    return results

def get_fixtures(cookies, gw):
    """Get fixtures for gameweek"""
    url = f"https://fantasy.premierleague.com/api/fixtures/?event={gw}"
    return fetch_json(url, cookies) or []

def get_live(cookies, gw):
    """Get live data for gameweek"""
    url = f"https://fantasy.premierleague.com/api/event/{gw}/live/"
    data = fetch_json(url, cookies)
    if not data:
        raise RuntimeError("live data failed")
    return data

def fetch_picks(entry_id, gw, cookies):
    """Fetch picks for a manager"""
    url = f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gw}/picks/"
    return fetch_json(url, cookies)

# --------------- RULE HELPERS ---------------
def team_fixtures_decided(team_id, fixtures, postponed_games=None):
    """Check if team's fixtures are decided"""
    postponed_games = postponed_games or {}
    if team_id in postponed_games:
        return True
    for f in fixtures:
        if f['team_h'] == team_id or f['team_a'] == team_id:
            if f.get('finished') or f.get('finished_provisional') or (f.get('kickoff_time') is None):
                return True
    return False

def build_effective_multipliers(picks, live_dict, player_info, fixtures, postponed_games, chip_played):
    """Calculate effective multipliers with captain/VC logic"""
    cap_id = next((p['element'] for p in picks if p.get('is_captain')), None)
    vc_id = next((p['element'] for p in picks if p.get('is_vice_captain')), None)

    cap_played = False
    if cap_id:
        cap_played = live_dict.get(cap_id, {'minutes': 0})['minutes'] > 0
    cap_team = player_info[cap_id]['team'] if cap_id else None
    cap_decided = team_fixtures_decided(cap_team, fixtures, postponed_games) if cap_team else False

    cap_mult = 0
    vc_mult = 0
    if cap_id:
        if cap_played:
            cap_mult = 3 if chip_played == '3xc' else 2
        else:
            if cap_decided and vc_id:
                vc_mult = 2

    eff = {}
    for i, p in enumerate(picks):
        pid = p['element']
        base = 1 if i < 11 else 0
        if chip_played == 'bboost' and i >= 11:
            base = 1
        mult = base
        if pid == cap_id:
            mult = cap_mult
        elif pid == vc_id:
            mult = max(mult, vc_mult)
        eff[pid] = mult
    return eff

def calculate_sub_points(picks, live_dict, player_info, fixtures, postponed_games=None):
    """Calculate auto-sub points"""
    postponed_games = postponed_games or {}
    
    def pos_of(eid):
        return player_info[eid]['position']

    def formation_ok(d, m, f, g):
        return (g == 1 and 3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3)

    def team_done_for_player(eid):
        team_id = player_info[eid]['team']
        return team_fixtures_decided(team_id, fixtures, postponed_games)

    starters = picks[:11]
    bench = picks[11:]

    d = sum(1 for p in starters if pos_of(p['element']) == 2)
    m = sum(1 for p in starters if pos_of(p['element']) == 3)
    f = sum(1 for p in starters if pos_of(p['element']) == 4)
    g = sum(1 for p in starters if pos_of(p['element']) == 1)

    non_playing_starters = [
        p for p in starters
        if live_dict.get(p['element'], {}).get('minutes', 0) == 0
        and team_done_for_player(p['element'])
    ]

    used_bench_ids = set()
    sub_points = 0

    for starter in non_playing_starters:
        s_id = starter['element']
        s_pos = pos_of(s_id)

        for b in bench:
            b_id = b['element']
            if b_id in used_bench_ids:
                continue

            b_pos = pos_of(b_id)
            b_min = live_dict.get(b_id, {}).get('minutes', 0)
            b_played = b_min > 0
            b_done = team_done_for_player(b_id)

            if (s_pos == 1 and b_pos != 1) or (s_pos != 1 and b_pos == 1):
                continue

            if not b_played:
                if not b_done:
                    used_bench_ids.add(b_id)
                    break
                else:
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

            sub_points += live_dict[b_id]['total_points']
            used_bench_ids.add(b_id)
            d, m, f, g = d2, m2, f2, g2
            break

    return sub_points

def calculate_live_points(picks, live_dict, player_info, chip_played, fixtures, postponed_games=None, event_transfers_cost=0):
    """Calculate live points for a manager"""
    postponed_games = postponed_games or {}
    eff_mult = build_effective_multipliers(picks, live_dict, player_info, fixtures, postponed_games, chip_played)

    total = 0
    for i, p in enumerate(picks):
        pid = p['element']
        pts = live_dict.get(pid, {'total_points': 0})['total_points']
        if i < 11 or chip_played == 'bboost':
            total += pts * eff_mult.get(pid, 0)

    if chip_played != 'bboost':
        total += calculate_sub_points(picks, live_dict, player_info, fixtures, postponed_games)

    return total - event_transfers_cost

# ------------------ CHIP NAMES ------------------
def get_chip_display(chip):
    """Get Arabic chip name"""
    chips = {
        'wildcard': '🃏',
        'freehit': '🎯',
        'bboost': '📈',
        '3xc': '👑',
        'manager': '🧠'
    }
    return chips.get(chip, '-')

# ------------------ MAIN PIPELINE ------------------
def get_the100_standings(league_id=THE100_LEAGUE_ID, postponed_games=None):
    """Main function to fetch live standings using ThreadPoolExecutor"""
    try:
        postponed_games = postponed_games or {}
        cookies = get_cookies()

        # 1) Bootstrap / current GW / players
        current_gw, player_info, bootstrap_data = get_bootstrap(cookies)

        # 2) Fixtures & live data
        fixtures = get_fixtures(cookies, current_gw)
        live_json = get_live(cookies, current_gw)

        # 3) Build live dict
        live_dict = {
            e['id']: {
                'total_points': e['stats']['total_points'],
                'minutes': e['stats']['minutes'],
                'bps': e['stats']['bps'],
                'bonus': e['stats'].get('bonus', 0)
            } for e in live_json['elements']
        }

        # 4) Pull ALL standings pages
        standings = get_all_standings(cookies, league_id)
        if not standings:
            raise RuntimeError("No standings found")

        # 5) Fetch all picks concurrently using ThreadPoolExecutor
        picks_dict = {}
        
        def fetch_manager_picks(entry_id):
            return entry_id, fetch_picks(entry_id, current_gw, cookies)
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(fetch_manager_picks, r['entry']): r 
                for r in standings
            }
            for future in as_completed(futures):
                try:
                    entry_id, picks_data = future.result()
                    if picks_data:
                        picks_dict[entry_id] = picks_data
                except Exception:
                    pass

        # 6) Compute live points for each manager
        final_rows = []
        for row in standings:
            entry_id = row.get('entry')
            picks_data = picks_dict.get(entry_id)
            
            if not picks_data:
                continue
            
            picks = picks_data['picks']
            chip = picks_data.get('active_chip')
            hits = picks_data.get('entry_history', {}).get('event_transfers_cost', 0)
            old_gw_points = picks_data.get('entry_history', {}).get('points', 0)

            # Get captain name
            cap_id = next((p['element'] for p in picks if p.get('is_captain')), None)
            captain_name = player_info[cap_id]['name'] if cap_id else '-'

            # Calculate live points
            live_gw = calculate_live_points(
                picks, live_dict, player_info, chip, fixtures, postponed_games, hits
            )
            live_total = (row['total'] - old_gw_points) + live_gw

            final_rows.append({
                'entry_id': entry_id,
                'manager_name': row.get('player_name'),
                'team_name': row.get('entry_name'),
                'live_gw_points': live_gw,
                'live_total': live_total,
                'previous_rank': row.get('last_rank', row.get('rank')),
                'captain': captain_name,
                'chip': get_chip_display(chip),
                'chip_raw': chip
            })

        # 7) Sort by live total
        final_rows.sort(key=lambda x: (-x['live_total'], -x['live_gw_points']))

        # 8) Assign live ranks and calculate rank changes
        for i, row in enumerate(final_rows, 1):
            row['live_rank'] = i
            prev = row['previous_rank'] or i
            row['rank_change'] = prev - i

        # Check if GW is live
        is_live = any(
            f.get('started') and not f.get('finished_provisional')
            for f in fixtures
        )

        return {
            'standings': final_rows,
            'gameweek': current_gw,
            'total_managers': len(final_rows),
            'is_live': is_live,
            'qualification_cutoff': 100,
            'last_updated': datetime.now().strftime('%H:%M')
        }
        
    except Exception as e:
        print(f"Error fetching The 100 standings: {e}")
        return {
            'standings': [],
            'gameweek': None,
            'total_managers': 0,
            'is_live': False,
            'qualification_cutoff': 100,
            'error': str(e)
        }
