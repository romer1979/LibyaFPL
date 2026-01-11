# -*- coding: utf-8 -*-
"""
Script to reconstruct GW13 Arab League results and fix all standings from GW14 onwards.

Run this from Render Shell:
    python fix_arab_gw13.py
"""

import requests
from app import app, db
from models import TeamLeagueStandings

TIMEOUT = 15
ARAB_H2H_LEAGUE_ID = 1015271
GW_TO_FIX = 13

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

# Reverse lookup
ENTRY_TO_TEAM = {}
for team_name, ids in TEAMS_FPL_IDS.items():
    for entry_id in ids:
        ENTRY_TO_TEAM[entry_id] = team_name

# GW12 base standings (hardcoded)
GW12_STANDINGS = {
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


def fetch_json(url):
    """Simple fetch"""
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        print(f"Error fetching {url}: {r.status_code}")
        return None
    except Exception as e:
        print(f"Fetch error: {e}")
        return None


def get_bootstrap_data():
    """Get bootstrap data for player info"""
    return fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/")


def get_live_data(gameweek):
    """Get live data for a gameweek"""
    return fetch_json(f"https://fantasy.premierleague.com/api/event/{gameweek}/live/")


def get_picks(entry_id, gameweek):
    """Get picks for an entry in a gameweek"""
    return fetch_json(f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gameweek}/picks/")


def build_player_info(bootstrap):
    """Build player info dictionary"""
    player_info = {}
    for p in bootstrap.get('elements', []):
        player_info[p['id']] = {
            'name': p['web_name'],
            'team': p['team'],
            'position': p['element_type'],
        }
    return player_info


def build_live_elements(live_data):
    """Build live elements dictionary"""
    live_elements = {}
    for elem in live_data.get('elements', []):
        live_elements[elem['id']] = {
            'total_points': elem['stats']['total_points'],
            'minutes': elem['stats']['minutes'],
        }
    return live_elements


def calculate_auto_subs(picks, live_elements, player_info):
    """
    Calculate auto-sub points following league rules.
    """
    def pos_of(eid):
        return player_info.get(eid, {}).get('position', 0)
    
    def formation_ok(d, m, f, g):
        return (g == 1 and 3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3)
    
    starters = picks[:11]
    bench = picks[11:]
    
    # Baseline formation
    d = sum(1 for p in starters if pos_of(p['element']) == 2)
    m = sum(1 for p in starters if pos_of(p['element']) == 3)
    f = sum(1 for p in starters if pos_of(p['element']) == 4)
    g = sum(1 for p in starters if pos_of(p['element']) == 1)
    
    # Non-playing starters (GW is finished so all teams are done)
    non_playing_starters = [
        p for p in starters
        if live_elements.get(p['element'], {}).get('minutes', 0) == 0
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
            b_min = live_elements.get(b_id, {}).get('minutes', 0)
            
            # GK ↔ GK only; outfield ↔ outfield only
            if (s_pos == 1 and b_pos != 1) or (s_pos != 1 and b_pos == 1):
                continue
            
            # Bench player didn't play -> skip
            if b_min == 0:
                continue
            
            # Simulate swap and validate formation
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
            d, m, f, g = d2, m2, f2, g2
            break
    
    return sub_points


def calculate_manager_points(picks_data, live_elements, player_info):
    """
    Calculate points for a manager following league rules:
    - Captain: always 2x (no 3x for triple captain)
    - Bench boost: ignored (only first 11 + auto-subs)
    - Transfer hits: subtracted
    """
    if not picks_data:
        return 0
    
    picks = picks_data.get('picks', [])
    hits = picks_data.get('entry_history', {}).get('event_transfers_cost', 0)
    
    if not picks:
        return 0
    
    # Find captain and vice-captain
    captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
    vice_captain_id = next((p['element'] for p in picks if p.get('is_vice_captain')), None)
    
    captain_minutes = live_elements.get(captain_id, {}).get('minutes', 0) if captain_id else 0
    captain_played = captain_minutes > 0
    
    total_points = 0
    for pick in picks[:11]:
        pid = pick['element']
        pts = live_elements.get(pid, {}).get('total_points', 0)
        
        # Captain logic (always 2x, no 3xc)
        if pick.get('is_captain'):
            if captain_played:
                pts *= 2
            else:
                pts = 0  # Captain didn't play, VC takes over
        
        # Vice-captain logic
        elif pick.get('is_vice_captain'):
            if not captain_played:
                vc_minutes = live_elements.get(pid, {}).get('minutes', 0)
                if vc_minutes > 0:
                    pts *= 2  # VC becomes captain
        
        total_points += pts
    
    # Add auto-sub points
    sub_points = calculate_auto_subs(picks, live_elements, player_info)
    
    return total_points + sub_points - hits


def get_gw13_team_points():
    """
    Calculate total points for each team in GW13 using correct league rules.
    """
    print("\n=== Fetching GW13 data ===\n")
    
    # Get bootstrap and live data
    bootstrap = get_bootstrap_data()
    if not bootstrap:
        print("Failed to fetch bootstrap data!")
        return None
    
    live_data = get_live_data(GW_TO_FIX)
    if not live_data:
        print("Failed to fetch live data!")
        return None
    
    player_info = build_player_info(bootstrap)
    live_elements = build_live_elements(live_data)
    
    print(f"Loaded {len(player_info)} players, {len(live_elements)} live elements\n")
    
    team_points = {team: 0 for team in TEAMS_FPL_IDS.keys()}
    
    for team_name, entry_ids in TEAMS_FPL_IDS.items():
        total = 0
        print(f"{team_name}:")
        
        for entry_id in entry_ids:
            picks_data = get_picks(entry_id, GW_TO_FIX)
            
            if picks_data:
                points = calculate_manager_points(picks_data, live_elements, player_info)
                total += points
                print(f"  Entry {entry_id}: {points} pts")
            else:
                print(f"  Entry {entry_id}: Failed to fetch picks!")
        
        team_points[team_name] = total
        print(f"  TOTAL: {total} pts\n")
    
    return team_points


def get_gw13_h2h_matches():
    """
    Fetch GW13 H2H matches from FPL API.
    """
    print("\n=== Fetching GW13 H2H Matches ===\n")
    
    url = f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{ARAB_H2H_LEAGUE_ID}/?event={GW_TO_FIX}"
    data = fetch_json(url)
    
    if not data or 'results' not in data:
        print("Failed to fetch H2H matches!")
        return []
    
    matches = []
    for match in data['results']:
        entry_1 = match.get('entry_1_entry')
        entry_2 = match.get('entry_2_entry')
        
        team_1 = ENTRY_TO_TEAM.get(entry_1)
        team_2 = ENTRY_TO_TEAM.get(entry_2)
        
        if team_1 and team_2:
            # Only add unique team matchups
            existing = next((m for m in matches if 
                (m['team_1'] == team_1 and m['team_2'] == team_2) or
                (m['team_1'] == team_2 and m['team_2'] == team_1)), None)
            
            if not existing:
                matches.append({
                    'team_1': team_1,
                    'team_2': team_2,
                })
    
    print(f"Found {len(matches)} unique team matchups")
    return matches


def calculate_gw13_results(team_points, matches):
    """
    Calculate W/D/L for each team based on GW13 points.
    """
    print("\n=== GW13 Match Results ===\n")
    
    results = {}
    
    for match in matches:
        team_1 = match['team_1']
        team_2 = match['team_2']
        pts_1 = team_points.get(team_1, 0)
        pts_2 = team_points.get(team_2, 0)
        
        if pts_1 > pts_2:
            results[team_1] = 'W'
            results[team_2] = 'L'
            result_str = f"{team_1} WIN"
        elif pts_2 > pts_1:
            results[team_1] = 'L'
            results[team_2] = 'W'
            result_str = f"{team_2} WIN"
        else:
            results[team_1] = 'D'
            results[team_2] = 'D'
            result_str = "DRAW"
        
        print(f"{team_1} ({pts_1}) vs {team_2} ({pts_2}) => {result_str}")
    
    return results


def calculate_gw13_points_to_add(results):
    """
    Calculate how many points each team should have gotten in GW13.
    """
    points_to_add = {}
    
    for team_name in TEAMS_FPL_IDS.keys():
        result = results.get(team_name, '')
        if result == 'W':
            points_to_add[team_name] = 3
        elif result == 'D':
            points_to_add[team_name] = 1
        else:
            points_to_add[team_name] = 0
    
    return points_to_add


def show_gw13_standings(points_to_add):
    """
    Show what GW13 standings should have been.
    """
    print("\n=== GW13 Standings (should have been saved) ===\n")
    
    gw13_standings = {}
    for team_name, base_points in GW12_STANDINGS.items():
        gw13_standings[team_name] = base_points + points_to_add.get(team_name, 0)
    
    sorted_standings = sorted(gw13_standings.items(), key=lambda x: -x[1])
    
    for i, (team, pts) in enumerate(sorted_standings, 1):
        added = points_to_add.get(team, 0)
        result = 'W' if added == 3 else ('D' if added == 1 else 'L')
        print(f"{i:2}. {team}: {pts} (+{added} {result})")
    
    return gw13_standings


def fix_database(points_to_add, dry_run=True):
    """
    Add missing GW13 points to all saved gameweeks (GW14+).
    """
    print(f"\n=== {'DRY RUN - ' if dry_run else ''}Fixing Database ===\n")
    
    with app.app_context():
        all_standings = TeamLeagueStandings.query.filter_by(
            league_type='arab'
        ).order_by(TeamLeagueStandings.gameweek).all()
        
        gameweeks_to_fix = set()
        
        for standing in all_standings:
            if standing.gameweek >= 14:
                gameweeks_to_fix.add(standing.gameweek)
                old_points = standing.league_points
                new_points = old_points + points_to_add.get(standing.team_name, 0)
                
                print(f"GW{standing.gameweek} {standing.team_name}: {old_points} -> {new_points} (+{points_to_add.get(standing.team_name, 0)})")
                
                if not dry_run:
                    standing.league_points = new_points
        
        print(f"\n{'Would save' if dry_run else 'Saving'} GW13 standings...")
        for team_name, base_points in GW12_STANDINGS.items():
            gw13_points = base_points + points_to_add.get(team_name, 0)
            print(f"  GW13 {team_name}: {gw13_points}")
            
            if not dry_run:
                existing = TeamLeagueStandings.query.filter_by(
                    league_type='arab',
                    gameweek=13,
                    team_name=team_name
                ).first()
                
                if existing:
                    existing.league_points = gw13_points
                else:
                    new_standing = TeamLeagueStandings(
                        league_type='arab',
                        gameweek=13,
                        team_name=team_name,
                        league_points=gw13_points
                    )
                    db.session.add(new_standing)
        
        if not dry_run:
            db.session.commit()
            print("\n✅ Database updated successfully!")
        else:
            print(f"\n⚠️  DRY RUN - No changes made. Run with dry_run=False to apply changes.")
        
        print(f"\nGameweeks affected: {sorted(gameweeks_to_fix)}")


def main():
    print("=" * 60)
    print("  Arab League GW13 Fix Script")
    print("  Using correct league scoring rules:")
    print("  - Captain: 2x only (no 3x triple captain)")
    print("  - Bench boost: ignored (only first 11 + auto-subs)")
    print("  - Transfer hits: subtracted")
    print("=" * 60)
    
    # Step 1: Get GW13 team points using correct rules
    team_points = get_gw13_team_points()
    
    if not team_points:
        print("\n❌ Could not calculate team points. Aborting.")
        return
    
    # Step 2: Get GW13 H2H matches
    matches = get_gw13_h2h_matches()
    
    if not matches:
        print("\n❌ Could not fetch H2H matches. Aborting.")
        return
    
    # Step 3: Calculate results
    results = calculate_gw13_results(team_points, matches)
    
    # Step 4: Calculate points to add
    points_to_add = calculate_gw13_points_to_add(results)
    
    # Step 5: Show GW13 standings
    gw13_standings = show_gw13_standings(points_to_add)
    
    # Step 6: DRY RUN first
    print("\n" + "=" * 60)
    print("  PHASE 1: DRY RUN (showing what would change)")
    print("=" * 60)
    fix_database(points_to_add, dry_run=True)
    
    # Ask for confirmation
    print("\n" + "=" * 60)
    response = input("\nDo you want to apply these changes? (yes/no): ")
    
    if response.lower() == 'yes':
        print("\n" + "=" * 60)
        print("  PHASE 2: APPLYING CHANGES")
        print("=" * 60)
        fix_database(points_to_add, dry_run=False)
    else:
        print("\n❌ Aborted. No changes made.")


if __name__ == '__main__':
    main()
