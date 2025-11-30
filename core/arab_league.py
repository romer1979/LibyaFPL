# -*- coding: utf-8 -*-
"""
البطولة العربية - Arab Championship
Team-based H2H league where each team has 3 managers
Special rules:
- Triple Captain counts as 2x (not 3x)
- Bench Boost is ignored (only 11 players count)
"""

import requests
import os
from datetime import datetime
import time
from collections import Counter

# Configuration
ARAB_H2H_LEAGUE_ID = 1015271
TIMEOUT = 15

# Previous GW standings (GW12)
PREVIOUS_STANDINGS = {
    "العربي القطري": 28,
    "العين": 27,
    "القوة الجوية": 24,
    "الفتح السعودي": 24,
    "نيوم": 24,
    "اتحاد العاصمة": 22,
    "المريخ": 19,
    "النصر السعودي": 18,
    "النجم الساحلي": 18,
    "الترجي": 18,
    "الجزيرة الإماراتي": 16,
    "الأهلي المصري": 15,
    "الأفريقي": 15,
    "الاتحاد السعودي": 15,
    "الوداد": 15,
    "الرجاء": 15,
    "شبيبة القبائل": 12,
    "الهلال السعودي": 12,
    "أربيل": 9,
    "الهلال السوداني": 9,
}

# Team definitions: team_name -> list of FPL entry IDs
TEAMS_FPL_IDS = {
    "الهلال السعودي": [1879543, 88452, 98572],
    "أربيل": [41808, 670218, 4848368],
    "الجزيرة الإماراتي": [1573546, 5636647, 2634904],
    "شبيبة القبائل": [1202069, 3270139, 320850],
    "الهلال السوداني": [209410, 378164, 2117536],
    "النصر السعودي": [2335351, 6623403, 1006830],
    "العربي القطري": [1760040, 1463315, 566072],
    "القوة الجوية": [1261804, 7858853, 2339182],
    "العين": [67860, 231961, 218561],
    "نيوم": [134163, 1906884, 5694119],
    "اتحاد العاصمة": [3912907, 296221, 2333410],
    "الأهلي المصري": [2991642, 7518995, 7520253],
    "الترجي": [5642346, 528968, 28018],
    "الرجاء": [791416, 5725497, 90766],
    "المريخ": [1075334, 21239, 2451310],
    "الوداد": [2042170, 2633744, 1941485],
    "الفتح السعودي": [2274832, 340462, 5778066],
    "النجم الساحلي": [429214, 1936900, 2156199],
    "الأفريقي": [2222152, 136304, 395732],
    "الاتحاد السعودي": [341979, 4878359, 89270],
}

# Reverse lookup: entry_id -> team_name
ENTRY_TO_TEAM = {}
for team_name, ids in TEAMS_FPL_IDS.items():
    for entry_id in ids:
        ENTRY_TO_TEAM[entry_id] = team_name

# Simple cache
_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 120  # Cache for 2 minutes
}

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

def format_captains(captains_list):
    """Format captains list with x2, x3 for duplicates"""
    if not captains_list:
        return []
    
    counter = Counter(captains_list)
    formatted = []
    for cap, count in counter.items():
        if count > 1:
            formatted.append(f"{cap} x{count}")
        else:
            formatted.append(cap)
    return formatted

def get_previous_rank(team_name):
    """Get previous rank based on previous standings"""
    sorted_teams = sorted(PREVIOUS_STANDINGS.items(), key=lambda x: -x[1])
    for i, (name, _) in enumerate(sorted_teams, 1):
        if name == team_name:
            return i
    return 20

def get_arab_league_data():
    """Fetch all data for Arab Championship"""
    global _cache
    
    now = time.time()
    
    if _cache['data'] and (now - _cache['timestamp']) < _cache['ttl']:
        return _cache['data']
    
    try:
        cookies = get_cookies()
        
        # 1) Get bootstrap data
        bootstrap = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/", cookies)
        if not bootstrap:
            raise RuntimeError("Failed to fetch bootstrap data")
        
        events = bootstrap["events"]
        current_gw = next((e["id"] for e in events if e.get("is_current")), None)
        if not current_gw:
            finished = [e for e in events if e.get("finished")]
            current_gw = max(finished, key=lambda e: e["id"])["id"] if finished else 1
        
        player_info = {
            p["id"]: {
                "name": p["web_name"],
                "team": p["team"],
                "position": p["element_type"],
            } for p in bootstrap["elements"]
        }
        
        # 2) Get live data
        live_data = fetch_json(f"https://fantasy.premierleague.com/api/event/{current_gw}/live/", cookies)
        if not live_data:
            raise RuntimeError("Failed to fetch live data")
        
        live_elements = {
            e['id']: {
                'total_points': e['stats']['total_points'],
                'minutes': e['stats']['minutes'],
            } for e in live_data['elements']
        }
        
        # 3) Get fixtures
        fixtures = fetch_json(f"https://fantasy.premierleague.com/api/fixtures/?event={current_gw}", cookies) or []
        
        team_fixture_done = {}
        for fix in fixtures:
            finished = fix.get('finished') or fix.get('finished_provisional')
            started = fix.get('started', False)
            postponed = fix.get('kickoff_time') is None
            done = finished or postponed or started
            team_fixture_done[fix['team_h']] = done
            team_fixture_done[fix['team_a']] = done
        
        # 4) Get current GW matches
        matches_data = fetch_json(f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{ARAB_H2H_LEAGUE_ID}/?event={current_gw}", cookies)
        matches = matches_data.get('results', []) if matches_data else []
        
        # 4b) Get H2H standings for manager names
        league_standings = fetch_json(f"https://fantasy.premierleague.com/api/leagues-h2h/{ARAB_H2H_LEAGUE_ID}/standings/", cookies)
        manager_names = {}
        if league_standings:
            for entry in league_standings.get('standings', {}).get('results', []):
                manager_names[entry['entry']] = entry.get('player_name', f"Manager {entry['entry']}")
        
        # 5) Helper functions
        def calculate_auto_subs(picks, live_elements, player_info, team_fixture_done):
            def pos_of(eid):
                return player_info.get(eid, {}).get('position', 0)
            
            def formation_ok(d, m, f, g):
                return (g == 1 and 3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3)
            
            def team_done(eid):
                team_id = player_info.get(eid, {}).get('team')
                return team_fixture_done.get(team_id, False)
            
            starters = picks[:11]
            bench = picks[11:]
            
            d = sum(1 for p in starters if pos_of(p['element']) == 2)
            m = sum(1 for p in starters if pos_of(p['element']) == 3)
            f = sum(1 for p in starters if pos_of(p['element']) == 4)
            g = sum(1 for p in starters if pos_of(p['element']) == 1)
            
            non_playing_starters = [
                p for p in starters
                if live_elements.get(p['element'], {}).get('minutes', 0) == 0
                and team_done(p['element'])
            ]
            
            used_bench = set()
            sub_points = 0
            
            for starter in non_playing_starters:
                s_id = starter['element']
                s_pos = pos_of(s_id)
                
                for b in bench:
                    b_id = b['element']
                    if b_id in used_bench:
                        continue
                    
                    b_pos = pos_of(b_id)
                    b_min = live_elements.get(b_id, {}).get('minutes', 0)
                    b_played = b_min > 0
                    b_done = team_done(b_id)
                    
                    if (s_pos == 1 and b_pos != 1) or (s_pos != 1 and b_pos == 1):
                        continue
                    
                    if not b_played:
                        if not b_done:
                            used_bench.add(b_id)
                            break
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
                    
                    sub_points += live_elements[b_id]['total_points']
                    used_bench.add(b_id)
                    d, m, f, g = d2, m2, f2, g2
                    break
            
            return sub_points
        
        def calculate_points_from_picks(picks_data, entry_id):
            if not picks_data:
                return 0, '-', 0
            
            picks = picks_data.get('picks', [])
            hits = picks_data.get('entry_history', {}).get('event_transfers_cost', 0)
            
            captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
            captain_name = player_info.get(captain_id, {}).get('name', '-') if captain_id else '-'
            
            total_points = 0
            for i, pick in enumerate(picks[:11]):
                pid = pick['element']
                pts = live_elements.get(pid, {}).get('total_points', 0)
                
                if pick.get('is_captain'):
                    cap_minutes = live_elements.get(pid, {}).get('minutes', 0)
                    cap_team = player_info.get(pid, {}).get('team')
                    cap_played = cap_minutes > 0
                    cap_done = team_fixture_done.get(cap_team, False)
                    
                    if cap_played:
                        pts *= 2
                    elif cap_done:
                        pts = 0
                    
                elif pick.get('is_vice_captain'):
                    cap_id = next((p['element'] for p in picks if p.get('is_captain')), None)
                    if cap_id:
                        cap_minutes = live_elements.get(cap_id, {}).get('minutes', 0)
                        cap_team = player_info.get(cap_id, {}).get('team')
                        cap_done = team_fixture_done.get(cap_team, False)
                        
                        if cap_minutes == 0 and cap_done:
                            vc_minutes = live_elements.get(pid, {}).get('minutes', 0)
                            if vc_minutes > 0:
                                pts *= 2
                
                total_points += pts
            
            sub_points = calculate_auto_subs(picks, live_elements, player_info, team_fixture_done)
            
            return total_points + sub_points - hits, captain_name, hits
        
        # 6) Calculate team points
        team_live_points = {}
        team_captains = {}
        team_picks_counter = {}
        all_managers = []
        
        for team_name, entry_ids in TEAMS_FPL_IDS.items():
            total_pts = 0
            captains = []
            picks_counter = Counter()
            
            for entry_id in entry_ids:
                picks_data = fetch_json(f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{current_gw}/picks/", cookies)
                if picks_data:
                    picks = picks_data.get('picks', [])
                    for p in picks[:11]:
                        picks_counter[p['element']] += 1
                    
                    pts, cap_name, _ = calculate_points_from_picks(picks_data, entry_id)
                    total_pts += pts
                    captains.append(cap_name)
                    
                    mgr_name = manager_names.get(entry_id, f"Manager {entry_id}")
                    all_managers.append({
                        'name': mgr_name,
                        'points': pts,
                        'team': team_name,
                        'entry_id': entry_id
                    })
                else:
                    captains.append('-')
            
            team_live_points[team_name] = total_pts
            team_captains[team_name] = captains
            team_picks_counter[team_name] = picks_counter
        
        best_team = max(team_live_points.items(), key=lambda x: x[1]) if team_live_points else (None, 0)
        best_manager = max(all_managers, key=lambda x: x['points']) if all_managers else {'name': '-', 'points': 0}
        
        # Fetch best manager's actual name (single API call)
        if best_manager.get('entry_id'):
            entry_data = fetch_json(f"https://fantasy.premierleague.com/api/entry/{best_manager['entry_id']}/", cookies)
            if entry_data:
                best_manager['name'] = entry_data.get('player_first_name', '') + ' ' + entry_data.get('player_last_name', '')
                best_manager['name'] = best_manager['name'].strip()
        
        def get_unique_players(team_1, team_2):
            counter_1 = team_picks_counter.get(team_1, Counter())
            counter_2 = team_picks_counter.get(team_2, Counter())
            
            all_players = set(counter_1.keys()) | set(counter_2.keys())
            
            unique_1 = []
            unique_2 = []
            
            for pid in all_players:
                count_1 = counter_1.get(pid, 0)
                count_2 = counter_2.get(pid, 0)
                
                diff = count_1 - count_2
                
                if diff > 0:
                    unique_1.append((pid, diff))
                elif diff < 0:
                    unique_2.append((pid, -diff))
            
            def format_unique(player_list):
                result = []
                for pid, diff_count in player_list:
                    info = player_info.get(pid, {})
                    minutes = live_elements.get(pid, {}).get('minutes', 0)
                    pts = live_elements.get(pid, {}).get('total_points', 0)
                    
                    if minutes > 0:
                        status = 'played'
                    else:
                        team_id = info.get('team')
                        if team_fixture_done.get(team_id, False):
                            status = 'benched'
                        else:
                            status = 'pending'
                    
                    name = info.get('name', 'Unknown')
                    if diff_count > 1:
                        name = f"{name} x{diff_count}"
                    
                    result.append({
                        'name': name,
                        'points': pts * diff_count,
                        'status': status,
                        'minutes': minutes,
                        'count': diff_count
                    })
                
                result.sort(key=lambda x: -x['points'])
                return result
            
            return format_unique(unique_1), format_unique(unique_2)
        
        # 7) Build H2H matches
        h2h_matches = []
        match_results = {}
        
        for match in matches:
            entry_1 = match.get('entry_1_entry')
            entry_2 = match.get('entry_2_entry')
            
            team_1 = ENTRY_TO_TEAM.get(entry_1)
            team_2 = ENTRY_TO_TEAM.get(entry_2)
            
            if team_1 and team_2:
                pts_1 = team_live_points.get(team_1, 0)
                pts_2 = team_live_points.get(team_2, 0)
                
                if pts_1 > pts_2:
                    match_results[team_1] = 'W'
                    match_results[team_2] = 'L'
                    winner = 1
                elif pts_2 > pts_1:
                    match_results[team_2] = 'W'
                    match_results[team_1] = 'L'
                    winner = 2
                else:
                    match_results[team_1] = 'D'
                    match_results[team_2] = 'D'
                    winner = 0
                
                unique_1, unique_2 = get_unique_players(team_1, team_2)
                
                h2h_matches.append({
                    'team_1': team_1,
                    'team_2': team_2,
                    'points_1': pts_1,
                    'points_2': pts_2,
                    'points_diff': abs(pts_1 - pts_2),
                    'winner': winner,
                    'captains_1': format_captains(team_captains.get(team_1, [])),
                    'captains_2': format_captains(team_captains.get(team_2, [])),
                    'team_1_unique': unique_1,
                    'team_2_unique': unique_2,
                })
        
        # 8) Build standings
        team_standings = []
        for team_name in TEAMS_FPL_IDS.keys():
            prev_points = PREVIOUS_STANDINGS.get(team_name, 0)
            prev_rank = get_previous_rank(team_name)
            
            result = match_results.get(team_name, '')
            if result == 'W':
                added_points = 3
            elif result == 'D':
                added_points = 1
            else:
                added_points = 0
            
            projected_points = prev_points + added_points
            
            team_standings.append({
                'team_name': team_name,
                'league_points': projected_points,
                'prev_points': prev_points,
                'live_gw_points': team_live_points.get(team_name, 0),
                'captains': format_captains(team_captains.get(team_name, [])),
                'result': result,
                'prev_rank': prev_rank,
            })
        
        team_standings.sort(key=lambda x: (-x['league_points'], -x['live_gw_points']))
        
        for i, team in enumerate(team_standings, 1):
            team['rank'] = i
            team['rank_change'] = team['prev_rank'] - i
        
        is_live = any(f.get('started') and not f.get('finished_provisional') for f in fixtures)
        
        result = {
            'standings': team_standings,
            'matches': h2h_matches,
            'gameweek': current_gw,
            'total_teams': len(TEAMS_FPL_IDS),
            'is_live': is_live,
            'last_updated': datetime.now().strftime('%H:%M'),
            'best_team': {
                'name': best_team[0],
                'points': best_team[1]
            },
            'best_manager': best_manager
        }
        
        _cache['data'] = result
        _cache['timestamp'] = now
        
        return result
        
    except Exception as e:
        print(f"Error fetching Arab League data: {e}")
        if _cache['data']:
            return _cache['data']
        return {
            'standings': [],
            'matches': [],
            'gameweek': None,
            'total_teams': 0,
            'is_live': False,
            'error': str(e)
        }
