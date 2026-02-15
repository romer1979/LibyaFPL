# -*- coding: utf-8 -*-
"""
Elite League History Module

Provides historical standings and fixture results for the Elite League.
Reads from StandingsHistory and FixtureResult database tables.
"""

from models import StandingsHistory, FixtureResult, db


def get_elite_history_from_db():
    """
    Get Elite League history from database.
    Returns dict with gameweek data including standings and fixtures.
    """
    # Get all standings
    all_standings = StandingsHistory.query.order_by(
        StandingsHistory.gameweek
    ).all()

    if not all_standings:
        return None

    # Get all fixture results
    all_fixtures = FixtureResult.query.order_by(
        FixtureResult.gameweek
    ).all()

    # Group by gameweek
    history = {}

    for s in all_standings:
        gw = s.gameweek
        if gw not in history:
            history[gw] = {'standings': [], 'fixtures': []}

        history[gw]['standings'].append({
            'entry_id': s.entry_id,
            'player_name': s.player_name,
            'team_name': s.team_name or '',
            'rank': s.rank or 0,
            'league_points': s.league_points or 0,
            'gw_points': s.gw_points or 0,
            'total_points': s.total_points or 0,
            'overall_rank': s.overall_rank,
            'result': s.result or '-',
            'opponent': s.opponent or '-',
            'captain': s.captain or '-',
            'chip': s.chip or '',
        })

    for f in all_fixtures:
        gw = f.gameweek
        if gw not in history:
            history[gw] = {'standings': [], 'fixtures': []}

        history[gw]['fixtures'].append({
            'entry_1_name': f.entry_1_name,
            'entry_2_name': f.entry_2_name,
            'entry_1_points': f.entry_1_points,
            'entry_2_points': f.entry_2_points,
            'winner': f.winner,
        })

    # Sort standings within each GW by league_points then total_points
    for gw in history:
        history[gw]['standings'].sort(key=lambda x: (
            -x['league_points'],
            -x['total_points'],
            x.get('overall_rank') or float('inf')
        ))
        # Re-rank
        for i, team in enumerate(history[gw]['standings'], 1):
            team['rank'] = i

    return history


def get_elite_history_data():
    """Get all data needed for Elite League history page."""
    history = get_elite_history_from_db()

    if not history:
        return {
            'league_name': 'دوري النخبة',
            'logo_file': 'elite_league_logo.png',
            'back_url': '/league/elite',
            'gameweeks': [],
            'history': {},
            'no_data': True,
        }

    gameweeks = sorted(history.keys())

    return {
        'league_name': 'دوري النخبة',
        'logo_file': 'elite_league_logo.png',
        'back_url': '/league/elite',
        'gameweeks': gameweeks,
        'history': history,
        'no_data': False,
    }
