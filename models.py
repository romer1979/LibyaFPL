# -*- coding: utf-8 -*-
"""
Database Models for Elite League
Stores standings history per gameweek for rank change tracking
"""

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class StandingsHistory(db.Model):
    """Stores standings snapshot for each gameweek"""
    __tablename__ = 'standings_history'
    
    id = db.Column(db.Integer, primary_key=True)
    gameweek = db.Column(db.Integer, nullable=False)
    entry_id = db.Column(db.Integer, nullable=False)
    player_name = db.Column(db.String(100), nullable=False)
    team_name = db.Column(db.String(100))
    
    # Ranking data
    rank = db.Column(db.Integer)  # League rank for this GW
    league_points = db.Column(db.Integer, default=0)
    gw_points = db.Column(db.Integer, default=0)
    total_points = db.Column(db.Integer, default=0)
    overall_rank = db.Column(db.Integer)
    
    # Match result
    result = db.Column(db.String(1))  # W, L, D
    opponent = db.Column(db.String(100))
    
    # Captain and chip
    captain = db.Column(db.String(50))
    chip = db.Column(db.String(20))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Unique constraint: one entry per player per gameweek
    __table_args__ = (
        db.UniqueConstraint('gameweek', 'entry_id', name='unique_gw_entry'),
    )
    
    def __repr__(self):
        return f'<StandingsHistory GW{self.gameweek} {self.player_name} Rank:{self.rank}>'


class FixtureResult(db.Model):
    """Stores H2H fixture results per gameweek"""
    __tablename__ = 'fixture_results'
    
    id = db.Column(db.Integer, primary_key=True)
    gameweek = db.Column(db.Integer, nullable=False)
    
    # Team 1
    entry_1_id = db.Column(db.Integer, nullable=False)
    entry_1_name = db.Column(db.String(100))
    entry_1_points = db.Column(db.Integer, default=0)
    
    # Team 2
    entry_2_id = db.Column(db.Integer, nullable=False)
    entry_2_name = db.Column(db.String(100))
    entry_2_points = db.Column(db.Integer, default=0)
    
    # Result: 1 = team1 won, 2 = team2 won, 0 = draw
    winner = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('gameweek', 'entry_1_id', 'entry_2_id', name='unique_gw_fixture'),
    )
    
    def __repr__(self):
        return f'<FixtureResult GW{self.gameweek} {self.entry_1_name} vs {self.entry_2_name}>'


def save_standings(gameweek, standings_data):
    """Save or update standings for a gameweek"""
    for team in standings_data:
        existing = StandingsHistory.query.filter_by(
            gameweek=gameweek,
            entry_id=team.get('entry_id')
        ).first()
        
        if existing:
            # Update existing record
            existing.rank = team.get('rank')
            existing.league_points = team.get('projected_league_points', 0)
            existing.gw_points = team.get('current_gw_points', 0)
            existing.total_points = team.get('total_points', 0)
            existing.overall_rank = team.get('overall_rank')
            existing.result = team.get('result')
            existing.opponent = team.get('opponent')
            existing.captain = team.get('captain')
            existing.chip = team.get('chip')
            existing.updated_at = datetime.utcnow()
        else:
            # Create new record
            new_standing = StandingsHistory(
                gameweek=gameweek,
                entry_id=team.get('entry_id'),
                player_name=team.get('player_name'),
                team_name=team.get('team_name'),
                rank=team.get('rank'),
                league_points=team.get('projected_league_points', 0),
                gw_points=team.get('current_gw_points', 0),
                total_points=team.get('total_points', 0),
                overall_rank=team.get('overall_rank'),
                result=team.get('result'),
                opponent=team.get('opponent'),
                captain=team.get('captain'),
                chip=team.get('chip')
            )
            db.session.add(new_standing)
    
    try:
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error saving standings: {e}")
        return False


def get_previous_standings(gameweek, entry_id):
    """Get standings from previous gameweek for rank comparison"""
    if gameweek <= 1:
        return None
    
    return StandingsHistory.query.filter_by(
        gameweek=gameweek - 1,
        entry_id=entry_id
    ).first()


def get_standings_history(entry_id):
    """Get all historical standings for a player"""
    return StandingsHistory.query.filter_by(
        entry_id=entry_id
    ).order_by(StandingsHistory.gameweek).all()


def calculate_rank_change(current_gameweek, entry_id, current_rank):
    """Calculate rank change compared to previous gameweek"""
    previous = get_previous_standings(current_gameweek, entry_id)
    
    if previous and previous.rank:
        # Positive = moved up (better rank), Negative = moved down
        return previous.rank - current_rank
    
    return 0


class TeamLeagueStandings(db.Model):
    """Stores team-based league standings (Cities, Libyan, Arab)"""
    __tablename__ = 'team_league_standings'
    
    id = db.Column(db.Integer, primary_key=True)
    league_type = db.Column(db.String(20), nullable=False)  # 'cities', 'libyan', 'arab'
    gameweek = db.Column(db.Integer, nullable=False)
    team_name = db.Column(db.String(100), nullable=False)
    league_points = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('league_type', 'gameweek', 'team_name', name='unique_team_league_gw'),
    )
    
    def __repr__(self):
        return f'<TeamLeagueStandings {self.league_type} GW{self.gameweek} {self.team_name}: {self.league_points}>'


def get_team_league_standings(league_type, gameweek):
    """Get standings for a specific gameweek"""
    standings = TeamLeagueStandings.query.filter_by(
        league_type=league_type,
        gameweek=gameweek
    ).all()
    return {s.team_name: s.league_points for s in standings}


def get_latest_team_league_standings(league_type):
    """Get the most recent saved standings for a league"""
    # Find the latest gameweek that has standings
    latest = db.session.query(db.func.max(TeamLeagueStandings.gameweek)).filter_by(
        league_type=league_type
    ).scalar()
    
    if latest:
        return get_team_league_standings(league_type, latest), latest
    return {}, 0


def save_team_league_standings(league_type, gameweek, standings_dict):
    """Save standings for a gameweek
    standings_dict: {team_name: league_points}
    """
    for team_name, points in standings_dict.items():
        existing = TeamLeagueStandings.query.filter_by(
            league_type=league_type,
            gameweek=gameweek,
            team_name=team_name
        ).first()
        
        if existing:
            existing.league_points = points
            existing.updated_at = datetime.utcnow()
        else:
            new_standing = TeamLeagueStandings(
                league_type=league_type,
                gameweek=gameweek,
                team_name=team_name,
                league_points=points
            )
            db.session.add(new_standing)
    
    try:
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error saving team league standings: {e}")
        return False
