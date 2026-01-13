# -*- coding: utf-8 -*-
"""
Rebuild All Standings Script

This script rebuilds all standings for Arab, Libyan, and Cities leagues
from GW1 to GW21 with correct league points and cumulative FPL points.

Uses custom points calculation:
- Captain: 2x only (no 3x for triple captain)
- Bench boost: ignored (only first 11 + auto-subs)
- Transfer hits: subtracted

Run from Render Shell:
    python rebuild_all_standings.py
"""

import requests
import time
from app import app, db
from models import TeamLeagueStandings, TeamLeagueMatches

TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY = 2

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
        }
    },
    'cities': {
        'h2h_id': 1011575,
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


def fetch_json(url, retries=MAX_RETRIES):
    """Fetch JSON with retries"""
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:  # Rate limited
                print(f"  Rate limited, waiting {RETRY_DELAY * 2}s...")
                time.sleep(RETRY_DELAY * 2)
            else:
                print(f"  HTTP {r.status_code} for {url}")
        except Exception as e:
            print(f"  Error: {e}")
        
        if attempt < retries - 1:
            time.sleep(RETRY_DELAY)
    
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


def process_gameweek(league_type, league_config, gameweek, player_info, prev_league_standings, prev_fpl_totals):
    """Process a single gameweek for a league"""
    teams = league_config['teams']
    h2h_id = league_config['h2h_id']
    
    # Build reverse lookup
    entry_to_team = {}
    for team_name, ids in teams.items():
        for entry_id in ids:
            entry_to_team[entry_id] = team_name
    
    # Get live data
    live_data = get_live_data(gameweek)
    if not live_data:
        print(f"    ❌ Failed to get live data for GW{gameweek}")
        return None, None
    
    live_elements = build_live_elements(live_data)
    
    # Calculate team FPL points for this GW
    gw_team_points = {}
    for team_name, entry_ids in teams.items():
        total = 0
        for entry_id in entry_ids:
            picks = get_picks(entry_id, gameweek)
            if picks:
                total += calculate_manager_points(picks, live_elements, player_info)
            time.sleep(0.1)  # Small delay to avoid rate limiting
        gw_team_points[team_name] = total
    
    # Get H2H matches
    matches_data = get_h2h_matches(h2h_id, gameweek)
    if not matches_data or 'results' not in matches_data:
        print(f"    ❌ Failed to get H2H matches for GW{gameweek}")
        return None, None
    
    # Determine unique team matchups with points
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
    
    # Calculate W/D/L and league points
    gw_league_points = {team: 0 for team in teams.keys()}
    
    for match in matches:
        t1, t2 = match['team1'], match['team2']
        p1, p2 = match['points1'], match['points2']
        
        if p1 > p2:
            gw_league_points[t1] = 3
            gw_league_points[t2] = 0
        elif p2 > p1:
            gw_league_points[t2] = 3
            gw_league_points[t1] = 0
        else:
            gw_league_points[t1] = 1
            gw_league_points[t2] = 1
    
    # Calculate cumulative standings
    new_league_standings = {}
    new_fpl_totals = {}
    
    for team in teams.keys():
        new_league_standings[team] = prev_league_standings.get(team, 0) + gw_league_points.get(team, 0)
        new_fpl_totals[team] = prev_fpl_totals.get(team, 0) + gw_team_points.get(team, 0)
    
    return new_league_standings, new_fpl_totals, matches


def rebuild_league(league_type, league_config, player_info, start_gw=1, end_gw=21):
    """Rebuild all standings for a league"""
    print(f"\n{'='*60}")
    print(f"  Rebuilding {league_type.upper()} League (GW{start_gw}-{end_gw})")
    print(f"{'='*60}")
    
    # Initialize cumulative totals
    league_standings = {team: 0 for team in league_config['teams'].keys()}
    fpl_totals = {team: 0 for team in league_config['teams'].keys()}
    
    all_gw_data = []
    
    for gw in range(start_gw, end_gw + 1):
        print(f"\n  Processing GW{gw}...")
        
        result = process_gameweek(
            league_type, league_config, gw, player_info,
            league_standings, fpl_totals
        )
        
        if result[0] is None:
            print(f"    ⚠️ Skipping GW{gw} due to errors")
            continue
        
        new_standings, new_fpl, matches = result
        league_standings = new_standings
        fpl_totals = new_fpl
        
        all_gw_data.append({
            'gameweek': gw,
            'standings': league_standings.copy(),
            'fpl_totals': fpl_totals.copy(),
            'matches': matches,
        })
        
        # Show top 3
        sorted_teams = sorted(league_standings.items(), key=lambda x: (-x[1], -fpl_totals.get(x[0], 0)))
        print(f"    Top 3: {sorted_teams[0][0]} ({sorted_teams[0][1]}), {sorted_teams[1][0]} ({sorted_teams[1][1]}), {sorted_teams[2][0]} ({sorted_teams[2][1]})")
    
    return all_gw_data


def save_league_data(league_type, all_gw_data):
    """Save all gameweek data to database"""
    print(f"\n  Saving {league_type} to database...")
    
    with app.app_context():
        # Delete existing standings for this league
        deleted_standings = TeamLeagueStandings.query.filter_by(league_type=league_type).delete()
        print(f"    Deleted {deleted_standings} existing standings records")
        
        # Delete existing matches for this league
        try:
            deleted_matches = TeamLeagueMatches.query.filter_by(league_type=league_type).delete()
            print(f"    Deleted {deleted_matches} existing match records")
        except:
            print(f"    No existing match records to delete")
        
        # Insert standings
        for gw_data in all_gw_data:
            gw = gw_data['gameweek']
            standings = gw_data['standings']
            fpl_totals = gw_data['fpl_totals']
            
            for team_name in standings.keys():
                new_record = TeamLeagueStandings(
                    league_type=league_type,
                    gameweek=gw,
                    team_name=team_name,
                    league_points=standings[team_name],
                    total_fpl_points=fpl_totals[team_name]
                )
                db.session.add(new_record)
        
        # Insert matches
        match_count = 0
        for gw_data in all_gw_data:
            gw = gw_data['gameweek']
            matches = gw_data.get('matches', [])
            
            for match in matches:
                new_match = TeamLeagueMatches(
                    league_type=league_type,
                    gameweek=gw,
                    team1_name=match['team1'],
                    team2_name=match['team2'],
                    team1_points=match['points1'],
                    team2_points=match['points2']
                )
                db.session.add(new_match)
                match_count += 1
        
        db.session.commit()
        print(f"    Saved {len(all_gw_data) * 20} standings records")
        print(f"    Saved {match_count} match records")


def main():
    print("=" * 60)
    print("  REBUILD ALL STANDINGS SCRIPT")
    print("=" * 60)
    print("\nThis will:")
    print("1. Delete ALL existing standings for Arab, Libyan, Cities leagues")
    print("2. Rebuild GW1-GW21 from FPL API with correct calculations")
    print("3. Save league_points and total_fpl_points for each team/gameweek")
    print("\nCustom calculation rules:")
    print("- Captain: 2x only (no 3x triple captain)")
    print("- Bench boost: ignored (only first 11 + auto-subs)")
    print("- Transfer hits: subtracted")
    print("=" * 60)
    
    response = input("\nProceed? (yes/no): ")
    if response.lower() != 'yes':
        print("Aborted.")
        return
    
    # Get bootstrap data
    print("\nFetching bootstrap data...")
    bootstrap = get_bootstrap_data()
    if not bootstrap:
        print("❌ Failed to fetch bootstrap data")
        return
    
    player_info = build_player_info(bootstrap)
    print(f"Loaded {len(player_info)} players")
    
    # Process each league
    all_league_data = {}
    
    for league_type, league_config in LEAGUES.items():
        data = rebuild_league(league_type, league_config, player_info, start_gw=1, end_gw=21)
        all_league_data[league_type] = data
    
    # Confirm before saving
    print("\n" + "=" * 60)
    print("  PREVIEW COMPLETE")
    print("=" * 60)
    
    for league_type, data in all_league_data.items():
        if data:
            final = data[-1]
            print(f"\n{league_type.upper()} Final GW21 Standings:")
            sorted_teams = sorted(
                final['standings'].items(),
                key=lambda x: (-x[1], -final['fpl_totals'].get(x[0], 0))
            )
            for i, (team, pts) in enumerate(sorted_teams[:5], 1):
                fpl = final['fpl_totals'].get(team, 0)
                print(f"  {i}. {team}: {pts} pts (مجموع النقاط: {fpl})")
            print("  ...")
    
    response = input("\nSave to database? (yes/no): ")
    if response.lower() != 'yes':
        print("Not saved.")
        return
    
    # Save to database
    for league_type, data in all_league_data.items():
        if data:
            save_league_data(league_type, data)
    
    print("\n" + "=" * 60)
    print("  ✅ ALL DONE!")
    print("=" * 60)
    
    # Verify
    with app.app_context():
        for league_type in LEAGUES.keys():
            count = TeamLeagueStandings.query.filter_by(league_type=league_type).count()
            print(f"{league_type}: {count} records")


if __name__ == '__main__':
    main()
