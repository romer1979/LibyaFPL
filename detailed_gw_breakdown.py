# -*- coding: utf-8 -*-
"""
Detailed Gameweek Breakdown Script

Shows match results and standings for each gameweek (GW1-12)
to help identify where discrepancies occur.

Run from Render Shell:
    python detailed_gw_breakdown.py
"""

import requests
import time

TIMEOUT = 15

# League configurations
LEAGUES = {
    'arab': {
        'h2h_id': 1015271,
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
        },
        'gw12': {
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
    },
    'libyan': {
        'h2h_id': 1231867,
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
        },
        'gw12': {
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
        }
    },
}


def fetch_json(url):
    """Fetch JSON"""
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None


def get_bootstrap_data():
    return fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/")


def get_live_data(gameweek):
    return fetch_json(f"https://fantasy.premierleague.com/api/event/{gameweek}/live/")


def get_picks(entry_id, gameweek):
    return fetch_json(f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gameweek}/picks/")


def get_h2h_matches(league_id, gameweek):
    return fetch_json(f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{league_id}/?event={gameweek}")


def build_player_info(bootstrap):
    return {
        p['id']: {
            'name': p['web_name'],
            'team': p['team'],
            'position': p['element_type'],
        }
        for p in bootstrap.get('elements', [])
    }


def build_live_elements(live_data):
    return {
        elem['id']: {
            'total_points': elem['stats']['total_points'],
            'minutes': elem['stats']['minutes'],
        }
        for elem in live_data.get('elements', [])
    }


def calculate_auto_subs(picks, live_elements, player_info):
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


def process_league(league_type, league_config, player_info):
    """Process all gameweeks for a league and show detailed breakdown"""
    teams = league_config['teams']
    h2h_id = league_config['h2h_id']
    gw12_expected = league_config['gw12']
    
    entry_to_team = {}
    for team_name, ids in teams.items():
        for entry_id in ids:
            entry_to_team[entry_id] = team_name
    
    # Cumulative standings
    cumulative_standings = {team: 0 for team in teams.keys()}
    
    print(f"\n{'='*80}")
    print(f"  {league_type.upper()} LEAGUE - DETAILED BREAKDOWN")
    print(f"{'='*80}")
    
    for gw in range(1, 13):
        print(f"\n{'='*80}")
        print(f"  GAMEWEEK {gw}")
        print(f"{'='*80}")
        
        live_data = get_live_data(gw)
        if not live_data:
            print("  ❌ Failed to get live data")
            continue
        
        live_elements = build_live_elements(live_data)
        
        # Calculate team points for this GW
        gw_team_points = {}
        print(f"\n  Team FPL Points (Custom Calculation):")
        print(f"  {'-'*40}")
        
        for team_name, entry_ids in teams.items():
            total = 0
            for entry_id in entry_ids:
                picks = get_picks(entry_id, gw)
                if picks:
                    total += calculate_manager_points(picks, live_elements, player_info)
                time.sleep(0.05)
            gw_team_points[team_name] = total
        
        # Sort by points for display
        sorted_teams = sorted(gw_team_points.items(), key=lambda x: -x[1])
        for team, pts in sorted_teams:
            print(f"  {team}: {pts}")
        
        # Get H2H matches
        matches_data = get_h2h_matches(h2h_id, gw)
        if not matches_data or 'results' not in matches_data:
            print(f"\n  ❌ Failed to get H2H matches")
            continue
        
        # Determine matchups
        matches = []
        for match in matches_data['results']:
            entry_1 = match.get('entry_1_entry')
            entry_2 = match.get('entry_2_entry')
            team_1 = entry_to_team.get(entry_1)
            team_2 = entry_to_team.get(entry_2)
            
            if team_1 and team_2:
                existing = next((m for m in matches if 
                    (m['team_1'] == team_1 and m['team_2'] == team_2) or
                    (m['team_1'] == team_2 and m['team_2'] == team_1)), None)
                if not existing:
                    matches.append({'team_1': team_1, 'team_2': team_2})
        
        # Show match results
        print(f"\n  Match Results:")
        print(f"  {'-'*60}")
        
        gw_results = {}
        for match in matches:
            t1, t2 = match['team_1'], match['team_2']
            p1, p2 = gw_team_points.get(t1, 0), gw_team_points.get(t2, 0)
            
            if p1 > p2:
                result = f"{t1} WIN"
                gw_results[t1] = 'W'
                gw_results[t2] = 'L'
                cumulative_standings[t1] += 3
            elif p2 > p1:
                result = f"{t2} WIN"
                gw_results[t1] = 'L'
                gw_results[t2] = 'W'
                cumulative_standings[t2] += 3
            else:
                result = "DRAW"
                gw_results[t1] = 'D'
                gw_results[t2] = 'D'
                cumulative_standings[t1] += 1
                cumulative_standings[t2] += 1
            
            print(f"  {t1} ({p1}) vs {t2} ({p2}) => {result}")
        
        # Show cumulative standings after this GW
        print(f"\n  Cumulative Standings after GW{gw}:")
        print(f"  {'-'*40}")
        sorted_standings = sorted(cumulative_standings.items(), key=lambda x: -x[1])
        for i, (team, pts) in enumerate(sorted_standings, 1):
            gw_result = gw_results.get(team, '-')
            pts_added = 3 if gw_result == 'W' else (1 if gw_result == 'D' else 0)
            print(f"  {i:2}. {team}: {pts} (+{pts_added} {gw_result})")
    
    # Final comparison with expected GW12
    print(f"\n{'='*80}")
    print(f"  FINAL COMPARISON - GW12")
    print(f"{'='*80}")
    print(f"  {'Team':<25} {'Expected':>10} {'Calculated':>12} {'Diff':>8} {'Status':>10}")
    print(f"  {'-'*70}")
    
    all_match = True
    sorted_expected = sorted(gw12_expected.items(), key=lambda x: -x[1])
    
    for team, expected in sorted_expected:
        calculated = cumulative_standings.get(team, 0)
        diff = calculated - expected
        
        if diff == 0:
            status = "✓ Match"
        else:
            status = "❌ DIFF"
            all_match = False
        
        diff_str = f"+{diff}" if diff > 0 else str(diff)
        print(f"  {team:<25} {expected:>10} {calculated:>12} {diff_str:>8} {status:>10}")
    
    return all_match, cumulative_standings


def main():
    print("=" * 80)
    print("  DETAILED GAMEWEEK BREAKDOWN")
    print("  Shows match results and standings for each GW (1-12)")
    print("=" * 80)
    
    # Select league
    print("\nWhich league to analyze?")
    print("  1. Arab League")
    print("  2. Libyan League")
    print("  3. Both")
    
    choice = input("\nEnter choice (1/2/3): ").strip()
    
    if choice == '1':
        leagues_to_process = ['arab']
    elif choice == '2':
        leagues_to_process = ['libyan']
    else:
        leagues_to_process = ['arab', 'libyan']
    
    # Get bootstrap
    print("\nFetching bootstrap data...")
    bootstrap = get_bootstrap_data()
    if not bootstrap:
        print("❌ Failed to get bootstrap data")
        return
    
    player_info = build_player_info(bootstrap)
    print(f"Loaded {len(player_info)} players")
    
    results = {}
    
    for league_type in leagues_to_process:
        league_config = LEAGUES[league_type]
        match, standings = process_league(league_type, league_config, player_info)
        results[league_type] = {'match': match, 'standings': standings}
    
    # Summary
    print("\n" + "=" * 80)
    print("  SUMMARY")
    print("=" * 80)
    
    for league_type, result in results.items():
        status = "✅ All Match" if result['match'] else "❌ Differences Found"
        print(f"  {league_type.upper()}: {status}")


if __name__ == '__main__':
    main()
