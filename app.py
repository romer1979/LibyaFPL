# -*- coding: utf-8 -*-
"""
Fantasy Premier League Multi-League App
"""

from flask import Flask, render_template, jsonify
import os
import sys
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
from models import db, save_standings, calculate_rank_change

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


@app.route('/league/elite')
def elite_dashboard():
    """Elite League dashboard page"""
    data = get_dashboard()
    
    # Calculate rank changes from database
    if data.get('success') and data.get('standings'):
        gameweek = data.get('gameweek', 1)
        
        for team in data['standings']:
            entry_id = team.get('entry_id')
            current_rank = team.get('rank', 0)
            
            # Get rank change from previous gameweek
            rank_change = calculate_rank_change(gameweek, entry_id, current_rank)
            team['rank_change'] = rank_change
        
        # Save current standings to database (only if gameweek is finished or live)
        if data.get('gw_finished') or data.get('is_live'):
            save_standings(gameweek, data['standings'])
    
    return render_template('dashboard.html', data=data, ar=ARABIC)


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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=False, host='0.0.0.0', port=port)
