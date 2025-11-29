# -*- coding: utf-8 -*-
"""
The 100 League - Simple Standings with Winner Exception
No picks fetching - fast and memory efficient
"""

import requests
import os
from datetime import datetime
import time

# Configuration
THE100_LEAGUE_ID = 8921
TIMEOUT = 15

# Last season winner - auto-qualifies regardless of position
WINNER_ENTRY_ID = 49250

# Simple cache
_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 120  # Cache for 2 minutes
}

def get_cookies():
    return {
        'sessionid': os.environ.get('FPL_SESSION_ID', ''),
        'csrftoken': os.environ.get('FPL_CSRF_TOKEN', '')
    }

def fetch_json(url, cookies=None):
    """Simple fetch with timeout"""
    try:
        r = requests.get(url, cookies=cookies, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"Fetch error: {e}")
        return None

def get_the100_standings(league_id=THE100_LEAGUE_ID):
    """Fetch official FPL standings with winner exception"""
    global _cache
    
    now = time.time()
    
    # Return cached data if valid
    if _cache['data'] and (now - _cache['timestamp']) < _cache['ttl']:
        return _cache['data']
    
    try:
        cookies = get_cookies()
        
        # 1) Get current gameweek
        bootstrap = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/", cookies)
        if not bootstrap:
            raise RuntimeError("Failed to fetch bootstrap data")
        
        events = bootstrap["events"]
        current_gw = next((e["id"] for e in events if e.get("is_current")), None)
        if not current_gw:
            finished = [e for e in events if e.get("finished")]
            current_gw = max(finished, key=lambda e: e["id"])["id"] if finished else 1
        
        # 2) Fetch all standings (paginated)
        standings = []
        page = 1
        while True:
            url = f"https://fantasy.premierleague.com/api/leagues-classic/{league_id}/standings/?page_standings={page}"
            data = fetch_json(url, cookies)
            if not data:
                break
            
            block = data.get("standings", {})
            rows = block.get("results", [])
            standings.extend(rows)
            
            if not block.get("has_next"):
                break
            page += 1
        
        if not standings:
            raise RuntimeError("No standings found")
        
        # 3) Find winner's position
        winner_rank = None
        for row in standings:
            if row.get('entry') == WINNER_ENTRY_ID:
                winner_rank = row.get('rank', 0)
                break
        
        # 4) Qualification cutoff is always 99 (winner is separate)
        qualification_cutoff = 99
        
        # 5) Build standings list
        final_rows = []
        for row in standings:
            entry_id = row.get('entry')
            current_rank = row.get('rank', 0)
            last_rank = row.get('last_rank') or current_rank
            rank_change = last_rank - current_rank
            
            # Check if this is the winner (auto-qualifier)
            is_winner = (entry_id == WINNER_ENTRY_ID)
            
            final_rows.append({
                'live_rank': current_rank,
                'manager_name': row.get('player_name', ''),
                'team_name': row.get('entry_name', ''),
                'live_total': row.get('total', 0),
                'live_gw_points': row.get('event_total', 0),
                'rank_change': rank_change,
                'entry_id': entry_id,
                'is_winner': is_winner
            })
        
        result = {
            'standings': final_rows,
            'gameweek': current_gw,
            'total_managers': len(final_rows),
            'is_live': False,
            'qualification_cutoff': qualification_cutoff,
            'winner_entry_id': WINNER_ENTRY_ID,
            'winner_rank': winner_rank,
            'last_updated': datetime.now().strftime('%H:%M')
        }
        
        # Cache the result
        _cache['data'] = result
        _cache['timestamp'] = now
        
        return result
        
    except Exception as e:
        print(f"Error fetching The 100 standings: {e}")
        # Return cached data if available
        if _cache['data']:
            return _cache['data']
        return {
            'standings': [],
            'gameweek': None,
            'total_managers': 0,
            'is_live': False,
            'qualification_cutoff': 100,
            'error': str(e)
        }
