# -*- coding: utf-8 -*-
"""
Initialize GW13 standings for team leagues
Calculates results from GW13 matches and saves to database
Run this once after deploying to set up GW13 standings
"""

import os
import sys
import requests
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import TeamLeagueStandings, save_team_league_standings

TIMEOUT = 15

def get_cookies():
    return {
        'sessionid': os.environ.get('FPL_SESSION_ID', ''),
        'csrftoken': os.environ.get('FPL_CSRF_TOKEN', '')
    }

def fetch_json(url, cookies=None):
    try:
        r = requests.get(url, cookies=cookies, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"Fetch error: {e}")
        return None

# ============================================
# CITIES LEAGUE
# ============================================
CITIES_H2H_LEAGUE_ID = 1011575
CITIES_INITIAL_STANDINGS = {
    "جالو": 33,
    "طرميسة": 24,
    "غريان": 24,
    "اوجلة": 21,
    "حي 9 يونيو": 19,
    "ترهونة": 19,
    "الهضبة": 19,
    "المحجوب": 18,
    "القطرون": 18,
    "بنغازي": 18,
    "طرابلس": 18,
    "درنه": 18,
    "بوسليم": 16,
    "الخمس": 16,
    "البازة": 15,
    "زليتن": 15,
    "الفرناج": 15,
    "الزاوية": 13,
    "سوق الجمعة": 9,
    "مصراتة": 9,
}
CITIES_TEAMS_FPL_IDS = {
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

# ============================================
# LIBYAN LEAGUE
# ============================================
LIBYAN_H2H_LEAGUE_ID = 1231867
LIBYAN_INITIAL_STANDINGS = {
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
LIBYAN_TEAMS_FPL_IDS = {
    "السويحلي": [90627, 4314045, 6904125],
    "الأفريقي درنة": [73166, 48803, 157909],
    "المدينة": [1801960, 1616108, 3708101],
    "النصر زليتن": [2864, 32014, 1138535],
    "دارنس": [2042169, 79249, 6918866],
    "الشرارة": [4474659, 4665498, 1382702],
    "العروبة": [2429965, 104498, 2155970],
    "الصقور": [7161174, 6656930, 6698684],
    "الإخاء": [168059, 1282550, 3049220],
    "الأهلي طرابلس": [1011498, 5765498, 1018875],
    "النصر": [139498, 2440757, 1304043],
    "الشط": [8027734, 189473, 31498],
    "يفرن": [8102498, 2486232, 6905498],
    "الأخضر": [47498, 93498, 2899498],
    "الصداقة": [161498, 3216498, 5626498],
    "الملعب": [3312498, 4315498, 76498],
    "الجزيرة": [2988586, 92498, 41498],
    "الظهرة": [7598, 4614103, 1050498],
    "الأولمبي": [24498, 2434498, 4656498],
    "المستقبل": [6498, 1040498, 3389498],
}

# ============================================
# ARAB LEAGUE
# ============================================
ARAB_H2H_LEAGUE_ID = 1015271
ARAB_INITIAL_STANDINGS = {
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
ARAB_TEAMS_FPL_IDS = {
    "الهلال السعودي": [1879543, 88452, 98572],
    "أربيل": [41808, 670218, 4848368],
    "الجزيرة الإماراتي": [1573546, 5636647, 2634904],
    "شبيبة القبائل": [1202069, 3270139, 320850],
    "الهلال السوداني": [209410, 378164, 2117536],
    "المريخ": [5766070, 2401629, 2119541],
    "الرجاء": [1137498, 3303498, 1572498],
    "النجم الساحلي": [6168498, 99498, 6082498],
    "الأفريقي": [2296498, 4146498, 1070498],
    "اتحاد العاصمة": [2115498, 2163498, 1065498],
    "الترجي": [6376498, 6364498, 6430498],
    "الوداد": [6332498, 1109498, 1085498],
    "الأهلي المصري": [5933498, 5930498, 5893498],
    "القوة الجوية": [5660498, 5700498, 5651498],
    "العين": [5569498, 5590498, 5555498],
    "نيوم": [5540498, 5471498, 5415498],
    "الفتح السعودي": [5352498, 5361498, 5332498],
    "الاتحاد السعودي": [5216498, 5219498, 5232498],
    "النصر السعودي": [5276498, 5280498, 5246498],
    "العربي القطري": [5127498, 5157498, 5109498],
}


def calculate_gw13_standings(league_type, league_id, teams_fpl_ids, initial_standings):
    """Calculate GW13 standings based on match results"""
    print(f"\n{'='*50}")
    print(f"Calculating GW13 standings for {league_type}")
    print(f"{'='*50}")
    
    cookies = get_cookies()
    
    # Build reverse lookup: entry_id -> team_name
    entry_to_team = {}
    for team_name, ids in teams_fpl_ids.items():
        for entry_id in ids:
            entry_to_team[entry_id] = team_name
    
    # Fetch GW13 matches
    matches_url = f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{league_id}/?event=13"
    matches_data = fetch_json(matches_url, cookies)
    
    if not matches_data:
        print(f"ERROR: Could not fetch GW13 matches for {league_type}")
        return None
    
    # Fetch GW13 live data for points
    live_url = "https://fantasy.premierleague.com/api/event/13/live/"
    live_data = fetch_json(live_url, cookies)
    
    if not live_data:
        print(f"ERROR: Could not fetch GW13 live data")
        return None
    
    # Build live elements dict
    live_elements = {elem['id']: elem['stats']['total_points'] for elem in live_data['elements']}
    
    # Calculate team points from GW13 matches
    team_gw_points = {team: 0 for team in teams_fpl_ids.keys()}
    
    # Fetch picks for each manager and sum team points
    for team_name, entry_ids in teams_fpl_ids.items():
        total_pts = 0
        for entry_id in entry_ids:
            picks_url = f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/13/picks/"
            picks_data = fetch_json(picks_url, cookies)
            
            if picks_data:
                # Calculate points (simplified - just starting 11)
                picks = picks_data.get('picks', [])[:11]
                manager_pts = 0
                for pick in picks:
                    element_id = pick['element']
                    pts = live_elements.get(element_id, 0)
                    multiplier = pick.get('multiplier', 1)
                    # TC counts as 2x in team leagues
                    if multiplier == 3:
                        multiplier = 2
                    manager_pts += pts * multiplier
                
                # Subtract transfer cost
                manager_pts -= picks_data.get('entry_history', {}).get('event_transfers_cost', 0)
                total_pts += manager_pts
        
        team_gw_points[team_name] = total_pts
    
    print(f"\nTeam GW13 Points:")
    for team, pts in sorted(team_gw_points.items(), key=lambda x: -x[1]):
        print(f"  {team}: {pts}")
    
    # Determine H2H results from matches
    match_results = {}  # team_name -> 'W', 'L', 'D'
    
    matches = matches_data.get('results', [])
    print(f"\nGW13 H2H Matches:")
    
    for match in matches:
        entry_1 = match.get('entry_1_entry')
        entry_2 = match.get('entry_2_entry')
        
        if not entry_1 or not entry_2:
            continue
        
        team_1 = entry_to_team.get(entry_1)
        team_2 = entry_to_team.get(entry_2)
        
        if not team_1 or not team_2:
            continue
        
        # Skip if same team (shouldn't happen but just in case)
        if team_1 == team_2:
            continue
        
        # Skip if already processed this matchup
        if team_1 in match_results:
            continue
        
        pts_1 = team_gw_points.get(team_1, 0)
        pts_2 = team_gw_points.get(team_2, 0)
        
        if pts_1 > pts_2:
            match_results[team_1] = 'W'
            match_results[team_2] = 'L'
            winner = team_1
        elif pts_2 > pts_1:
            match_results[team_1] = 'L'
            match_results[team_2] = 'W'
            winner = team_2
        else:
            match_results[team_1] = 'D'
            match_results[team_2] = 'D'
            winner = 'DRAW'
        
        print(f"  {team_1} ({pts_1}) vs {team_2} ({pts_2}) -> {winner}")
    
    # Calculate GW13 standings
    gw13_standings = {}
    print(f"\nGW13 Standings:")
    
    for team_name in teams_fpl_ids.keys():
        base_pts = initial_standings.get(team_name, 0)
        result = match_results.get(team_name, '')
        
        if result == 'W':
            added = 3
        elif result == 'D':
            added = 1
        else:
            added = 0
        
        gw13_pts = base_pts + added
        gw13_standings[team_name] = gw13_pts
        print(f"  {team_name}: {base_pts} + {added} = {gw13_pts} ({result})")
    
    return gw13_standings


def main():
    """Main function to calculate and save GW13 standings"""
    
    with app.app_context():
        # Create tables if they don't exist
        db.create_all()
        
        # Check if GW13 standings already exist
        existing = TeamLeagueStandings.query.filter_by(gameweek=13).first()
        if existing:
            print("GW13 standings already exist in database!")
            response = input("Do you want to recalculate and overwrite? (yes/no): ")
            if response.lower() != 'yes':
                print("Aborting.")
                return
            # Delete existing GW13 standings
            TeamLeagueStandings.query.filter_by(gameweek=13).delete()
            db.session.commit()
            print("Deleted existing GW13 standings.")
        
        # Calculate and save for each league
        leagues = [
            ('cities', CITIES_H2H_LEAGUE_ID, CITIES_TEAMS_FPL_IDS, CITIES_INITIAL_STANDINGS),
            ('libyan', LIBYAN_H2H_LEAGUE_ID, LIBYAN_TEAMS_FPL_IDS, LIBYAN_INITIAL_STANDINGS),
            ('arab', ARAB_H2H_LEAGUE_ID, ARAB_TEAMS_FPL_IDS, ARAB_INITIAL_STANDINGS),
        ]
        
        for league_type, league_id, teams_fpl_ids, initial_standings in leagues:
            gw13_standings = calculate_gw13_standings(
                league_type, league_id, teams_fpl_ids, initial_standings
            )
            
            if gw13_standings:
                success = save_team_league_standings(league_type, 13, gw13_standings)
                if success:
                    print(f"\n✓ Saved GW13 standings for {league_type}")
                else:
                    print(f"\n✗ Failed to save GW13 standings for {league_type}")
            else:
                print(f"\n✗ Failed to calculate GW13 standings for {league_type}")
        
        print("\n" + "="*50)
        print("GW13 Initialization Complete!")
        print("="*50)


if __name__ == '__main__':
    main()
