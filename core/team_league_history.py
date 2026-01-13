# -*- coding: utf-8 -*-
"""
Team League History Module

Provides historical data for Arab, Libyan, and Cities leagues.
"""

import requests
import time
from models import TeamLeagueStandings

TIMEOUT = 15

# League configurations
LEAGUE_CONFIGS = {
    'arab': {
        'name': 'البطولة العربية',
        'h2h_id': 1015271,
        'logo': 'arab_logo.png',
        'back_url': '/league/arab',
        'teams': {
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
    },
    'libyan': {
        'name': 'الدوري الليبي',
        'h2h_id': 1231867,
        'logo': 'libyan_logo.png',
        'back_url': '/league/libyan',
        'teams': {
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
    },
    'cities': {
        'name': 'دوري المدن',
        'h2h_id': 1011575,
        'logo': 'cities_logo.png',
        'back_url': '/league/cities',
        'teams': {
            "بوسليم": [102255, 170629, 50261],
            "اوجلة": [423562, 49250, 99910],
            "البازة": [116175, 4005689, 2486966],
            "طرميسة": [701092, 199211, 2098119],
            "درنه": [191337, 4696003, 2601894],
            "ترهونة": [1941402, 2940600, 179958],
            "غريان": [7928, 6889159, 110964],
            "الهضبة": [3530273, 2911452, 1128265],
            "بنغازي": [372479, 568897, 3279877],
            "حي 9 يونيو": [7934485, 1651522, 5259149],
            "الخمس": [1301966, 4168085, 8041861],
            "المحجوب": [2780336, 746231, 1841364],
            "طرابلس": [2841954, 974668, 554016],
            "الفرناج": [129548, 1200849, 1163868],
            "مصراتة": [2501532, 255116, 346814],
            "زليتن": [4795379, 1298141, 3371889],
            "الزاوية": [3507158, 851661, 2811004],
            "القطرون": [3142905, 1760648, 43105],
            "جالو": [5026431, 117063, 97707],
            "سوق الجمعة": [46435, 57593, 4701548],
        }
    }
}


def fetch_json(url):
    """Fetch JSON from URL"""
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None


def get_bootstrap_data():
    """Get bootstrap data"""
    return fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/")


def get_live_data(gameweek):
    """Get live data for a gameweek"""
    return fetch_json(f"https://fantasy.premierleague.com/api/event/{gameweek}/live/")


def get_picks(entry_id, gameweek):
    """Get picks for an entry"""
    return fetch_json(f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gameweek}/picks/")


def get_h2h_matches(league_id, gameweek):
    """Get H2H matches"""
    return fetch_json(f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{league_id}/?event={gameweek}")


def build_player_info(bootstrap):
    """Build player info dict"""
    return {
        p['id']: {
            'name': p['web_name'],
            'team': p['team'],
            'position': p['element_type'],
        }
        for p in bootstrap.get('elements', [])
    }


def build_live_elements(live_data):
    """Build live elements dict"""
    return {
        elem['id']: {
            'total_points': elem['stats']['total_points'],
            'minutes': elem['stats']['minutes'],
        }
        for elem in live_data.get('elements', [])
    }


def calculate_auto_subs(picks, live_elements, player_info):
    """Calculate auto-sub points"""
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


def calculate_manager_points(picks_data, live_elements, player_info):
    """Calculate manager points using custom rules"""
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
    
    total += calculate_auto_subs(picks, live_elements, player_info)
    return total - hits


def get_league_history(league_type, max_gw=None):
    """
    Get complete history for a league.
    Returns dict with gameweek data including matches and standings.
    """
    if league_type not in LEAGUE_CONFIGS:
        return None
    
    config = LEAGUE_CONFIGS[league_type]
    teams = config['teams']
    h2h_id = config['h2h_id']
    
    # Build entry to team mapping
    entry_to_team = {}
    for team_name, ids in teams.items():
        for entry_id in ids:
            entry_to_team[entry_id] = team_name
    
    # Get bootstrap data
    bootstrap = get_bootstrap_data()
    if not bootstrap:
        return None
    
    player_info = build_player_info(bootstrap)
    
    # Determine max gameweek
    if max_gw is None:
        # Get from bootstrap
        events = bootstrap.get('events', [])
        finished_events = [e for e in events if e.get('finished')]
        max_gw = max(e['id'] for e in finished_events) if finished_events else 1
    
    # Process each gameweek
    history = {}
    cumulative_standings = {team: 0 for team in teams.keys()}
    cumulative_fpl = {team: 0 for team in teams.keys()}
    
    for gw in range(1, max_gw + 1):
        live_data = get_live_data(gw)
        if not live_data:
            continue
        
        live_elements = build_live_elements(live_data)
        
        # Calculate team points for this GW
        gw_team_points = {}
        for team_name, entry_ids in teams.items():
            total = 0
            for entry_id in entry_ids:
                picks = get_picks(entry_id, gw)
                if picks:
                    total += calculate_manager_points(picks, live_elements, player_info)
                time.sleep(0.02)  # Small delay
            gw_team_points[team_name] = total
        
        # Get H2H matches
        matches_data = get_h2h_matches(h2h_id, gw)
        if not matches_data or 'results' not in matches_data:
            continue
        
        # Build unique team matchups
        matches = []
        for match in matches_data['results']:
            entry_1 = match.get('entry_1_entry')
            entry_2 = match.get('entry_2_entry')
            team_1 = entry_to_team.get(entry_1)
            team_2 = entry_to_team.get(entry_2)
            
            if team_1 and team_2:
                existing = next((m for m in matches if 
                    (m['team1'] == team_1 and m['team2'] == team_2) or
                    (m['team1'] == team_2 and m['team2'] == team_1)), None)
                if not existing:
                    matches.append({
                        'team1': team_1,
                        'team2': team_2,
                        'points1': gw_team_points.get(team_1, 0),
                        'points2': gw_team_points.get(team_2, 0),
                    })
        
        # Calculate results and update cumulative standings
        gw_results = {}
        for match in matches:
            t1, t2 = match['team1'], match['team2']
            p1, p2 = match['points1'], match['points2']
            
            if p1 > p2:
                gw_results[t1] = 'W'
                gw_results[t2] = 'L'
                cumulative_standings[t1] += 3
            elif p2 > p1:
                gw_results[t1] = 'L'
                gw_results[t2] = 'W'
                cumulative_standings[t2] += 3
            else:
                gw_results[t1] = 'D'
                gw_results[t2] = 'D'
                cumulative_standings[t1] += 1
                cumulative_standings[t2] += 1
        
        # Update cumulative FPL points
        for team in teams.keys():
            cumulative_fpl[team] += gw_team_points.get(team, 0)
        
        # Build standings list sorted by league points, then FPL points
        standings = []
        for team in teams.keys():
            standings.append({
                'name': team,
                'league_points': cumulative_standings[team],
                'total_fpl_points': cumulative_fpl[team],
                'gw_result': gw_results.get(team, '-'),
                'gw_points': gw_team_points.get(team, 0),
            })
        
        standings.sort(key=lambda x: (-x['league_points'], -x['total_fpl_points']))
        
        history[gw] = {
            'matches': matches,
            'standings': standings,
        }
    
    return history


def get_league_history_data(league_type):
    """
    Get all data needed for history page.
    """
    if league_type not in LEAGUE_CONFIGS:
        return None
    
    config = LEAGUE_CONFIGS[league_type]
    history = get_league_history(league_type)
    
    if not history:
        return None
    
    gameweeks = sorted(history.keys())
    
    return {
        'league_name': config['name'],
        'logo_file': config['logo'],
        'back_url': config['back_url'],
        'gameweeks': gameweeks,
        'history': history,
    }
