# -*- coding: utf-8 -*-
"""
الدوري الليبي - Libyan League
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
from models import get_latest_team_league_standings, save_team_league_standings, get_team_league_standings, get_team_league_standings_full
from core.fpl_api import is_gameweek_finished

# Configuration
LIBYAN_H2H_LEAGUE_ID = 1231867
TIMEOUT = 15
LEAGUE_TYPE = 'libyan'

# Hardcoded standings per gameweek
STANDINGS_BY_GW = {
    12: {
        "الأخضر": 28,
        "يفرن": 27,
        "الصقور": 24,
        "المستقبل": 24,
        "الظهرة": 24,
        "العروبة": 24,
        "الشط": 22,
        "النصر": 21,
        "الجزيرة": 21,
        "الصداقة": 18,
        "الأولمبي": 18,
        "الملعب": 18,
        "النصر زليتن": 15,
        "الأفريقي درنة": 15,
        "الإخاء": 12,
        "المدينة": 12,
        "دارنس": 9,
        "الأهلي طرابلس": 9,
        "الشرارة": 9,
        "السويحلي": 9,
    },
    # GW13 standings will be added here
    # 13: { ... }
}

def get_base_standings_hardcoded(current_gw):
    """Get base standings from hardcoded values"""
    prev_gw = current_gw - 1
    available_gws = sorted(STANDINGS_BY_GW.keys(), reverse=True)
    for gw in available_gws:
        if gw <= prev_gw:
            return STANDINGS_BY_GW[gw].copy(), gw
    if available_gws:
        earliest = min(available_gws)
        return STANDINGS_BY_GW[earliest].copy(), earliest
    return {}, 0

# Team definitions: team_name -> list of FPL entry IDs
TEAMS_FPL_IDS = {
    "السويحلي": [90627, 4314045, 6904125],
    "الأفريقي درنة": [73166, 48803, 157909],
    "المدينة": [1801960, 1616108, 3708101],
    "النصر زليتن": [2864, 32014, 1138535],
    "دارنس": [2042169, 79249, 6918866],
    "النصر": [31117, 1145928, 992855],
    "الصقور": [2365915, 372802, 4991175],
    "الأهلي طرابلس": [1731626, 108289, 1470003],
    "الصداقة": [3714390, 856776, 191126],
    "الأخضر": [48104, 42848, 33884],
    "الأولمبي": [48946, 3990916, 2188316],
    "المستقبل": [1426246, 249320, 2083158],
    "الملعب": [3669605, 1094184, 1847110],
    "الإخاء": [59863, 976705, 6253123],
    "الجزيرة": [165841, 1269288, 2588180],
    "الظهرة": [333686, 5677799, 1306887],
    "الشرارة": [5614876, 1026083, 1037827],
    "يفرن": [2537692, 860303, 4666133],
    "العروبة": [947836, 3954364, 3209689],
    "الشط": [1357695, 318013, 330526],
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

def get_previous_rank(team_name, standings_dict):
    """Get previous rank based on standings dictionary"""
    sorted_teams = sorted(standings_dict.items(), key=lambda x: -x[1])
    for i, (name, _) in enumerate(sorted_teams, 1):
        if name == team_name:
            return i
    return 20

def get_base_standings(current_gw):
    """Get the base standings to build upon.
    Returns (standings_dict, fpl_totals_dict, source_gw)
    """
    prev_gw = current_gw - 1
    
    # First try hardcoded standings
    if prev_gw in STANDINGS_BY_GW:
        return STANDINGS_BY_GW[prev_gw].copy(), {}, prev_gw
    
    # Then try database (with FPL totals)
    db_standings = get_team_league_standings_full(LEAGUE_TYPE, prev_gw)
    if db_standings:
        league_points = {k: v['league_points'] for k, v in db_standings.items()}
        fpl_totals = {k: v['total_fpl_points'] for k, v in db_standings.items()}
        return league_points, fpl_totals, prev_gw
    
    # Fall back to hardcoded function
    standings, gw = get_base_standings_hardcoded(current_gw)
    return standings, {}, gw

def get_libyan_league_data():
    """Fetch all data for Libyan League"""
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
                'bps': e['stats']['bps'],
                'bonus': e['stats'].get('bonus', 0),
            } for e in live_data['elements']
        }
        
        # Calculate and apply projected bonus points (same as Elite League)
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
                players = group[group['bps'] == bps_score]
                num = len(players)
                
                if position == 1:
                    group.loc[players.index, 'bonus'] = 3
                    position += 2 if num > 1 else 1
                elif position == 2:
                    group.loc[players.index, 'bonus'] = 2
                    position = min(position + num, 4)
                elif position == 3:
                    group.loc[players.index, 'bonus'] = 1
                    position += 1
            
            return group
        
        # Build player list for bonus calculation
        bonus_players = []
        for player_data in live_data['elements']:
            player_id = player_data['id']
            bps = player_data['stats']['bps']
            minutes = player_data['stats']['minutes']
            
            for fixture_info in player_data.get('explain', []):
                fixture_id = fixture_info['fixture']
                if bps > 0 or minutes > 0:
                    bonus_players.append({
                        'player_id': player_id,
                        'fixture_id': fixture_id,
                        'bps': bps,
                        'total_points': player_data['stats']['total_points'],
                        'bonus': 0
                    })
                    break
        
        if bonus_players:
            import pandas as pd
            df = pd.DataFrame(bonus_players)
            df = df.groupby('fixture_id', group_keys=False).apply(assign_bonus_points)
            bonus_points_dict = df.set_index('player_id')['bonus'].to_dict()
            
            for player_id, stats in live_elements.items():
                new_bonus = bonus_points_dict.get(player_id, 0)
                stats['total_points'] += new_bonus - stats.get('bonus', 0)
                stats['bonus'] = new_bonus
        
        # 3) Get fixtures
        fixtures = fetch_json(f"https://fantasy.premierleague.com/api/fixtures/?event={current_gw}", cookies) or []
        
        # Build team fixture started status
        team_fixture_started = {}
        for fix in fixtures:
            started = fix.get('started', False)
            team_fixture_started[fix['team_h']] = started
            team_fixture_started[fix['team_a']] = started
        
        def is_game_complete_or_postponed(team_id):
            """Check if team's game is complete or postponed (started OR postponed)"""
            game_started = team_fixture_started.get(team_id, False)
            if game_started:
                return True
            for fix in fixtures:
                if fix['team_h'] == team_id or fix['team_a'] == team_id:
                    started = fix.get('started', False)
                    is_postponed = fix.get('kickoff_time') is None
                    return started or is_postponed
            return False
        
        def are_all_team_fixtures_complete_or_postponed(team_id):
            """Check if all team fixtures are complete or postponed"""
            team_fixtures = [fix for fix in fixtures if fix['team_h'] == team_id or fix['team_a'] == team_id]
            if not team_fixtures:
                return True
            for fix in team_fixtures:
                if not (fix.get('started', False) or fix.get('kickoff_time') is None):
                    return False
            return True
        
        # 4) Get current GW matches
        matches_data = fetch_json(f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{LIBYAN_H2H_LEAGUE_ID}/?event={current_gw}", cookies)
        matches = matches_data.get('results', []) if matches_data else []
        
        # 4b) Get H2H standings for manager names
        league_standings = fetch_json(f"https://fantasy.premierleague.com/api/leagues-h2h/{LIBYAN_H2H_LEAGUE_ID}/standings/", cookies)
        manager_names = {}
        if league_standings:
            for entry in league_standings.get('standings', {}).get('results', []):
                manager_names[entry['entry']] = entry.get('player_name', f"Manager {entry['entry']}")
        
        # 5) Helper functions
        def calculate_auto_subs(picks, live_elements, player_info, fixtures):
            """
            FPL auto-subs (live/expected):
              - For each non-playing starter (XI order), scan bench in order.
              - If a bench player has not played AND his team hasn't started/postponed -> RESERVE him for this starter and stop scanning (adds 0 now).
              - If a bench player has not played AND his team has started/postponed -> reject (DNP).
              - If a bench player has played -> test GK↔GK rule and formation; accept first valid one.
              - Formation after swap must be: GK=1, DEF 3–5, MID 2–5, FWD 1–3.
              - A bench player can be used/reserved at most once.
            Returns total points currently added by accepted bench subs.
            """
            def pos_of(eid):
                return player_info.get(eid, {}).get('position', 0)
            
            def formation_ok(d, m, f, g):
                return (g == 1 and 3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3)
            
            def team_done(eid):
                return are_all_team_fixtures_complete_or_postponed(player_info.get(eid, {}).get('team'))
            
            starters = picks[:11]
            bench = picks[11:]
            
            # Baseline formation from original XI
            d = sum(1 for p in starters if pos_of(p['element']) == 2)
            m = sum(1 for p in starters if pos_of(p['element']) == 3)
            f = sum(1 for p in starters if pos_of(p['element']) == 4)
            g = sum(1 for p in starters if pos_of(p['element']) == 1)
            
            # Non-playing starters eligible for auto-sub (their team's game started/postponed)
            non_playing_starters = [
                p for p in starters
                if live_elements.get(p['element'], {}).get('minutes', 0) == 0
                and team_done(p['element'])
            ]
            
            used_bench_ids = set()  # includes both accepted AND reserved bench players
            sub_points = 0
            
            for starter in non_playing_starters:
                s_id = starter['element']
                s_pos = pos_of(s_id)
                
                for b in bench:
                    b_id = b['element']
                    if b_id in used_bench_ids:
                        continue
                    
                    b_pos = pos_of(b_id)
                    b_min = live_elements.get(b_id, {}).get('minutes', 0)
                    b_played = b_min > 0
                    b_done = team_done(b_id)
                    
                    # GK ↔ GK only; outfield ↔ outfield only
                    if (s_pos == 1 and b_pos != 1) or (s_pos != 1 and b_pos == 1):
                        continue
                    
                    # Not played yet and team not finished -> RESERVE this bench slot for this starter
                    if not b_played and not b_done:
                        used_bench_ids.add(b_id)  # reserved; adds 0 now
                        break  # stop scanning further bench for this starter
                    
                    # Not played and team finished -> DNP, reject and continue
                    if not b_played and b_done:
                        continue
                    
                    # Bench has played -> simulate swap and validate formation
                    d2, m2, f2, g2 = d, m, f, g
                    if   s_pos == 2: d2 -= 1
                    elif s_pos == 3: m2 -= 1
                    elif s_pos == 4: f2 -= 1
                    elif s_pos == 1: g2 -= 1
                    
                    if   b_pos == 2: d2 += 1
                    elif b_pos == 3: m2 += 1
                    elif b_pos == 4: f2 += 1
                    elif b_pos == 1: g2 += 1
                    
                    if not formation_ok(d2, m2, f2, g2):
                        continue
                    
                    # Accept this bench player
                    sub_points += live_elements.get(b_id, {}).get('total_points', 0)
                    used_bench_ids.add(b_id)
                    d, m, f, g = d2, m2, f2, g2  # commit formation for next substitutions
                    break  # move to next non-playing starter
            
            return sub_points
        
        def calculate_points_from_picks(picks_data, entry_id):
            if not picks_data:
                return 0, '-', 0
            
            picks = picks_data.get('picks', [])
            hits = picks_data.get('entry_history', {}).get('event_transfers_cost', 0)
            
            # Find captain and vice-captain
            captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
            vice_captain_id = next((p['element'] for p in picks if p.get('is_vice_captain')), None)
            captain_name = player_info.get(captain_id, {}).get('name', '-') if captain_id else '-'
            
            # Check captain status using is_game_complete_or_postponed
            captain_minutes = live_elements.get(captain_id, {}).get('minutes', 0) if captain_id else 0
            captain_team = player_info.get(captain_id, {}).get('team') if captain_id else None
            captain_played = captain_minutes > 0
            captain_team_game_complete_or_postponed = is_game_complete_or_postponed(captain_team) if captain_team else False
            
            total_points = 0
            for pick in picks[:11]:
                pid = pick['element']
                pts = live_elements.get(pid, {}).get('total_points', 0)
                
                # Captain logic (always 2x for team leagues, no 3xc)
                if pick.get('is_captain'):
                    if captain_played:
                        pts *= 2  # Captain played - gets 2x
                    elif captain_team_game_complete_or_postponed:
                        pts *= 0  # Captain didn't play and team started/postponed - 0 points (VC takes over)
                    else:
                        pts *= 1  # Captain's team hasn't started - wait (1x for now)
                
                # Vice-captain logic
                elif pick.get('is_vice_captain'):
                    if captain_team_game_complete_or_postponed and not captain_played:
                        # Captain didn't play and his team is done - VC gets captaincy
                        vc_minutes = live_elements.get(pid, {}).get('minutes', 0)
                        vc_team = player_info.get(pid, {}).get('team')
                        vc_team_game_complete_or_postponed = is_game_complete_or_postponed(vc_team) if vc_team else False
                        
                        if vc_minutes > 0:
                            pts *= 2  # VC played - gets 2x
                        elif vc_team_game_complete_or_postponed:
                            pts *= 0  # VC also didn't play and team done - 0
                        else:
                            pts *= 1  # VC's team hasn't started yet - wait
                
                total_points += pts
            
            sub_points = calculate_auto_subs(picks, live_elements, player_info, fixtures)
            
            return total_points + sub_points - hits, captain_name, hits
        
        # 6) Calculate team points
        team_live_points = {}
        team_captains = {}
        team_picks_counter = {}
        all_managers = []
        
        def simulate_autosubs_for_xi(picks):
            """Simulate auto-subs and return list of player IDs in final XI"""
            def pos_of(eid):
                return player_info.get(eid, {}).get('position', 0)
            
            def formation_ok(d, m, f, g):
                return g == 1 and 3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3
            
            def team_done(eid):
                return are_all_team_fixtures_complete_or_postponed(player_info.get(eid, {}).get('team'))
            
            starters = picks[:11]
            bench = picks[11:]
            
            xi_ids = [p['element'] for p in starters]
            
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
                    
                    if not b_played and not b_done:
                        used_bench.add(b_id)
                        xi_ids.remove(s_id)
                        xi_ids.append(b_id)
                        break
                    
                    if not b_played and b_done:
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
                    
                    xi_ids.remove(s_id)
                    xi_ids.append(b_id)
                    used_bench.add(b_id)
                    d, m, f, g = d2, m2, f2, g2
                    break
            
            return xi_ids
        
        for team_name, entry_ids in TEAMS_FPL_IDS.items():
            total_pts = 0
            captains = []
            picks_counter = Counter()
            
            for entry_id in entry_ids:
                picks_data = fetch_json(f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{current_gw}/picks/", cookies)
                if picks_data:
                    picks = picks_data.get('picks', [])
                    
                    # Find captain info for this manager
                    captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
                    vice_captain_id = next((p['element'] for p in picks if p.get('is_vice_captain')), None)
                    
                    # Check if captain played
                    captain_minutes = live_elements.get(captain_id, {}).get('minutes', 0) if captain_id else 0
                    captain_team = player_info.get(captain_id, {}).get('team') if captain_id else None
                    captain_played = captain_minutes > 0
                    captain_team_done = is_game_complete_or_postponed(captain_team) if captain_team else False
                    
                    # Determine effective captain (captain or vice-captain if captain DNP)
                    effective_captain_id = None
                    if captain_played:
                        effective_captain_id = captain_id
                    elif captain_team_done and not captain_played:
                        # Captain DNP, check vice-captain
                        vc_minutes = live_elements.get(vice_captain_id, {}).get('minutes', 0) if vice_captain_id else 0
                        if vc_minutes > 0:
                            effective_captain_id = vice_captain_id
                    elif not captain_team_done:
                        # Captain's game not done yet, assume captain will play
                        effective_captain_id = captain_id
                    
                    # Simulate auto-subs and count final XI players
                    final_xi = simulate_autosubs_for_xi(picks)
                    for pid in final_xi:
                        picks_counter[pid] += 1
                        # Count captain twice (since they get 2x points)
                        if pid == effective_captain_id:
                            picks_counter[pid] += 1
                    
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
        
        # Find best team(s) (team of the week) - show all tied winners
        if team_live_points:
            max_team_pts = max(team_live_points.values())
            best_teams = [name for name, pts in team_live_points.items() if pts == max_team_pts]
            best_team = (', '.join(best_teams), max_team_pts)
        else:
            best_team = (None, 0)
        
        # Find best manager(s) (star of the week) - show all tied winners
        if all_managers:
            max_manager_pts = max(m['points'] for m in all_managers)
            best_managers = [m for m in all_managers if m['points'] == max_manager_pts]
            
            # Fetch all best managers' actual names
            best_manager_names = []
            best_manager_teams = []
            for mgr in best_managers:
                if mgr.get('entry_id'):
                    entry_data = fetch_json(f"https://fantasy.premierleague.com/api/entry/{mgr['entry_id']}/", cookies)
                    if entry_data:
                        full_name = entry_data.get('player_first_name', '') + ' ' + entry_data.get('player_last_name', '')
                        best_manager_names.append(full_name.strip())
                        best_manager_teams.append(mgr.get('team', ''))
                    else:
                        best_manager_names.append(mgr.get('name', '-'))
                        best_manager_teams.append(mgr.get('team', ''))
                else:
                    best_manager_names.append(mgr.get('name', '-'))
                    best_manager_teams.append(mgr.get('team', ''))
            
            best_manager = {
                'name': ', '.join(best_manager_names),
                'points': max_manager_pts,
                'team': ', '.join(best_manager_teams) if len(set(best_manager_teams)) > 1 else best_manager_teams[0] if best_manager_teams else ''
            }
        else:
            best_manager = {'name': '-', 'points': 0, 'team': ''}
        
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
                    team_id = info.get('team')
                    
                    # Check if game is in progress
                    game_in_progress = False
                    
                    for fix in fixtures:
                        if fix['team_h'] == team_id or fix['team_a'] == team_id:
                            started = fix.get('started', False)
                            finished = fix.get('finished', False) or fix.get('finished_provisional', False)
                            if started and not finished:
                                game_in_progress = True
                                break
                    
                    # Simple status: playing (blue), played (grey), pending (purple)
                    if minutes > 0:
                        if game_in_progress:
                            status = 'playing'  # Blue - currently on pitch
                        else:
                            status = 'played'   # Grey - finished
                    else:
                        status = 'pending'      # Purple - yet to play
                    
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
        
        # 8) Get base standings from database or initial standings
        base_standings, base_fpl_totals, base_gw = get_base_standings(current_gw)
        
        # 9) Build standings
        team_standings = []
        for team_name in TEAMS_FPL_IDS.keys():
            prev_points = base_standings.get(team_name, 0)
            prev_fpl_total = base_fpl_totals.get(team_name, 0)
            prev_rank = get_previous_rank(team_name, base_standings)
            
            result = match_results.get(team_name, '')
            if result == 'W':
                added_points = 3
            elif result == 'D':
                added_points = 1
            else:
                added_points = 0
            
            projected_points = prev_points + added_points
            
            # Calculate total FPL points (previous + current GW)
            current_gw_fpl = team_live_points.get(team_name, 0)
            total_fpl_points = prev_fpl_total + current_gw_fpl
            
            team_standings.append({
                'team_name': team_name,
                'league_points': projected_points,
                'prev_points': prev_points,
                'live_gw_points': current_gw_fpl,
                'total_fpl_points': total_fpl_points,
                'captains': format_captains(team_captains.get(team_name, [])),
                'result': result,
                'prev_rank': prev_rank,
            })
        
        team_standings.sort(key=lambda x: (-x['league_points'], -x['total_fpl_points']))
        
        for i, team in enumerate(team_standings, 1):
            team['rank'] = i
            team['rank_change'] = team['prev_rank'] - i
        
        # Check GW status
        any_started = any(f.get('started', False) for f in fixtures)
        all_matches_done = all(f.get('finished') or f.get('finished_provisional') for f in fixtures) if fixtures else False
        is_live = any_started and not all_matches_done
        
        # Use 24-hour buffer ONLY for saving standings to database
        gw_finished_for_save = is_gameweek_finished(current_gw, fixtures)
        
        # For display purposes, GW is "finished" when all matches are done
        gw_finished_display = all_matches_done
        
        # 10) Save standings to database if GW is finished (24 hours after last match)
        if gw_finished_for_save:
            final_standings = {team['team_name']: team['league_points'] for team in team_standings}
            final_fpl_totals = {team['team_name']: team['total_fpl_points'] for team in team_standings}
            save_team_league_standings(LEAGUE_TYPE, current_gw, final_standings, final_fpl_totals)
        
        result = {
            'standings': team_standings,
            'matches': h2h_matches,
            'gameweek': current_gw,
            'total_teams': len(TEAMS_FPL_IDS),
            'is_live': is_live,
            'gw_finished': gw_finished_display,
            'base_gw': base_gw,
            'last_updated_utc': datetime.utcnow().isoformat() + 'Z',
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
        print(f"Error fetching Libyan League data: {e}")
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
