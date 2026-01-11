# -*- coding: utf-8 -*-
"""
Script to fix Libyan League standings.

Issues to fix:
1. GW13 is missing - need to add GW13 points to GW14-19
2. GW20 is missing - need to reconstruct and save
3. GW21 is wrong (calculated from GW12 instead of GW19) - need to delete and recalculate

Run this from Render Shell:
    python fix_libyan_league.py
"""

import requests
from app import app, db
from models import TeamLeagueStandings

TIMEOUT = 15
LIBYAN_H2H_LEAGUE_ID = 1231867  # Libyan League H2H ID

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

# Reverse lookup
ENTRY_TO_TEAM = {}
for team_name, ids in TEAMS_FPL_IDS.items():
    for entry_id in ids:
        ENTRY_TO_TEAM[entry_id] = team_name

# GW12 base standings (hardcoded)
GW12_STANDINGS = {
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
    """Calculate auto-sub points following league rules."""
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
            
            if (s_pos == 1 and b_pos != 1) or (s_pos != 1 and b_pos == 1):
                continue
            
            if b_min == 0:
                continue
            
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
            
            sub_points += live_elements.get(b_id, {}).get('total_points', 0)
            used_bench_ids.add(b_id)
            d, m, f, g = d2, m2, f2, g2
            break
    
    return sub_points


def calculate_manager_points(picks_data, live_elements, player_info):
    """Calculate points for a manager following league rules."""
    if not picks_data:
        return 0
    
    picks = picks_data.get('picks', [])
    hits = picks_data.get('entry_history', {}).get('event_transfers_cost', 0)
    
    if not picks:
        return 0
    
    captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
    captain_minutes = live_elements.get(captain_id, {}).get('minutes', 0) if captain_id else 0
    captain_played = captain_minutes > 0
    
    total_points = 0
    for pick in picks[:11]:
        pid = pick['element']
        pts = live_elements.get(pid, {}).get('total_points', 0)
        
        if pick.get('is_captain'):
            if captain_played:
                pts *= 2
            else:
                pts = 0
        elif pick.get('is_vice_captain'):
            if not captain_played:
                vc_minutes = live_elements.get(pid, {}).get('minutes', 0)
                if vc_minutes > 0:
                    pts *= 2
        
        total_points += pts
    
    sub_points = calculate_auto_subs(picks, live_elements, player_info)
    
    return total_points + sub_points - hits


def get_gw_team_points(gameweek, bootstrap, player_info):
    """Calculate total points for each team in a gameweek."""
    print(f"\n=== Fetching GW{gameweek} data ===\n")
    
    live_data = get_live_data(gameweek)
    if not live_data:
        print(f"Failed to fetch live data for GW{gameweek}!")
        return None
    
    live_elements = build_live_elements(live_data)
    
    team_points = {team: 0 for team in TEAMS_FPL_IDS.keys()}
    
    for team_name, entry_ids in TEAMS_FPL_IDS.items():
        total = 0
        
        for entry_id in entry_ids:
            picks_data = get_picks(entry_id, gameweek)
            
            if picks_data:
                points = calculate_manager_points(picks_data, live_elements, player_info)
                total += points
            else:
                print(f"  {team_name} - Entry {entry_id}: Failed to fetch picks!")
        
        team_points[team_name] = total
        print(f"{team_name}: {total} pts")
    
    return team_points


def get_gw_h2h_matches(gameweek):
    """Fetch H2H matches for a gameweek."""
    print(f"\n=== Fetching GW{gameweek} H2H Matches ===\n")
    
    url = f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{LIBYAN_H2H_LEAGUE_ID}/?event={gameweek}"
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


def calculate_gw_results(team_points, matches, gameweek):
    """Calculate W/D/L for each team based on points."""
    print(f"\n=== GW{gameweek} Match Results ===\n")
    
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


def calculate_points_to_add(results):
    """Calculate league points from results."""
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


def main():
    print("=" * 60)
    print("  Libyan League Comprehensive Fix Script")
    print("=" * 60)
    print("\nThis script will:")
    print("1. Reconstruct GW13 results and add points to GW14-19")
    print("2. Reconstruct GW20 results and save GW20")
    print("3. Delete wrong GW21 and recalculate from GW20")
    print("\nUsing correct league scoring rules:")
    print("- Captain: 2x only (no 3x triple captain)")
    print("- Bench boost: ignored (only first 11 + auto-subs)")
    print("- Transfer hits: subtracted")
    print("=" * 60)
    
    # Get bootstrap data once
    print("\nFetching bootstrap data...")
    bootstrap = get_bootstrap_data()
    if not bootstrap:
        print("❌ Failed to fetch bootstrap data. Aborting.")
        return
    
    player_info = build_player_info(bootstrap)
    print(f"Loaded {len(player_info)} players")
    
    # ========== STEP 1: Reconstruct GW13 ==========
    print("\n" + "=" * 60)
    print("  STEP 1: Reconstruct GW13")
    print("=" * 60)
    
    gw13_team_points = get_gw_team_points(13, bootstrap, player_info)
    if not gw13_team_points:
        print("❌ Failed to get GW13 team points. Aborting.")
        return
    
    gw13_matches = get_gw_h2h_matches(13)
    if not gw13_matches:
        print("❌ Failed to get GW13 matches. Aborting.")
        return
    
    gw13_results = calculate_gw_results(gw13_team_points, gw13_matches, 13)
    gw13_points_to_add = calculate_points_to_add(gw13_results)
    
    print("\n=== GW13 League Points to Add ===")
    for team, pts in sorted(gw13_points_to_add.items(), key=lambda x: -x[1]):
        result = 'W' if pts == 3 else ('D' if pts == 1 else 'L')
        print(f"  {team}: +{pts} ({result})")
    
    # ========== STEP 2: Reconstruct GW20 ==========
    print("\n" + "=" * 60)
    print("  STEP 2: Reconstruct GW20")
    print("=" * 60)
    
    gw20_team_points = get_gw_team_points(20, bootstrap, player_info)
    if not gw20_team_points:
        print("❌ Failed to get GW20 team points. Aborting.")
        return
    
    gw20_matches = get_gw_h2h_matches(20)
    if not gw20_matches:
        print("❌ Failed to get GW20 matches. Aborting.")
        return
    
    gw20_results = calculate_gw_results(gw20_team_points, gw20_matches, 20)
    gw20_points_to_add = calculate_points_to_add(gw20_results)
    
    print("\n=== GW20 League Points to Add ===")
    for team, pts in sorted(gw20_points_to_add.items(), key=lambda x: -x[1]):
        result = 'W' if pts == 3 else ('D' if pts == 1 else 'L')
        print(f"  {team}: +{pts} ({result})")
    
    # ========== STEP 3: Reconstruct GW21 ==========
    print("\n" + "=" * 60)
    print("  STEP 3: Reconstruct GW21")
    print("=" * 60)
    
    gw21_team_points = get_gw_team_points(21, bootstrap, player_info)
    if not gw21_team_points:
        print("❌ Failed to get GW21 team points. Aborting.")
        return
    
    gw21_matches = get_gw_h2h_matches(21)
    if not gw21_matches:
        print("❌ Failed to get GW21 matches. Aborting.")
        return
    
    gw21_results = calculate_gw_results(gw21_team_points, gw21_matches, 21)
    gw21_points_to_add = calculate_points_to_add(gw21_results)
    
    print("\n=== GW21 League Points to Add ===")
    for team, pts in sorted(gw21_points_to_add.items(), key=lambda x: -x[1]):
        result = 'W' if pts == 3 else ('D' if pts == 1 else 'L')
        print(f"  {team}: +{pts} ({result})")
    
    # ========== STEP 4: Calculate Correct Standings ==========
    print("\n" + "=" * 60)
    print("  STEP 4: Calculate Correct Standings")
    print("=" * 60)
    
    # GW13 = GW12 + GW13 results
    gw13_standings = {}
    for team, base in GW12_STANDINGS.items():
        gw13_standings[team] = base + gw13_points_to_add.get(team, 0)
    
    print("\n=== Correct GW13 Standings ===")
    for i, (team, pts) in enumerate(sorted(gw13_standings.items(), key=lambda x: -x[1]), 1):
        print(f"{i:2}. {team}: {pts}")
    
    # ========== STEP 5: DRY RUN ==========
    print("\n" + "=" * 60)
    print("  STEP 5: DRY RUN - Preview Changes")
    print("=" * 60)
    
    with app.app_context():
        # Show what will happen to GW14-19
        print("\n--- GW14-19: Add GW13 points ---")
        for gw in range(14, 20):
            standings = TeamLeagueStandings.query.filter_by(
                league_type='libyan', gameweek=gw
            ).all()
            
            if standings:
                print(f"\nGW{gw}:")
                for s in sorted(standings, key=lambda x: -x.league_points)[:5]:
                    old = s.league_points
                    new = old + gw13_points_to_add.get(s.team_name, 0)
                    print(f"  {s.team_name}: {old} -> {new} (+{gw13_points_to_add.get(s.team_name, 0)})")
                print("  ...")
        
        # Show GW20 will be created
        print("\n--- GW20: Will be CREATED ---")
        # GW20 = GW19 (corrected) + GW20 results
        # First get corrected GW19
        gw19_standings = TeamLeagueStandings.query.filter_by(
            league_type='libyan', gameweek=19
        ).all()
        
        gw20_new_standings = {}
        for s in gw19_standings:
            corrected_gw19 = s.league_points + gw13_points_to_add.get(s.team_name, 0)
            gw20_new_standings[s.team_name] = corrected_gw19 + gw20_points_to_add.get(s.team_name, 0)
        
        for i, (team, pts) in enumerate(sorted(gw20_new_standings.items(), key=lambda x: -x[1]), 1):
            if i <= 5:
                print(f"  {i}. {team}: {pts}")
        print("  ...")
        
        # Show GW21 will be replaced
        print("\n--- GW21: Will be DELETED and RECREATED ---")
        gw21_new_standings = {}
        for team, gw20_pts in gw20_new_standings.items():
            gw21_new_standings[team] = gw20_pts + gw21_points_to_add.get(team, 0)
        
        print("Old GW21 (wrong):")
        old_gw21 = TeamLeagueStandings.query.filter_by(
            league_type='libyan', gameweek=21
        ).order_by(TeamLeagueStandings.league_points.desc()).limit(5).all()
        for s in old_gw21:
            print(f"  {s.team_name}: {s.league_points}")
        print("  ...")
        
        print("\nNew GW21 (correct):")
        for i, (team, pts) in enumerate(sorted(gw21_new_standings.items(), key=lambda x: -x[1]), 1):
            if i <= 5:
                print(f"  {i}. {team}: {pts}")
        print("  ...")
        
        # Also save GW13
        print("\n--- GW13: Will be CREATED ---")
        for i, (team, pts) in enumerate(sorted(gw13_standings.items(), key=lambda x: -x[1]), 1):
            if i <= 5:
                print(f"  {i}. {team}: {pts}")
        print("  ...")
    
    # ========== STEP 6: Confirm and Apply ==========
    print("\n" + "=" * 60)
    response = input("\nDo you want to apply these changes? (yes/no): ")
    
    if response.lower() != 'yes':
        print("\n❌ Aborted. No changes made.")
        return
    
    print("\n" + "=" * 60)
    print("  APPLYING CHANGES")
    print("=" * 60)
    
    with app.app_context():
        # 1. Add GW13 points to GW14-19
        print("\n1. Adding GW13 points to GW14-19...")
        for gw in range(14, 20):
            standings = TeamLeagueStandings.query.filter_by(
                league_type='libyan', gameweek=gw
            ).all()
            
            for s in standings:
                s.league_points += gw13_points_to_add.get(s.team_name, 0)
            
            print(f"   GW{gw}: Updated {len(standings)} teams")
        
        # 2. Save GW13
        print("\n2. Saving GW13 standings...")
        for team, pts in gw13_standings.items():
            existing = TeamLeagueStandings.query.filter_by(
                league_type='libyan', gameweek=13, team_name=team
            ).first()
            
            if existing:
                existing.league_points = pts
            else:
                new_standing = TeamLeagueStandings(
                    league_type='libyan',
                    gameweek=13,
                    team_name=team,
                    league_points=pts
                )
                db.session.add(new_standing)
        print(f"   GW13: Saved {len(gw13_standings)} teams")
        
        # 3. Create GW20
        print("\n3. Creating GW20 standings...")
        for team, pts in gw20_new_standings.items():
            existing = TeamLeagueStandings.query.filter_by(
                league_type='libyan', gameweek=20, team_name=team
            ).first()
            
            if existing:
                existing.league_points = pts
            else:
                new_standing = TeamLeagueStandings(
                    league_type='libyan',
                    gameweek=20,
                    team_name=team,
                    league_points=pts
                )
                db.session.add(new_standing)
        print(f"   GW20: Saved {len(gw20_new_standings)} teams")
        
        # 4. Delete and recreate GW21
        print("\n4. Replacing GW21 standings...")
        TeamLeagueStandings.query.filter_by(
            league_type='libyan', gameweek=21
        ).delete()
        
        for team, pts in gw21_new_standings.items():
            new_standing = TeamLeagueStandings(
                league_type='libyan',
                gameweek=21,
                team_name=team,
                league_points=pts
            )
            db.session.add(new_standing)
        print(f"   GW21: Replaced with {len(gw21_new_standings)} teams")
        
        # Commit all changes
        db.session.commit()
        print("\n✅ All changes committed successfully!")
    
    # Final verification
    print("\n" + "=" * 60)
    print("  VERIFICATION")
    print("=" * 60)
    
    with app.app_context():
        print("\n=== Final GW21 Standings ===")
        final = TeamLeagueStandings.query.filter_by(
            league_type='libyan', gameweek=21
        ).order_by(TeamLeagueStandings.league_points.desc()).all()
        
        for i, s in enumerate(final, 1):
            print(f"{i:2}. {s.team_name}: {s.league_points}")


if __name__ == '__main__':
    main()
