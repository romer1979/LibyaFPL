# -*- coding: utf-8 -*-
"""
Script to fix Libyan League GW24 missing standings.

Issue: GW24 standings were not saved to the database, causing GW25
to fall back to GW12 hardcoded standings (wrong).

This script will:
1. Check which GWs are saved in the database
2. Rebuild any missing GWs from the last saved GW through GW24
3. Save them to the database so GW25 calculates correctly

Run this from Render Shell:
    python fix_gw24_libyan.py
"""

import requests
import time
from app import app, db
from models import (
    TeamLeagueStandings, TeamLeagueMatches,
    save_team_league_standings, save_team_league_matches,
    get_team_league_standings_full
)

TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY = 2
LIBYAN_H2H_LEAGUE_ID = 1231867
LEAGUE_TYPE = 'libyan'
TARGET_GW = 24  # The GW we need to save

# Team definitions
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

# GW12 base standings (fallback)
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
GW12_FPL_TOTALS = {team: 0 for team in GW12_STANDINGS}


def fetch_json(url, retries=MAX_RETRIES):
    """Fetch JSON with retries"""
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
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
    return fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/")


def get_live_data(gameweek):
    return fetch_json(f"https://fantasy.premierleague.com/api/event/{gameweek}/live/")


def get_picks(entry_id, gameweek):
    return fetch_json(f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gameweek}/picks/")


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


def process_gameweek(gameweek, player_info, prev_league_standings, prev_fpl_totals):
    """Process a single gameweek: calculate points, matches, and new standings"""
    print(f"\n  Processing GW{gameweek}...")

    # Get live data
    live_data = get_live_data(gameweek)
    if not live_data:
        print(f"    Failed to get live data for GW{gameweek}")
        return None

    live_elements = build_live_elements(live_data)

    # Calculate team FPL points for this GW
    gw_team_points = {}
    for team_name, entry_ids in TEAMS_FPL_IDS.items():
        total = 0
        for entry_id in entry_ids:
            picks = get_picks(entry_id, gameweek)
            if picks:
                total += calculate_manager_points(picks, live_elements, player_info)
            time.sleep(0.1)  # Avoid rate limiting
        gw_team_points[team_name] = total
        print(f"    {team_name}: {total} pts")

    # Get H2H matches
    url = f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{LIBYAN_H2H_LEAGUE_ID}/?event={gameweek}"
    matches_data = fetch_json(url)
    if not matches_data or 'results' not in matches_data:
        print(f"    Failed to get H2H matches for GW{gameweek}")
        return None

    # Determine unique team matchups
    matches = []
    for match in matches_data['results']:
        entry_1 = match.get('entry_1_entry')
        entry_2 = match.get('entry_2_entry')

        team_1 = ENTRY_TO_TEAM.get(entry_1)
        team_2 = ENTRY_TO_TEAM.get(entry_2)

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

    # Calculate W/D/L and league points for this GW
    gw_league_points = {team: 0 for team in TEAMS_FPL_IDS.keys()}

    print(f"\n    GW{gameweek} Match Results:")
    for match in matches:
        t1, t2 = match['team1'], match['team2']
        p1, p2 = match['points1'], match['points2']

        if p1 > p2:
            gw_league_points[t1] = 3
            gw_league_points[t2] = 0
            result_str = f"{t1} WIN"
        elif p2 > p1:
            gw_league_points[t2] = 3
            gw_league_points[t1] = 0
            result_str = f"{t2} WIN"
        else:
            gw_league_points[t1] = 1
            gw_league_points[t2] = 1
            result_str = "DRAW"

        print(f"      {t1} ({p1}) vs {t2} ({p2}) => {result_str}")

    # Calculate cumulative standings
    new_league_standings = {}
    new_fpl_totals = {}

    for team in TEAMS_FPL_IDS.keys():
        new_league_standings[team] = prev_league_standings.get(team, 0) + gw_league_points.get(team, 0)
        new_fpl_totals[team] = prev_fpl_totals.get(team, 0) + gw_team_points.get(team, 0)

    return {
        'standings': new_league_standings,
        'fpl_totals': new_fpl_totals,
        'matches': matches,
        'gw_team_points': gw_team_points,
    }


def main():
    print("=" * 60)
    print("  Libyan League GW24 Fix Script")
    print("=" * 60)
    print("\nThis script will:")
    print("1. Check which GWs are saved in the database")
    print("2. Rebuild GW24 from GW23 base")
    print("3. Delete wrong GW25 and recalculate from correct GW24")
    print("\nCustom rules: Captain 2x, no bench boost, hits subtracted")
    print("=" * 60)

    # Step 1: Check database state
    print("\n--- Step 1: Checking database state ---\n")

    with app.app_context():
        # Find all saved GWs
        saved_gws = db.session.query(
            TeamLeagueStandings.gameweek
        ).filter_by(
            league_type=LEAGUE_TYPE
        ).distinct().order_by(TeamLeagueStandings.gameweek).all()

        saved_gw_list = [gw[0] for gw in saved_gws]
        print(f"Saved GWs in database: {saved_gw_list}")

        # Find ALL missing GWs (gaps) up to TARGET_GW
        max_gw = max(max(saved_gw_list) if saved_gw_list else 0, TARGET_GW)
        missing_gws = [gw for gw in range(1, max_gw + 1) if gw not in saved_gw_list]

        if TARGET_GW not in saved_gw_list:
            print(f"\nGW{TARGET_GW} is MISSING from the database!")
        else:
            print(f"\nGW{TARGET_GW} exists in the database.")

        if missing_gws:
            print(f"All missing GWs: {missing_gws}")

        # Check if GWs after TARGET_GW exist and are wrong (built from wrong base)
        wrong_gws = [gw for gw in saved_gw_list if gw > TARGET_GW]
        if wrong_gws:
            print(f"\nGWs after {TARGET_GW} that need recalculation: {wrong_gws}")
            print("(These were calculated from wrong base and will be deleted + rebuilt)")

        # Determine which GWs we need to process: missing GW24 + wrong GWs after it
        gws_to_process = sorted(set(
            [gw for gw in missing_gws if gw >= TARGET_GW] + wrong_gws
        ))

        if not gws_to_process:
            print(f"\nNothing to fix! GW{TARGET_GW} and all subsequent GWs look correct.")
            return

        print(f"\nGWs to process: {gws_to_process}")

        # Find the base GW (last correctly saved GW before the ones we need to fix)
        base_gw = TARGET_GW - 1
        while base_gw > 0 and base_gw not in saved_gw_list:
            base_gw -= 1

        if base_gw > 0 and base_gw in saved_gw_list:
            base_data = get_team_league_standings_full(LEAGUE_TYPE, base_gw)
            base_standings = {k: v['league_points'] for k, v in base_data.items()}
            base_fpl_totals = {k: v['total_fpl_points'] for k, v in base_data.items()}
            print(f"\nUsing GW{base_gw} from database as base:")
        else:
            base_standings = GW12_STANDINGS.copy()
            base_fpl_totals = GW12_FPL_TOTALS.copy()
            base_gw = 12
            print(f"\nUsing GW12 hardcoded standings as base:")

        sorted_base = sorted(base_standings.items(), key=lambda x: -x[1])
        for i, (team, pts) in enumerate(sorted_base[:5], 1):
            fpl = base_fpl_totals.get(team, 0)
            print(f"  {i}. {team}: {pts} league pts, {fpl} FPL pts")
        print("  ...")

    # Step 2: Fetch data from FPL API
    print("\n--- Step 2: Fetching FPL API data ---\n")

    bootstrap = get_bootstrap_data()
    if not bootstrap:
        print("Failed to fetch bootstrap data. Aborting.")
        return

    player_info = build_player_info(bootstrap)
    print(f"Loaded {len(player_info)} players")

    # Step 3: Process each GW that needs fixing
    print("\n--- Step 3: Processing GWs ---")

    current_standings = base_standings.copy()
    current_fpl_totals = base_fpl_totals.copy()
    all_gw_results = {}

    for gw in gws_to_process:
        result = process_gameweek(gw, player_info, current_standings, current_fpl_totals)

        if result is None:
            print(f"\nFailed to process GW{gw}. Aborting.")
            return

        all_gw_results[gw] = result
        current_standings = result['standings']
        current_fpl_totals = result['fpl_totals']

        # Show standings after this GW
        print(f"\n    Standings after GW{gw}:")
        sorted_teams = sorted(current_standings.items(), key=lambda x: (-x[1], -current_fpl_totals.get(x[0], 0)))
        for i, (team, pts) in enumerate(sorted_teams, 1):
            fpl = current_fpl_totals.get(team, 0)
            print(f"      {i:2}. {team}: {pts} league pts, {fpl} FPL pts")

    # Step 4: Confirm and save
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)

    for gw in gws_to_process:
        action = "CREATE" if gw not in saved_gw_list else "REPLACE (delete wrong + save correct)"
        print(f"\n  GW{gw}: {action}")

    print(f"\nFinal standings after GW{gws_to_process[-1]}:")
    last_gw = gws_to_process[-1]
    final_standings = all_gw_results[last_gw]['standings']
    final_fpl = all_gw_results[last_gw]['fpl_totals']
    sorted_final = sorted(final_standings.items(), key=lambda x: (-x[1], -final_fpl.get(x[0], 0)))

    for i, (team, pts) in enumerate(sorted_final, 1):
        fpl = final_fpl.get(team, 0)
        print(f"  {i:2}. {team}: {pts} league pts, {fpl} FPL pts")

    print("\n" + "=" * 60)
    response = input("\nDo you want to save these to the database? (yes/no): ")

    if response.lower() != 'yes':
        print("\nAborted. No changes made.")
        return

    # Step 5: Save to database
    print("\n--- Saving to database ---\n")

    with app.app_context():
        # First delete wrong GWs that will be replaced
        for gw in gws_to_process:
            if gw in saved_gw_list:
                deleted = TeamLeagueStandings.query.filter_by(
                    league_type=LEAGUE_TYPE, gameweek=gw
                ).delete()
                deleted_matches = TeamLeagueMatches.query.filter_by(
                    league_type=LEAGUE_TYPE, gameweek=gw
                ).delete()
                db.session.commit()
                print(f"  GW{gw}: Deleted {deleted} wrong standings + {deleted_matches} matches")

        # Now save correct data
        for gw in gws_to_process:
            gw_data = all_gw_results[gw]

            # Save standings
            save_team_league_standings(
                LEAGUE_TYPE, gw,
                gw_data['standings'],
                gw_data['fpl_totals']
            )
            print(f"  GW{gw}: Saved standings for {len(gw_data['standings'])} teams")

            # Save matches
            save_team_league_matches(LEAGUE_TYPE, gw, gw_data['matches'])
            print(f"  GW{gw}: Saved {len(gw_data['matches'])} matches")

        print(f"\nAll done! Processed GWs: {gws_to_process}")

    # Step 6: Verify
    print("\n--- Verification ---\n")

    with app.app_context():
        saved_gws_after = db.session.query(
            TeamLeagueStandings.gameweek
        ).filter_by(
            league_type=LEAGUE_TYPE
        ).distinct().order_by(TeamLeagueStandings.gameweek).all()

        saved_gw_list_after = [gw[0] for gw in saved_gws_after]
        print(f"Saved GWs now: {saved_gw_list_after}")

        # Verify each processed GW
        for gw in gws_to_process:
            gw_data = get_team_league_standings_full(LEAGUE_TYPE, gw)
            if gw_data:
                sorted_teams = sorted(gw_data.items(), key=lambda x: (-x[1]['league_points'], -x[1]['total_fpl_points']))
                top = sorted_teams[0]
                print(f"  GW{gw}: OK - Leader: {top[0]} ({top[1]['league_points']} pts)")
            else:
                print(f"  GW{gw}: WARNING - not found in database!")

        print(f"\nNext GW will correctly use GW{gws_to_process[-1]} as its base.")


if __name__ == '__main__':
    main()
