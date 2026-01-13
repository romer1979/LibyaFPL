# -*- coding: utf-8 -*-
"""
Team League History Module

Provides historical data for Arab, Libyan, and Cities leagues.
Reads from database for fast loading.
"""

from models import TeamLeagueStandings, db

# League configurations
LEAGUE_CONFIGS = {
    'arab': {
        'name': 'البطولة العربية',
        'h2h_id': 1015271,
        'logo': 'arab_logo.png',
        'back_url': '/league/arab',
    },
    'libyan': {
        'name': 'الدوري الليبي',
        'h2h_id': 1231867,
        'logo': 'libyan_logo.png',
        'back_url': '/league/libyan',
    },
    'cities': {
        'name': 'دوري المدن',
        'h2h_id': 1011575,
        'logo': 'cities_logo.png',
        'back_url': '/league/cities',
    }
}


def get_league_history_from_db(league_type):
    """
    Get history from database.
    Returns dict with gameweek data including standings.
    """
    if league_type not in LEAGUE_CONFIGS:
        return None
    
    # Get all standings for this league from database
    all_standings = TeamLeagueStandings.query.filter_by(
        league_type=league_type
    ).order_by(TeamLeagueStandings.gameweek).all()
    
    if not all_standings:
        return None
    
    # Group by gameweek
    history = {}
    gameweeks = set()
    
    for s in all_standings:
        gw = s.gameweek
        gameweeks.add(gw)
        
        if gw not in history:
            history[gw] = {
                'standings': [],
                'matches': []  # We don't store matches in DB, will be empty for now
            }
        
        history[gw]['standings'].append({
            'name': s.team_name,
            'league_points': s.league_points,
            'total_fpl_points': s.total_fpl_points or 0,
            'gw_result': '-',  # Will calculate below
        })
    
    # Sort standings within each gameweek and calculate GW results
    for gw in history:
        # Sort by league points, then FPL points
        history[gw]['standings'].sort(key=lambda x: (-x['league_points'], -x['total_fpl_points']))
        
        # Calculate GW result by comparing to previous GW
        if gw > 1 and (gw - 1) in history:
            prev_standings = {s['name']: s['league_points'] for s in history[gw - 1]['standings']}
            
            for team in history[gw]['standings']:
                prev_pts = prev_standings.get(team['name'], 0)
                curr_pts = team['league_points']
                diff = curr_pts - prev_pts
                
                if diff == 3:
                    team['gw_result'] = 'W'
                elif diff == 1:
                    team['gw_result'] = 'D'
                else:
                    team['gw_result'] = 'L'
        else:
            # For GW1, calculate from points directly
            for team in history[gw]['standings']:
                pts = team['league_points']
                if pts == 3:
                    team['gw_result'] = 'W'
                elif pts == 1:
                    team['gw_result'] = 'D'
                else:
                    team['gw_result'] = 'L'
    
    return history


def get_league_history_data(league_type):
    """
    Get all data needed for history page.
    """
    if league_type not in LEAGUE_CONFIGS:
        return None
    
    config = LEAGUE_CONFIGS[league_type]
    history = get_league_history_from_db(league_type)
    
    if not history:
        # Return empty state if no data
        return {
            'league_name': config['name'],
            'logo_file': config['logo'],
            'back_url': config['back_url'],
            'gameweeks': [],
            'history': {},
            'no_data': True,
        }
    
    gameweeks = sorted(history.keys())
    
    return {
        'league_name': config['name'],
        'logo_file': config['logo'],
        'back_url': config['back_url'],
        'gameweeks': gameweeks,
        'history': history,
        'no_data': False,
    }
