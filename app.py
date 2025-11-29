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
    """Home page showing all leagues"""
    # Get Elite League standings for top 10
    elite_data = get_dashboard()
    elite_standings = []
    
    if elite_data.get('success') and elite_data.get('standings'):
        gameweek = elite_data.get('gameweek', 1)
        
        for team in elite_data['standings']:
            entry_id = team.get('entry_id')
            current_rank = team.get('rank', 0)
            rank_change = calculate_rank_change(gameweek, entry_id, current_rank)
            team['rank_change'] = rank_change
        
        elite_standings = elite_data['standings']
        
        # Save standings if live or finished
        if elite_data.get('gw_finished') or elite_data.get('is_live'):
            save_standings(gameweek, elite_standings)
    
    return render_template('home.html', elite_standings=elite_standings)


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
    return render_template('the100_dashboard.html')


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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=False, host='0.0.0.0', port=port)
