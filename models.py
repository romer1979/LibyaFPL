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


def get_elite_previous_league_points(gameweek):
    """
    Return {entry_id: league_points} from StandingsHistory for gameweek-1.
    Empty dict if nothing saved yet (e.g. GW1 or fresh DB).

    Used by the Elite dashboard to compute current-GW projected league points
    as `prev_cumulative + this_gw_h2h_delta` instead of trusting FPL's `total`
    field (which lags during/just after a GW).
    """
    if gameweek <= 1:
        return {}
    rows = StandingsHistory.query.filter_by(gameweek=gameweek - 1).all()
    return {r.entry_id: (r.league_points or 0) for r in rows}


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
    total_fpl_points = db.Column(db.Integer, default=0)  # Cumulative FPL points (custom calculation)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('league_type', 'gameweek', 'team_name', name='unique_team_league_gw'),
    )
    
    def __repr__(self):
        return f'<TeamLeagueStandings {self.league_type} GW{self.gameweek} {self.team_name}: {self.league_points}>'


class TeamLeagueMatches(db.Model):
    """Stores match results for team-based leagues"""
    __tablename__ = 'team_league_matches'
    
    id = db.Column(db.Integer, primary_key=True)
    league_type = db.Column(db.String(20), nullable=False)  # 'cities', 'libyan', 'arab'
    gameweek = db.Column(db.Integer, nullable=False)
    team1_name = db.Column(db.String(100), nullable=False)
    team2_name = db.Column(db.String(100), nullable=False)
    team1_points = db.Column(db.Integer, default=0)  # FPL points for this GW
    team2_points = db.Column(db.Integer, default=0)  # FPL points for this GW
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('league_type', 'gameweek', 'team1_name', 'team2_name', name='unique_match'),
    )
    
    def __repr__(self):
        return f'<TeamLeagueMatches {self.league_type} GW{self.gameweek} {self.team1_name} vs {self.team2_name}>'


def get_team_league_matches(league_type, gameweek):
    """Get matches for a specific gameweek"""
    matches = TeamLeagueMatches.query.filter_by(
        league_type=league_type,
        gameweek=gameweek
    ).all()
    return [{
        'team1': m.team1_name,
        'team2': m.team2_name,
        'points1': m.team1_points,
        'points2': m.team2_points,
    } for m in matches]


def save_team_league_matches(league_type, gameweek, matches_list):
    """Save matches for a gameweek
    matches_list: [{team1, team2, points1, points2}, ...]
    """
    for match in matches_list:
        existing = TeamLeagueMatches.query.filter_by(
            league_type=league_type,
            gameweek=gameweek,
            team1_name=match['team1'],
            team2_name=match['team2']
        ).first()
        
        if existing:
            existing.team1_points = match['points1']
            existing.team2_points = match['points2']
        else:
            new_match = TeamLeagueMatches(
                league_type=league_type,
                gameweek=gameweek,
                team1_name=match['team1'],
                team2_name=match['team2'],
                team1_points=match['points1'],
                team2_points=match['points2']
            )
            db.session.add(new_match)
    
    try:
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error saving team league matches: {e}")
        return False


def get_team_league_standings(league_type, gameweek):
    """Get standings for a specific gameweek"""
    standings = TeamLeagueStandings.query.filter_by(
        league_type=league_type,
        gameweek=gameweek
    ).all()
    return {s.team_name: s.league_points for s in standings}


def get_team_league_standings_full(league_type, gameweek):
    """Get standings with total_fpl_points for a specific gameweek"""
    standings = TeamLeagueStandings.query.filter_by(
        league_type=league_type,
        gameweek=gameweek
    ).all()
    return {s.team_name: {'league_points': s.league_points, 'total_fpl_points': s.total_fpl_points or 0} for s in standings}


def get_latest_team_league_standings(league_type):
    """Get the most recent saved standings for a league"""
    # Find the latest gameweek that has standings
    latest = db.session.query(db.func.max(TeamLeagueStandings.gameweek)).filter_by(
        league_type=league_type
    ).scalar()
    
    if latest:
        return get_team_league_standings(league_type, latest), latest
    return {}, 0


def save_team_league_standings(league_type, gameweek, standings_dict, fpl_points_dict=None):
    """Save standings for a gameweek
    standings_dict: {team_name: league_points}
    fpl_points_dict: {team_name: total_fpl_points} (optional)
    """
    for team_name, points in standings_dict.items():
        existing = TeamLeagueStandings.query.filter_by(
            league_type=league_type,
            gameweek=gameweek,
            team_name=team_name
        ).first()
        
        fpl_points = fpl_points_dict.get(team_name, 0) if fpl_points_dict else 0
        
        if existing:
            existing.league_points = points
            existing.total_fpl_points = fpl_points
            existing.updated_at = datetime.utcnow()
        else:
            new_standing = TeamLeagueStandings(
                league_type=league_type,
                gameweek=gameweek,
                team_name=team_name,
                league_points=points,
                total_fpl_points=fpl_points
            )
            db.session.add(new_standing)
    
    try:
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error saving team league standings: {e}")
        return False


# ============================================
# THE 100 LEAGUE MODELS
# ============================================

class The100QualifiedManager(db.Model):
    """Stores the 100 qualified managers after GW19"""
    __tablename__ = 'the100_qualified'
    
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, nullable=False, unique=True)
    manager_name = db.Column(db.String(100), nullable=False)
    team_name = db.Column(db.String(100))
    qualification_rank = db.Column(db.Integer, nullable=False)
    qualification_total = db.Column(db.Integer, default=0)
    is_winner = db.Column(db.Boolean, default=False)  # Previous season champion
    
    # Elimination tracking
    eliminated_gw = db.Column(db.Integer, nullable=True)  # GW when eliminated (null = still in)
    final_rank = db.Column(db.Integer, nullable=True)  # Final rank when eliminated
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<The100Qualified {self.manager_name} Q#{self.qualification_rank}>'


class The100EliminationResult(db.Model):
    """Stores weekly elimination results"""
    __tablename__ = 'the100_eliminations'
    
    id = db.Column(db.Integer, primary_key=True)
    gameweek = db.Column(db.Integer, nullable=False)
    entry_id = db.Column(db.Integer, nullable=False)
    manager_name = db.Column(db.String(100))
    team_name = db.Column(db.String(100))
    gw_points = db.Column(db.Integer, default=0)
    gw_rank = db.Column(db.Integer)  # Rank within that GW (e.g., 95-100 for bottom 6)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('gameweek', 'entry_id', name='unique_the100_elim_gw_entry'),
    )
    
    def __repr__(self):
        return f'<The100Elimination GW{self.gameweek} {self.manager_name}>'


class The100ChampionshipMatch(db.Model):
    """Stores championship bracket matches (GW34-37)"""
    __tablename__ = 'the100_championship'

    id = db.Column(db.Integer, primary_key=True)
    gameweek = db.Column(db.Integer, nullable=False)
    round_name = db.Column(db.String(20))  # 'round_16', 'quarter', 'semi', 'final'
    match_number = db.Column(db.Integer)  # 1-8 for R16, 1-4 for QF, 1-2 for SF, 1 for Final

    # Participants (nullable for later rounds before winners propagate)
    entry_1_id = db.Column(db.Integer, nullable=True)
    entry_1_name = db.Column(db.String(100), nullable=True)
    entry_1_team_name = db.Column(db.String(100), nullable=True)
    entry_1_seed = db.Column(db.Integer, nullable=True)
    entry_1_points = db.Column(db.Integer, default=0)
    entry_1_total = db.Column(db.Integer, nullable=True)  # total season points (for tiebreak)

    entry_2_id = db.Column(db.Integer, nullable=True)
    entry_2_name = db.Column(db.String(100), nullable=True)
    entry_2_team_name = db.Column(db.String(100), nullable=True)
    entry_2_seed = db.Column(db.Integer, nullable=True)
    entry_2_points = db.Column(db.Integer, default=0)
    entry_2_total = db.Column(db.Integer, nullable=True)

    # Result
    winner_id = db.Column(db.Integer, nullable=True)
    is_complete = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('round_name', 'match_number', name='unique_the100_champ_match'),
    )

    def __repr__(self):
        return f'<The100Championship {self.round_name} #{self.match_number}>'


# Helper functions for The 100
def get_the100_qualified_managers():
    """Get all qualified managers"""
    return The100QualifiedManager.query.filter(
        The100QualifiedManager.eliminated_gw.is_(None)
    ).order_by(The100QualifiedManager.qualification_rank).all()


def get_the100_eliminated_in_gw(gameweek):
    """Get managers eliminated in a specific gameweek"""
    return The100EliminationResult.query.filter_by(gameweek=gameweek).all()


def save_the100_qualified_managers(managers_list):
    """
    Save the initial 100 qualified managers (called after GW19)
    managers_list: list of dicts with entry_id, manager_name, team_name, qualification_rank, qualification_total, is_winner
    """
    for manager in managers_list:
        existing = The100QualifiedManager.query.filter_by(
            entry_id=manager['entry_id']
        ).first()
        
        if not existing:
            new_manager = The100QualifiedManager(
                entry_id=manager['entry_id'],
                manager_name=manager['manager_name'],
                team_name=manager['team_name'],
                qualification_rank=manager['qualification_rank'],
                qualification_total=manager.get('qualification_total', 0),
                is_winner=manager.get('is_winner', False)
            )
            db.session.add(new_manager)
    
    try:
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error saving The 100 qualified managers: {e}")
        return False


class The100GameweekRanking(db.Model):
    """Full ranking for each manager per elimination-phase GW (for history view)."""
    __tablename__ = 'the100_gw_rankings'

    id = db.Column(db.Integer, primary_key=True)
    gameweek = db.Column(db.Integer, nullable=False, index=True)
    entry_id = db.Column(db.Integer, nullable=False)
    manager_name = db.Column(db.String(100))
    team_name = db.Column(db.String(100))
    gw_points = db.Column(db.Integer, default=0)  # net of hits
    gw_rank = db.Column(db.Integer)               # rank within alive pool for this GW
    was_eliminated = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('gameweek', 'entry_id', name='unique_the100_rank_gw_entry'),
    )

    def __repr__(self):
        return f'<The100GameweekRanking GW{self.gameweek} {self.manager_name} #{self.gw_rank}>'


def save_the100_gameweek_ranking(gameweek, ranking_list):
    """
    Replace the full ranking for a GW.
    ranking_list: list of dicts with entry_id, manager_name, team_name,
                  gw_points, gw_rank, was_eliminated (bool)
    """
    The100GameweekRanking.query.filter_by(gameweek=gameweek).delete(synchronize_session=False)
    for r in ranking_list:
        db.session.add(The100GameweekRanking(
            gameweek=gameweek,
            entry_id=r['entry_id'],
            manager_name=r.get('manager_name', ''),
            team_name=r.get('team_name', ''),
            gw_points=r.get('gw_points', 0),
            gw_rank=r.get('gw_rank', 0),
            was_eliminated=bool(r.get('was_eliminated', False)),
        ))
    try:
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error saving The 100 GW{gameweek} ranking: {e}")
        return False


def get_the100_history():
    """
    Return {gameweek: [row_dicts sorted by gw_rank asc]} for every GW that has rankings saved.
    """
    rows = The100GameweekRanking.query.order_by(
        The100GameweekRanking.gameweek.asc(),
        The100GameweekRanking.gw_rank.asc(),
    ).all()
    history = {}
    for r in rows:
        history.setdefault(r.gameweek, []).append({
            'entry_id': r.entry_id,
            'manager_name': r.manager_name,
            'team_name': r.team_name,
            'gw_points': r.gw_points,
            'gw_rank': r.gw_rank,
            'was_eliminated': r.was_eliminated,
        })
    return history


def save_the100_elimination(gameweek, eliminated_managers):
    """
    Save elimination results for a gameweek
    eliminated_managers: list of dicts with entry_id, manager_name, team_name, gw_points, gw_rank
    """
    for manager in eliminated_managers:
        # Save to elimination results
        existing = The100EliminationResult.query.filter_by(
            gameweek=gameweek,
            entry_id=manager['entry_id']
        ).first()
        
        if not existing:
            new_elim = The100EliminationResult(
                gameweek=gameweek,
                entry_id=manager['entry_id'],
                manager_name=manager['manager_name'],
                team_name=manager.get('team_name', ''),
                gw_points=manager.get('gw_points', 0),
                gw_rank=manager.get('gw_rank', 0)
            )
            db.session.add(new_elim)
        
        # Update qualified manager record
        qualified = The100QualifiedManager.query.filter_by(
            entry_id=manager['entry_id']
        ).first()
        
        if qualified:
            qualified.eliminated_gw = gameweek
            qualified.final_rank = manager.get('gw_rank', 0)
    
    try:
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error saving The 100 eliminations: {e}")
        return False


# ============================================
# The 100 Championship bracket helpers
# ============================================

# Standard 16-team seeded bracket: each row is (seed_a, seed_b) for R16 match i.
# Seeds 1 and 2 can only meet in the final, 1 and 3 can only meet in the SFs.
CHAMPIONSHIP_R16_SEED_PAIRS = [
    (1, 16),  # M1
    (8, 9),   # M2
    (5, 12),  # M3
    (4, 13),  # M4
    (6, 11),  # M5
    (3, 14),  # M6
    (7, 10),  # M7
    (2, 15),  # M8
]

CHAMPIONSHIP_ROUND_GW = {
    'round_16': 34,
    'quarter':  35,
    'semi':     36,
    'final':    37,
}

CHAMPIONSHIP_NEXT_ROUND = {
    'round_16': 'quarter',
    'quarter':  'semi',
    'semi':     'final',
}


def _recreate_championship_table():
    """Drop and recreate the_100 championship table (safe because bracket
    is regenerated end-to-end when this is called)."""
    The100ChampionshipMatch.__table__.drop(db.engine, checkfirst=True)
    The100ChampionshipMatch.__table__.create(db.engine)


def generate_the100_bracket(survivors):
    """
    Create the full 15-match bracket from the 16 championship survivors.
    survivors: list of dicts with entry_id, manager_name, team_name, total_points.
               Must contain exactly 16 entries.
    Seeds 1..16 are assigned by total_points descending (tiebreak entry_id asc).
    R16 slots are populated; QF/SF/Final are created empty and filled as winners
    propagate via advance_the100_round().
    """
    if len(survivors) != 16:
        raise ValueError(f"generate_the100_bracket requires exactly 16 survivors, got {len(survivors)}")

    # Assign seeds by total_points desc
    seeded = sorted(
        survivors,
        key=lambda s: (-int(s.get('total_points', 0) or 0), int(s['entry_id'])),
    )
    by_seed = {i + 1: s for i, s in enumerate(seeded)}

    _recreate_championship_table()

    # R16: 8 matches, paired by CHAMPIONSHIP_R16_SEED_PAIRS
    for match_num, (seed_a, seed_b) in enumerate(CHAMPIONSHIP_R16_SEED_PAIRS, start=1):
        a, b = by_seed[seed_a], by_seed[seed_b]
        db.session.add(The100ChampionshipMatch(
            gameweek=CHAMPIONSHIP_ROUND_GW['round_16'],
            round_name='round_16',
            match_number=match_num,
            entry_1_id=a['entry_id'],
            entry_1_name=a['manager_name'],
            entry_1_team_name=a.get('team_name', ''),
            entry_1_seed=seed_a,
            entry_1_total=int(a.get('total_points', 0) or 0),
            entry_2_id=b['entry_id'],
            entry_2_name=b['manager_name'],
            entry_2_team_name=b.get('team_name', ''),
            entry_2_seed=seed_b,
            entry_2_total=int(b.get('total_points', 0) or 0),
        ))

    # QF (4), SF (2), Final (1) — empty slots, will be filled on advance
    for round_name, count in (('quarter', 4), ('semi', 2), ('final', 1)):
        for match_num in range(1, count + 1):
            db.session.add(The100ChampionshipMatch(
                gameweek=CHAMPIONSHIP_ROUND_GW[round_name],
                round_name=round_name,
                match_number=match_num,
            ))

    db.session.commit()
    return True


def advance_the100_round(round_name, gw_net_points_by_entry):
    """
    Resolve a round's matches (fill in winner_id/points/is_complete) and
    propagate winners into the next round.

    round_name: 'round_16', 'quarter', or 'semi' (not 'final' -- final needs
                no propagation, just resolution).
    gw_net_points_by_entry: {entry_id: net_gw_points_int}
    """
    matches = The100ChampionshipMatch.query.filter_by(
        round_name=round_name
    ).order_by(The100ChampionshipMatch.match_number.asc()).all()

    winners = []  # list of (entry_id, manager_name, team_name, total, seed) tuples, in match order

    for m in matches:
        if m.entry_1_id is None or m.entry_2_id is None:
            continue  # not yet populated (earlier round not advanced)

        p1 = int(gw_net_points_by_entry.get(m.entry_1_id, 0))
        p2 = int(gw_net_points_by_entry.get(m.entry_2_id, 0))
        m.entry_1_points = p1
        m.entry_2_points = p2

        # Decide winner: higher net GW pts, tiebreak higher total season points
        if p1 > p2:
            winner_side = 1
        elif p2 > p1:
            winner_side = 2
        else:
            t1 = m.entry_1_total or 0
            t2 = m.entry_2_total or 0
            winner_side = 1 if t1 >= t2 else 2

        if winner_side == 1:
            m.winner_id = m.entry_1_id
            winner = (m.entry_1_id, m.entry_1_name, m.entry_1_team_name,
                      m.entry_1_total or 0, m.entry_1_seed)
        else:
            m.winner_id = m.entry_2_id
            winner = (m.entry_2_id, m.entry_2_name, m.entry_2_team_name,
                      m.entry_2_total or 0, m.entry_2_seed)
        m.is_complete = True
        winners.append((m.match_number, winner))

    # Propagate into next round (except for 'final')
    next_round = CHAMPIONSHIP_NEXT_ROUND.get(round_name)
    if next_round:
        # Winners of M(2k-1) and M(2k) feed next round match k
        for match_num, winner in winners:
            next_match_num = (match_num + 1) // 2
            slot = 1 if (match_num % 2 == 1) else 2
            next_match = The100ChampionshipMatch.query.filter_by(
                round_name=next_round, match_number=next_match_num
            ).first()
            if not next_match:
                continue
            if slot == 1:
                next_match.entry_1_id = winner[0]
                next_match.entry_1_name = winner[1]
                next_match.entry_1_team_name = winner[2]
                next_match.entry_1_total = winner[3]
                next_match.entry_1_seed = winner[4]
            else:
                next_match.entry_2_id = winner[0]
                next_match.entry_2_name = winner[1]
                next_match.entry_2_team_name = winner[2]
                next_match.entry_2_total = winner[3]
                next_match.entry_2_seed = winner[4]

    try:
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error advancing {round_name}: {e}")
        return False


def get_the100_bracket():
    """Return {round_name: [match_dict, ...]} for the full bracket, ordered by match_number."""
    rows = The100ChampionshipMatch.query.order_by(
        The100ChampionshipMatch.gameweek.asc(),
        The100ChampionshipMatch.match_number.asc(),
    ).all()
    bracket = {}
    for m in rows:
        bracket.setdefault(m.round_name, []).append({
            'match_number': m.match_number,
            'gameweek': m.gameweek,
            'entry_1_id': m.entry_1_id,
            'entry_1_name': m.entry_1_name,
            'entry_1_team_name': m.entry_1_team_name,
            'entry_1_seed': m.entry_1_seed,
            'entry_1_points': m.entry_1_points,
            'entry_1_total': m.entry_1_total,
            'entry_2_id': m.entry_2_id,
            'entry_2_name': m.entry_2_name,
            'entry_2_team_name': m.entry_2_team_name,
            'entry_2_seed': m.entry_2_seed,
            'entry_2_points': m.entry_2_points,
            'entry_2_total': m.entry_2_total,
            'winner_id': m.winner_id,
            'is_complete': m.is_complete,
        })
    return bracket
