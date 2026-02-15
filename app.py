# -*- coding: utf-8 -*-
"""
Fantasy Premier League Multi-League App
"""

from flask import Flask, render_template, jsonify, request
import os
import sys
import requests as http_requests
from datetime import datetime

# Load environment variables from .env file (for local development)
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import LEAGUE_ID, ARABIC
from core.dashboard import get_dashboard
from core.stats import get_league_stats, get_manager_history
from core.the100 import get_the100_standings, get_the100_stats
from core.cities_league import get_cities_league_data
from core.libyan_league import get_libyan_league_data
from core.arab_league import get_arab_league_data
from models import db, save_standings, calculate_rank_change, StandingsHistory, FixtureResult

app = Flask(__name__)

# Database configuration
database_url = os.environ.get('DATABASE_URL', 'sqlite:///elite_league.db')
# Fix for Render PostgreSQL URL (postgres:// -> postgresql://)
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'elite-league-secret-key-2024')

# Initialize database
db.init_app(app)

# Create tables on first request
with app.app_context():
    db.create_all()


@app.route('/')
def home():
    """Home page showing all leagues - simple links only"""
    return render_template('home.html')


# Guard to prevent concurrent elite backfill
_elite_backfill_in_progress = False


def backfill_elite_standings(current_gw):
    """
    Backfill missing elite league standings and fixture results for previous GWs.
    Fetches data from the FPL API for any GW not yet saved in the database.
    """
    global _elite_backfill_in_progress
    if _elite_backfill_in_progress:
        return
    _elite_backfill_in_progress = True

    try:
        from core.fpl_api import (
            get_bootstrap_data, get_league_standings, get_league_matches,
            get_multiple_entry_data, get_multiple_entry_picks, build_player_info
        )
        from config import LEAGUE_ID, EXCLUDED_PLAYERS, get_chip_arabic

        # Find which GWs have standings saved
        saved_standings_gws = db.session.query(
            StandingsHistory.gameweek
        ).distinct().all()
        saved_standings_set = {gw[0] for gw in saved_standings_gws}

        # Find which GWs have fixture results saved
        saved_fixtures_gws = db.session.query(
            FixtureResult.gameweek
        ).distinct().all()
        saved_fixtures_set = {gw[0] for gw in saved_fixtures_gws}

        # Determine which finished GWs need work
        bootstrap = get_bootstrap_data()
        events = bootstrap.get('events', [])
        finished_gws = [e['id'] for e in events if e.get('finished') and e.get('data_checked')]

        # GWs missing standings entirely
        missing_standings = sorted([gw for gw in finished_gws if gw not in saved_standings_set and gw < current_gw])
        # GWs that have standings but missing fixtures
        missing_fixtures_only = sorted([gw for gw in finished_gws if gw in saved_standings_set and gw not in saved_fixtures_set and gw < current_gw])

        # GWs that have standings but result/opponent are missing (saved by old code)
        standings_missing_results = []
        for gw in finished_gws:
            if gw in saved_standings_set and gw not in missing_fixtures_only and gw < current_gw:
                has_blank = StandingsHistory.query.filter_by(gameweek=gw).filter(
                    db.or_(StandingsHistory.result.is_(None), StandingsHistory.result == '-', StandingsHistory.result == '')
                ).first()
                if has_blank:
                    standings_missing_results.append(gw)

        # Detect GWs where all league_points are 0 (backfilled with no LP)
        zero_lp_gws = []
        for gw in finished_gws:
            if gw in saved_standings_set and gw not in missing_standings and gw < current_gw:
                has_nonzero = StandingsHistory.query.filter_by(gameweek=gw).filter(
                    StandingsHistory.league_points > 0
                ).first()
                if not has_nonzero:
                    zero_lp_gws.append(gw)

        missing_gws = missing_standings
        all_gws_to_process = sorted(set(missing_standings + missing_fixtures_only + standings_missing_results))

        if not all_gws_to_process and not zero_lp_gws:
            return

        if missing_standings:
            print(f"[elite] Backfilling missing standings+fixtures for GWs: {missing_standings}")
        if missing_fixtures_only:
            print(f"[elite] Backfilling missing fixtures only for GWs: {missing_fixtures_only}")
        if standings_missing_results:
            print(f"[elite] Fixing missing result/opponent in standings for GWs: {standings_missing_results}")
        if zero_lp_gws:
            print(f"[elite] Fixing zero league_points for GWs: {zero_lp_gws}")

        print(f"[elite] Backfilling missing GWs: {missing_gws}")

        # Get league standings (current cumulative data)
        league_data = get_league_standings(LEAGUE_ID)
        teams_league = league_data['standings']['results']

        # Build entry info map
        entry_info = {}
        entry_ids = []
        for entry in teams_league:
            name = entry.get('player_name')
            if name in EXCLUDED_PLAYERS:
                continue
            eid = entry.get('entry')
            entry_ids.append(eid)
            entry_info[eid] = {
                'player_name': name,
                'entry_name': entry.get('entry_name', ''),
                'total_h2h': int(entry.get('total', 0) or 0),
                'points_for': entry.get('points_for', 0),
            }

        # Get overall ranks
        all_entry_data = get_multiple_entry_data(entry_ids)
        player_info_map = build_player_info(bootstrap)
        elements = bootstrap.get('elements', [])

        for gw in all_gws_to_process:
            needs_standings = gw in missing_standings
            needs_fixtures = gw in missing_fixtures_only or gw in standings_missing_results or needs_standings
            needs_result_fix = gw in standings_missing_results or gw in missing_fixtures_only
            label = "standings+fixtures" if needs_standings else ("fixtures+results" if needs_result_fix else "fixtures only")
            print(f"[elite] Backfilling GW{gw} ({label})...")

            try:
                # Fetch H2H matches for this GW (needed for both cases)
                matches_data = get_league_matches(LEAGUE_ID, gw)
                matches = matches_data.get('results', [])

                # Save standings if needed
                if needs_standings:
                    all_picks = get_multiple_entry_picks(entry_ids, gw)

                    standings_data = []
                    for eid in entry_ids:
                        info = entry_info.get(eid, {})
                        picks_data = all_picks.get(eid, {})
                        e_data = all_entry_data.get(eid, {})

                        gw_points = picks_data.get('entry_history', {}).get('points', 0) if picks_data else 0
                        overall_rank = e_data.get('summary_overall_rank')

                        captain = None
                        if picks_data:
                            captain_id = next((p['element'] for p in picks_data.get('picks', []) if p.get('is_captain')), None)
                            if captain_id:
                                capt = next((pl for pl in elements if pl.get('id') == captain_id), None)
                                captain = capt.get('web_name') if capt else None

                        chip_raw = picks_data.get('active_chip') if picks_data else None
                        chip = get_chip_arabic(chip_raw)

                        result = '-'
                        opponent = '-'
                        for match in matches:
                            if match.get('entry_1_entry') == eid:
                                opp_id = match.get('entry_2_entry')
                                opp_info = entry_info.get(opp_id, {})
                                opponent = opp_info.get('player_name', '-')
                                p1 = match.get('entry_1_points', 0)
                                p2 = match.get('entry_2_points', 0)
                                result = 'W' if p1 > p2 else ('L' if p2 > p1 else 'D')
                                break
                            elif match.get('entry_2_entry') == eid:
                                opp_id = match.get('entry_1_entry')
                                opp_info = entry_info.get(opp_id, {})
                                opponent = opp_info.get('player_name', '-')
                                p1 = match.get('entry_1_points', 0)
                                p2 = match.get('entry_2_points', 0)
                                result = 'W' if p2 > p1 else ('L' if p1 > p2 else 'D')
                                break

                        standings_data.append({
                            'entry_id': eid,
                            'player_name': info.get('player_name', ''),
                            'team_name': info.get('entry_name', ''),
                            'projected_league_points': 0,
                            'current_gw_points': gw_points,
                            'total_points': info.get('points_for', 0),
                            'overall_rank': overall_rank,
                            'result': result,
                            'opponent': opponent,
                            'captain': captain or '-',
                            'chip': chip,
                        })

                    standings_data.sort(key=lambda x: (
                        -(3 if x['result'] == 'W' else (1 if x['result'] == 'D' else 0)),
                        -x['current_gw_points']
                    ))
                    for i, team in enumerate(standings_data, 1):
                        team['rank'] = i
                        team['projected_league_points'] = 0

                    save_standings(gw, standings_data)

                # Save fixture results if needed
                if needs_fixtures:
                    fixture_count = 0
                    for match in matches:
                        entry_1 = match.get('entry_1_entry')
                        entry_2 = match.get('entry_2_entry')
                        name_1 = entry_info.get(entry_1, {}).get('player_name', '')
                        name_2 = entry_info.get(entry_2, {}).get('player_name', '')

                        if not name_1 or not name_2:
                            continue

                        p1 = match.get('entry_1_points', 0)
                        p2 = match.get('entry_2_points', 0)
                        winner = 1 if p1 > p2 else (2 if p2 > p1 else 0)

                        existing = FixtureResult.query.filter_by(
                            gameweek=gw, entry_1_id=entry_1, entry_2_id=entry_2
                        ).first()

                        if not existing:
                            fixture = FixtureResult(
                                gameweek=gw,
                                entry_1_id=entry_1,
                                entry_1_name=name_1,
                                entry_1_points=p1,
                                entry_2_id=entry_2,
                                entry_2_name=name_2,
                                entry_2_points=p2,
                                winner=winner
                            )
                            db.session.add(fixture)
                            fixture_count += 1

                    db.session.commit()
                    print(f"[elite] Backfilled GW{gw}: {fixture_count} fixtures saved")

                # Update existing standings with result/opponent from H2H matches
                if needs_result_fix and not needs_standings:
                    updated_count = 0
                    for match in matches:
                        entry_1 = match.get('entry_1_entry')
                        entry_2 = match.get('entry_2_entry')
                        name_1 = entry_info.get(entry_1, {}).get('player_name', '-')
                        name_2 = entry_info.get(entry_2, {}).get('player_name', '-')
                        p1 = match.get('entry_1_points', 0)
                        p2 = match.get('entry_2_points', 0)

                        if p1 > p2:
                            r1, r2 = 'W', 'L'
                        elif p2 > p1:
                            r1, r2 = 'L', 'W'
                        else:
                            r1, r2 = 'D', 'D'

                        # Update entry 1
                        s1 = StandingsHistory.query.filter_by(gameweek=gw, entry_id=entry_1).first()
                        if s1:
                            s1.result = r1
                            s1.opponent = name_2
                            updated_count += 1

                        # Update entry 2
                        s2 = StandingsHistory.query.filter_by(gameweek=gw, entry_id=entry_2).first()
                        if s2:
                            s2.result = r2
                            s2.opponent = name_1
                            updated_count += 1

                    db.session.commit()
                    print(f"[elite] Fixed result/opponent for GW{gw}: {updated_count} standings updated")

            except Exception as e:
                print(f"[elite] Error backfilling GW{gw}: {e}")
                db.session.rollback()
                continue

        # Fix league points for GWs that have 0 (backfilled or newly created)
        gws_needing_lp = sorted(set(missing_standings + zero_lp_gws))
        if gws_needing_lp:
            print(f"[elite] Calculating cumulative league points for GWs: {gws_needing_lp}")
            cumulative_lp = {eid: 0 for eid in entry_ids}
            max_gw_needed = max(gws_needing_lp)

            for gw in sorted(finished_gws):
                if gw > max_gw_needed:
                    break
                try:
                    gw_matches = get_league_matches(LEAGUE_ID, gw)
                    for match in gw_matches.get('results', []):
                        e1 = match.get('entry_1_entry')
                        e2 = match.get('entry_2_entry')
                        p1 = match.get('entry_1_points', 0)
                        p2 = match.get('entry_2_points', 0)
                        if e1 in cumulative_lp:
                            cumulative_lp[e1] += 3 if p1 > p2 else (1 if p1 == p2 else 0)
                        if e2 in cumulative_lp:
                            cumulative_lp[e2] += 3 if p2 > p1 else (1 if p1 == p2 else 0)
                except Exception as e:
                    print(f"[elite] Error fetching GW{gw} matches for LP calc: {e}")
                    continue

                # Update DB records for this GW if it needs LP fix
                if gw in gws_needing_lp:
                    try:
                        for eid in entry_ids:
                            s = StandingsHistory.query.filter_by(gameweek=gw, entry_id=eid).first()
                            if s:
                                s.league_points = cumulative_lp.get(eid, 0)
                        db.session.commit()
                        print(f"[elite] Updated league points for GW{gw}: {dict(list(cumulative_lp.items())[:3])}...")
                    except Exception as e:
                        print(f"[elite] Error updating LP for GW{gw}: {e}")
                        db.session.rollback()

    except Exception as e:
        print(f"[elite] Backfill error: {e}")
    finally:
        _elite_backfill_in_progress = False


@app.route('/league/elite')
def elite_dashboard():
    """Elite League dashboard page"""
    data = get_dashboard()

    # Calculate rank changes from database
    if data.get('success') and data.get('standings'):
        gameweek = data.get('gameweek', 1)

        # Backfill any missing previous GW standings and fixture results
        try:
            backfill_elite_standings(gameweek)
        except Exception as e:
            print(f"[elite] Backfill failed: {e}")

        for team in data['standings']:
            entry_id = team.get('entry_id')
            current_rank = team.get('rank', 0)

            # Get rank change from previous gameweek
            rank_change = calculate_rank_change(gameweek, entry_id, current_rank)
            team['rank_change'] = rank_change

        # Save current standings to database (if gameweek is finished or live)
        if data.get('gw_finished') or data.get('is_live'):
            save_standings(gameweek, data['standings'])

            # Also save fixture results for current GW
            if data.get('fixtures'):
                for fix in data['fixtures']:
                    entry_1 = fix.get('entry_1')
                    entry_2 = fix.get('entry_2')
                    if not entry_1 or not entry_2:
                        continue
                    p1 = fix.get('team_1_points', 0)
                    p2 = fix.get('team_2_points', 0)
                    winner = fix.get('winner', 0)

                    existing = FixtureResult.query.filter_by(
                        gameweek=gameweek, entry_1_id=entry_1, entry_2_id=entry_2
                    ).first()

                    if existing:
                        existing.entry_1_points = p1
                        existing.entry_2_points = p2
                        existing.winner = winner
                    else:
                        fixture = FixtureResult(
                            gameweek=gameweek,
                            entry_1_id=entry_1,
                            entry_1_name=fix.get('team_1_name', ''),
                            entry_1_points=p1,
                            entry_2_id=entry_2,
                            entry_2_name=fix.get('team_2_name', ''),
                            entry_2_points=p2,
                            winner=winner
                        )
                        db.session.add(fixture)

                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"Error saving elite fixtures: {e}")

    return render_template('dashboard.html', data=data, ar=ARABIC)


@app.route('/league/elite/history')
def elite_history():
    """Elite League history page"""
    from core.elite_history import get_elite_history_data
    data = get_elite_history_data()
    if not data:
        return "Error loading history", 500
    return render_template('elite_history.html', **data)


@app.route('/league/elite/stats')
def elite_stats():
    """Elite League statistics page"""
    data = get_league_stats()
    return render_template('stats.html', data=data, ar=ARABIC)


@app.route('/league/the100')
def the100_dashboard():
    """The 100 League dashboard"""
    data = get_the100_standings()
    return render_template('the100_dashboard.html', data=data)


@app.route('/league/the100/stats')
def the100_stats():
    """The 100 League statistics page"""
    data = get_the100_stats()
    return render_template('the100_stats.html', data=data)


@app.route('/league/cities')
def cities_dashboard():
    """Cities League dashboard - Team H2H"""
    data = get_cities_league_data()
    return render_template('cities_dashboard.html', data=data)


@app.route('/league/libyan')
def libyan_dashboard():
    """Libyan League dashboard - Team H2H"""
    data = get_libyan_league_data()
    return render_template('libyan_dashboard.html', data=data)


@app.route('/league/arab')
def arab_dashboard():
    """Arab Championship dashboard - Team H2H"""
    data = get_arab_league_data()
    return render_template('arab_dashboard.html', data=data)


@app.route('/league/cities/history')
def cities_history():
    """Cities League history page"""
    from core.team_league_history import get_league_history_data
    data = get_league_history_data('cities')
    if not data:
        return "Error loading history", 500
    return render_template('team_league_history.html', **data)


@app.route('/league/libyan/history')
def libyan_history():
    """Libyan League history page"""
    from core.team_league_history import get_league_history_data
    data = get_league_history_data('libyan')
    if not data:
        return "Error loading history", 500
    return render_template('team_league_history.html', **data)


@app.route('/league/arab/history')
def arab_history():
    """Arab League history page"""
    from core.team_league_history import get_league_history_data
    data = get_league_history_data('arab')
    if not data:
        return "Error loading history", 500
    return render_template('team_league_history.html', **data)


@app.route('/api/comparison')
def comparison_data():
    """API endpoint for manager comparison data"""
    data = get_manager_history()
    return jsonify(data)


@app.route('/api/dashboard')
def api_dashboard():
    """API endpoint for AJAX updates"""
    data = get_dashboard()
    data['timestamp'] = datetime.now().strftime('%H:%M:%S')
    return jsonify(data)


@app.errorhandler(404)
def page_not_found(e):
    return render_template('home.html', elite_standings=[], error='Page not found'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('home.html', elite_standings=[], error='Server error'), 500


@app.route('/admin/init-gw13')
def init_gw13_standings():
    """Initialize GW13 standings for team leagues - run once after deployment"""
    from models import TeamLeagueStandings, save_team_league_standings
    import requests
    
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
        except:
            return None
    
    # Check if already initialized
    existing = TeamLeagueStandings.query.filter_by(gameweek=13).first()
    if existing:
        return jsonify({'status': 'already_exists', 'message': 'GW13 standings already exist'})
    
    cookies = get_cookies()
    results = {}
    
    # League configurations
    LEAGUES = {
        'cities': {
            'id': 1011575,
            'initial': {
                "جالو": 33, "طرميسة": 24, "غريان": 24, "اوجلة": 21, "حي 9 يونيو": 19,
                "ترهونة": 19, "الهضبة": 19, "المحجوب": 18, "القطرون": 18, "بنغازي": 18,
                "طرابلس": 18, "درنه": 18, "بوسليم": 16, "الخمس": 16, "البازة": 15,
                "زليتن": 15, "الفرناج": 15, "الزاوية": 13, "سوق الجمعة": 9, "مصراتة": 9,
            },
            'teams': {
                "بوسليم": [102255, 170629, 50261], "اوجلة": [423562, 49250, 99910],
                "البازة": [116175, 4005689, 2486966], "طرميسة": [701092, 199211, 2098119],
                "درنه": [191337, 4696003, 2601894], "ترهونة": [1941402, 2940600, 179958],
                "غريان": [7928, 6889159, 110964], "الهضبة": [3530273, 2911452, 1128265],
                "بنغازي": [372479, 568897, 3279877], "حي 9 يونيو": [7934485, 1651522, 5259149],
                "الخمس": [1301966, 4168085, 8041861], "المحجوب": [2780336, 746231, 1841364],
                "طرابلس": [2841954, 974668, 554016], "الفرناج": [129548, 1200849, 1163868],
                "مصراتة": [2501532, 255116, 346814], "زليتن": [4795379, 1298141, 3371889],
                "الزاوية": [3507158, 851661, 2811004], "القطرون": [3142905, 1760648, 43105],
                "جالو": [5026431, 117063, 97707], "سوق الجمعة": [46435, 57593, 4701548],
            }
        },
        'libyan': {
            'id': 1231867,
            'initial': {
                "الأخضر": 28, "يفرن": 27, "الصقور": 24, "المستقبل": 24, "الظهرة": 24,
                "العروبة": 24, "الشط": 22, "النصر": 21, "الجزيرة": 21, "الصداقة": 18,
                "الأولمبي": 18, "الملعب": 18, "النصر زليتن": 15, "الأفريقي درنة": 15,
                "الإخاء": 12, "المدينة": 12, "دارنس": 9, "الأهلي طرابلس": 9, "الشرارة": 9, "السويحلي": 9,
            },
            'teams': {
                "السويحلي": [90627, 4314045, 6904125], "الأفريقي درنة": [73166, 48803, 157909],
                "المدينة": [1801960, 1616108, 3708101], "النصر زليتن": [2864, 32014, 1138535],
                "دارنس": [2042169, 79249, 6918866], "الشرارة": [4474659, 4665498, 1382702],
                "العروبة": [2429965, 104498, 2155970], "الصقور": [7161174, 6656930, 6698684],
                "الإخاء": [168059, 1282550, 3049220], "الأهلي طرابلس": [1011498, 5765498, 1018875],
                "النصر": [139498, 2440757, 1304043], "الشط": [8027734, 189473, 31498],
                "يفرن": [8102498, 2486232, 6905498], "الأخضر": [47498, 93498, 2899498],
                "الصداقة": [161498, 3216498, 5626498], "الملعب": [3312498, 4315498, 76498],
                "الجزيرة": [2988586, 92498, 41498], "الظهرة": [7598, 4614103, 1050498],
                "الأولمبي": [24498, 2434498, 4656498], "المستقبل": [6498, 1040498, 3389498],
            }
        },
        'arab': {
            'id': 1015271,
            'initial': {
                "العربي القطري": 28, "العين": 27, "القوة الجوية": 24, "الفتح السعودي": 24,
                "نيوم": 24, "اتحاد العاصمة": 22, "المريخ": 19, "النصر السعودي": 18,
                "النجم الساحلي": 18, "الترجي": 18, "الجزيرة الإماراتي": 16, "الأهلي المصري": 15,
                "الأفريقي": 15, "الاتحاد السعودي": 15, "الوداد": 15, "الرجاء": 15,
                "شبيبة القبائل": 12, "الهلال السعودي": 12, "أربيل": 9, "الهلال السوداني": 9,
            },
            'teams': {
                "الهلال السعودي": [1879543, 88452, 98572], "أربيل": [41808, 670218, 4848368],
                "الجزيرة الإماراتي": [1573546, 5636647, 2634904], "شبيبة القبائل": [1202069, 3270139, 320850],
                "الهلال السوداني": [209410, 378164, 2117536], "المريخ": [5766070, 2401629, 2119541],
                "الرجاء": [1137498, 3303498, 1572498], "النجم الساحلي": [6168498, 99498, 6082498],
                "الأفريقي": [2296498, 4146498, 1070498], "اتحاد العاصمة": [2115498, 2163498, 1065498],
                "الترجي": [6376498, 6364498, 6430498], "الوداد": [6332498, 1109498, 1085498],
                "الأهلي المصري": [5933498, 5930498, 5893498], "القوة الجوية": [5660498, 5700498, 5651498],
                "العين": [5569498, 5590498, 5555498], "نيوم": [5540498, 5471498, 5415498],
                "الفتح السعودي": [5352498, 5361498, 5332498], "الاتحاد السعودي": [5216498, 5219498, 5232498],
                "النصر السعودي": [5276498, 5280498, 5246498], "العربي القطري": [5127498, 5157498, 5109498],
            }
        }
    }
    
    # Fetch GW13 live data
    live_data = fetch_json("https://fantasy.premierleague.com/api/event/13/live/", cookies)
    if not live_data:
        return jsonify({'status': 'error', 'message': 'Could not fetch GW13 live data'})
    
    live_elements = {elem['id']: elem['stats']['total_points'] for elem in live_data['elements']}
    
    for league_type, config in LEAGUES.items():
        # Build entry_to_team lookup
        entry_to_team = {}
        for team_name, ids in config['teams'].items():
            for entry_id in ids:
                entry_to_team[entry_id] = team_name
        
        # Calculate team GW points
        team_gw_points = {}
        for team_name, entry_ids in config['teams'].items():
            total_pts = 0
            for entry_id in entry_ids:
                picks_data = fetch_json(f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/13/picks/", cookies)
                if picks_data:
                    picks = picks_data.get('picks', [])[:11]
                    manager_pts = 0
                    for pick in picks:
                        pts = live_elements.get(pick['element'], 0)
                        mult = pick.get('multiplier', 1)
                        if mult == 3:  # TC = 2x in team leagues
                            mult = 2
                        manager_pts += pts * mult
                    manager_pts -= picks_data.get('entry_history', {}).get('event_transfers_cost', 0)
                    total_pts += manager_pts
            team_gw_points[team_name] = total_pts
        
        # Fetch H2H matches to determine matchups
        matches_data = fetch_json(f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{config['id']}/?event=13", cookies)
        
        # Determine results based on team points
        match_results = {}
        processed_teams = set()
        
        if matches_data:
            for match in matches_data.get('results', []):
                entry_1 = match.get('entry_1_entry')
                entry_2 = match.get('entry_2_entry')
                
                if not entry_1 or not entry_2:
                    continue
                
                team_1 = entry_to_team.get(entry_1)
                team_2 = entry_to_team.get(entry_2)
                
                if not team_1 or not team_2 or team_1 == team_2:
                    continue
                
                if team_1 in processed_teams:
                    continue
                
                pts_1 = team_gw_points.get(team_1, 0)
                pts_2 = team_gw_points.get(team_2, 0)
                
                if pts_1 > pts_2:
                    match_results[team_1] = 'W'
                    match_results[team_2] = 'L'
                elif pts_2 > pts_1:
                    match_results[team_1] = 'L'
                    match_results[team_2] = 'W'
                else:
                    match_results[team_1] = 'D'
                    match_results[team_2] = 'D'
                
                processed_teams.add(team_1)
                processed_teams.add(team_2)
        
        # Calculate final GW13 standings
        gw13_standings = {}
        for team_name in config['teams'].keys():
            base_pts = config['initial'].get(team_name, 0)
            result = match_results.get(team_name, '')
            added = 3 if result == 'W' else (1 if result == 'D' else 0)
            gw13_standings[team_name] = base_pts + added
        
        # Save to database
        save_team_league_standings(league_type, 13, gw13_standings)
        results[league_type] = gw13_standings
    
    return jsonify({
        'status': 'success',
        'message': 'GW13 standings initialized',
        'standings': results
    })


@app.route('/admin/the100/init-qualified')
def init_the100_qualified():
    """Initialize the 100 qualified managers after GW19 - run once"""
    from models import The100QualifiedManager, save_the100_qualified_managers
    from core.the100 import get_qualification_standings, WINNER_ENTRY_ID, THE100_LEAGUE_ID
    
    # Check if already initialized
    existing = The100QualifiedManager.query.first()
    if existing:
        count = The100QualifiedManager.query.count()
        return jsonify({
            'status': 'already_exists',
            'message': f'Qualified managers already initialized ({count} managers)'
        })
    
    # Fetch qualification standings
    qual_standings = get_qualification_standings(THE100_LEAGUE_ID)
    
    if not qual_standings:
        return jsonify({'status': 'error', 'message': 'Could not fetch qualification standings'})
    
    # Determine qualified managers (top 99 + winner)
    qualified = []
    winner_in_top_99 = False
    
    # Check if winner is in top 99
    for row in qual_standings:
        if row.get('entry') == WINNER_ENTRY_ID and row.get('rank', 0) <= 99:
            winner_in_top_99 = True
            break
    
    # Build qualified list
    count_non_winner = 0
    for row in qual_standings:
        entry_id = row.get('entry')
        rank = row.get('rank', 0)
        is_winner = (entry_id == WINNER_ENTRY_ID)
        
        if winner_in_top_99:
            if rank <= 100:
                qualified.append({
                    'entry_id': entry_id,
                    'manager_name': row.get('player_name', ''),
                    'team_name': row.get('entry_name', ''),
                    'qualification_rank': rank,
                    'qualification_total': row.get('total', 0),
                    'is_winner': is_winner
                })
        else:
            if is_winner:
                qualified.append({
                    'entry_id': entry_id,
                    'manager_name': row.get('player_name', ''),
                    'team_name': row.get('entry_name', ''),
                    'qualification_rank': rank,
                    'qualification_total': row.get('total', 0),
                    'is_winner': True
                })
            elif count_non_winner < 99:
                qualified.append({
                    'entry_id': entry_id,
                    'manager_name': row.get('player_name', ''),
                    'team_name': row.get('entry_name', ''),
                    'qualification_rank': rank,
                    'qualification_total': row.get('total', 0),
                    'is_winner': False
                })
                count_non_winner += 1
        
        if len(qualified) >= 100:
            break
    
    # Save to database
    success = save_the100_qualified_managers(qualified)
    
    if success:
        return jsonify({
            'status': 'success',
            'message': f'Initialized {len(qualified)} qualified managers',
            'qualified_count': len(qualified),
            'winner_in_top_99': winner_in_top_99
        })
    else:
        return jsonify({'status': 'error', 'message': 'Failed to save to database'})


@app.route('/admin/the100/process-elimination/<int:gameweek>')
def process_the100_elimination(gameweek):
    """Process elimination for a specific gameweek"""
    from models import (
        The100QualifiedManager, The100EliminationResult,
        save_the100_elimination
    )
    from core.the100 import (
        get_elimination_standings, ELIMINATION_START_GW, 
        ELIMINATION_END_GW, ELIMINATIONS_PER_GW
    )
    
    # Validate gameweek
    if gameweek < ELIMINATION_START_GW or gameweek > ELIMINATION_END_GW:
        return jsonify({
            'status': 'error',
            'message': f'Invalid gameweek. Elimination phase is GW{ELIMINATION_START_GW}-{ELIMINATION_END_GW}'
        })
    
    # Check if already processed
    existing = The100EliminationResult.query.filter_by(gameweek=gameweek).first()
    if existing:
        count = The100EliminationResult.query.filter_by(gameweek=gameweek).count()
        return jsonify({
            'status': 'already_processed',
            'message': f'GW{gameweek} elimination already processed ({count} eliminated)'
        })
    
    # Get remaining qualified managers (not yet eliminated)
    remaining = The100QualifiedManager.query.filter(
        The100QualifiedManager.eliminated_gw.is_(None)
    ).order_by(The100QualifiedManager.qualification_rank).all()
    
    if not remaining:
        return jsonify({'status': 'error', 'message': 'No remaining managers found'})
    
    qualified = [{
        'entry_id': m.entry_id,
        'manager_name': m.manager_name,
        'team_name': m.team_name,
        'qualification_rank': m.qualification_rank,
        'qualification_total': m.qualification_total,
        'is_winner': m.is_winner
    } for m in remaining]
    
    # Get standings for this gameweek
    elim_data = get_elimination_standings(gameweek, qualified)
    
    if not elim_data or not elim_data.get('standings'):
        return jsonify({'status': 'error', 'message': 'Could not fetch elimination standings'})
    
    standings = elim_data['standings']
    
    # Get bottom 6 (to be eliminated)
    eliminated = standings[-ELIMINATIONS_PER_GW:]
    
    eliminated_list = [{
        'entry_id': m['entry_id'],
        'manager_name': m['manager_name'],
        'team_name': m['team_name'],
        'gw_points': m['live_gw_points'],
        'gw_rank': m['live_rank']
    } for m in eliminated]
    
    # Save eliminations
    success = save_the100_elimination(gameweek, eliminated_list)
    
    if success:
        return jsonify({
            'status': 'success',
            'message': f'Processed GW{gameweek} elimination',
            'eliminated': [m['manager_name'] for m in eliminated_list],
            'remaining_count': len(qualified) - ELIMINATIONS_PER_GW
        })
    else:
        return jsonify({'status': 'error', 'message': 'Failed to save eliminations'})


@app.route('/api/the100')
def api_the100():
    """API endpoint for The 100 data"""
    data = get_the100_standings()
    data['timestamp'] = datetime.now().strftime('%H:%M:%S')
    return jsonify(data)


@app.route('/admin/social-posts')
def admin_social_posts():
    """Admin page for generating social media posts"""
    return render_template('admin_social_posts.html')


@app.route('/api/generate-post', methods=['POST'])
def api_generate_post():
    """Generate a social media post for a league using OpenAI"""
    api_key = os.environ.get('OPEN_AI_KEY')
    if not api_key:
        return jsonify({'error': 'OpenAI API key not configured'})

    data = request.get_json()
    league = data.get('league', 'elite')
    post_format = data.get('format', 'twitter')

    try:
        summary = _gather_league_summary(league)
        if not summary:
            return jsonify({'error': 'Failed to fetch league data'})

        post = _call_openai(api_key, summary, post_format)
        return jsonify({'post': post, 'league': league, 'format': post_format})
    except Exception as e:
        print(f"[social-post] Error: {e}")
        return jsonify({'error': f'Error generating post: {str(e)}'})


def _gather_league_summary(league):
    """Gather and format league data into a concise summary for the prompt"""

    if league == 'elite':
        data = get_dashboard()
        if not data.get('success'):
            return None
        gw = data.get('gameweek', '?')
        fixtures_text = ""
        for f in data.get('fixtures', []):
            w = f['team_1_name'] if f['winner'] == 1 else (f['team_2_name'] if f['winner'] == 2 else 'Draw')
            fixtures_text += f"- {f['team_1_name']} {f['team_1_points']} vs {f['team_2_points']} {f['team_2_name']} (Winner: {w})\n"
        standings_text = ""
        for t in data.get('standings', [])[:10]:
            standings_text += f"  {t['rank']}. {t['player_name']} - LP: {t['projected_league_points']}, GW: {t['current_gw_points']}, Captain: {t.get('captain','-')}, Result: {t.get('result','-')}\n"
        chips = [f"{t['player_name']}: {t['chip']}" for t in data.get('standings', []) if t.get('chip_active')]
        return (
            f"League: Elite League (دوري النخبة) - H2H\n"
            f"Gameweek: {gw}\n"
            f"Is Live: {data.get('is_live')}\n\n"
            f"Fixtures:\n{fixtures_text}\n"
            f"Standings (top 10):\n{standings_text}\n"
            f"Chips used: {', '.join(chips) if chips else 'None'}\n"
        )

    elif league == 'the100':
        data = get_the100_standings()
        stats = get_the100_stats()
        if not data or not data.get('standings'):
            return None
        gw = data.get('gameweek', '?')
        phase = data.get('phase', 'unknown')

        top = data['standings'][:10]
        standings_text = ""
        for t in top:
            name = t.get('manager_name', '')
            pts = t.get('live_gw_points', t.get('live_total', 0))
            rank = t.get('live_rank', '')
            standings_text += f"  {rank}. {name} - GW pts: {pts}\n"

        bottom_text = ""
        if phase == 'elimination':
            bottom = [t for t in data['standings'] if t.get('in_elimination_zone')]
            for t in bottom:
                bottom_text += f"  {t.get('live_rank')}. {t['manager_name']} - GW pts: {t['live_gw_points']} (ELIMINATION ZONE)\n"

        stats_text = ""
        if stats and stats.get('success'):
            ps = stats.get('points_stats', {})
            stats_text = (
                f"Points stats: Min={ps.get('min')} ({', '.join(ps.get('min_managers',[]))}), "
                f"Max={ps.get('max')} ({', '.join(ps.get('max_managers',[]))}), "
                f"Avg={ps.get('avg')}\n"
            )
            caps = stats.get('captain_stats', [])[:5]
            if caps:
                caps_str = ', '.join(f"{c['name']} ({c['count']})" for c in caps)
                stats_text += f"Top captains: {caps_str}\n"

        phase_info = data.get('phase_info', {})
        elim_section = f"Elimination Zone:\n{bottom_text}\n" if bottom_text else ""
        return (
            f"League: The 100 (دوري المئة)\n"
            f"Phase: {phase_info.get('name_en', phase)} ({phase_info.get('name', '')})\n"
            f"Gameweek: {gw}\n"
            f"Total managers: {data.get('total_managers', 0)}, Remaining: {data.get('remaining_managers', '')}\n\n"
            f"Top 10:\n{standings_text}\n"
            f"{elim_section}"
            f"{stats_text}"
        )

    elif league in ('libyan', 'arab', 'cities'):
        funcs = {
            'libyan': (get_libyan_league_data, 'Libyan League (الدوري الليبي)', 'Team H2H with 3 managers per team'),
            'arab': (get_arab_league_data, 'Arab League (الدوري العربي)', 'Team H2H with 3 managers per team'),
            'cities': (get_cities_league_data, 'Cities League (دوري المدن)', 'Team H2H with 3 managers per team'),
        }
        func, name, desc = funcs[league]
        data = func()
        if not data or not data.get('standings'):
            return None
        gw = data.get('gameweek', '?')

        standings_text = ""
        for t in data.get('standings', []):
            standings_text += f"  {t.get('rank','?')}. {t['team_name']} - LP: {t['league_points']}, GW: {t['live_gw_points']}, Result: {t.get('result','')}\n"

        matches_text = ""
        for m in data.get('matches', []):
            w = m['team_1'] if m['winner'] == 1 else (m['team_2'] if m['winner'] == 2 else 'Draw')
            matches_text += f"- {m['team_1']} {m['points_1']} vs {m['points_2']} {m['team_2']} (Winner: {w})\n"

        best = ""
        bt = data.get('best_team')
        bm = data.get('best_manager')
        if bt:
            best += f"Best team: {bt['name']} ({bt['points']} pts)\n"
        if bm:
            best += f"Best manager: {bm['name']} ({bm['points']} pts, team: {bm.get('team','')})\n"

        return (
            f"League: {name}\n"
            f"Format: {desc}\n"
            f"Gameweek: {gw}\n\n"
            f"Matches:\n{matches_text}\n"
            f"Standings:\n{standings_text}\n"
            f"{best}"
        )

    return None


def _call_openai(api_key, summary, post_format):
    """Call OpenAI API to generate the social media post"""
    if post_format == 'twitter':
        format_instruction = (
            "اكتب منشور تويتر باللغة العربية عن نتائج هذه الجولة. "
            "يجب أن يكون المنشور مختصر وجذاب ولا يتجاوز 280 حرف. "
            "ركز على أهم النتائج والأحداث."
        )
    else:
        format_instruction = (
            "اكتب منشور انستقرام مفصل باللغة العربية عن نتائج هذه الجولة. "
            "اكتب ملخص شامل مع ايموجي وهاشتاقات. "
            "تحدث عن النتائج والترتيب واللاعبين المميزين والكباتن."
        )

    response = http_requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json={
            'model': 'gpt-4o-mini',
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        "أنت صحفي رياضي متخصص في فانتازي الدوري الإنجليزي. "
                        "تكتب منشورات سوشيال ميديا جذابة باللغة العربية. "
                        "ركز على القصص المثيرة: النتائج المفاجئة، الفوارق الكبيرة، "
                        "المنافسة على الصدارة، منطقة الخطر، الكباتن المميزين. "
                        "استخدم أسلوب حماسي ومشوق."
                    )
                },
                {
                    'role': 'user',
                    'content': f"{summary}\n\n{format_instruction}"
                }
            ],
            'temperature': 0.8,
            'max_tokens': 1000,
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(f"OpenAI API error: {response.status_code} - {response.text[:200]}")

    result = response.json()
    return result['choices'][0]['message']['content'].strip()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=False, host='0.0.0.0', port=port)
