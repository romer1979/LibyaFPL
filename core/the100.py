# -*- coding: utf-8 -*-
"""
The 100 League - Three Phase Competition

Phase 1: Qualification (GW1-19)
- Top 99 + defending champion qualify (100 total)
- Standings frozen at end of GW19

Phase 2: Elimination (GW20-33)
- 100 qualified managers compete
- 6 managers eliminated each gameweek (bottom 6 by GW points)
- After 14 gameweeks: 100 - (14 × 6) = 16 remain

Phase 3: Championship (GW34-37)
- 16 managers in knockout bracket
- Round of 16, Quarter-finals, Semi-finals, Final
"""

import requests
import os
from datetime import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from config import get_chip_arabic

# Configuration
THE100_LEAGUE_ID = 8921
TIMEOUT = 15

# Phase boundaries
QUALIFICATION_END_GW = 19
ELIMINATION_START_GW = 20
ELIMINATION_END_GW = 33
CHAMPIONSHIP_START_GW = 34
CHAMPIONSHIP_END_GW = 37

# Eliminations per gameweek
ELIMINATIONS_PER_GW = 6

# Last season winner - auto-qualifies regardless of position
WINNER_ENTRY_ID = 49250

# Cache
_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 120,  # 2 minutes
    'qualification_standings': None,  # Frozen GW19 standings
}

# Try to import database models (may not be available in all contexts)
try:
    from models import (
        db, 
        The100QualifiedManager, 
        The100EliminationResult,
        get_the100_qualified_managers,
        save_the100_qualified_managers,
        save_the100_elimination
    )
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False


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


def fetch_multiple_parallel(urls, cookies=None, max_workers=15):
    """Fetch multiple URLs in parallel"""
    results = {}
    if not urls:
        return results
    
    def fetch_one(url):
        try:
            r = requests.get(url, cookies=cookies, timeout=TIMEOUT)
            if r.status_code == 200:
                return url, r.json()
        except:
            pass
        return url, None
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, url): url for url in urls}
        for future in as_completed(futures):
            url, data = future.result()
            if data:
                results[url] = data
    
    return results


def build_player_info(bootstrap):
    """Build player info dictionary"""
    return {
        player['id']: {
            'name': player['web_name'],
            'position': player['element_type'],
            'team': player['team']
        }
        for player in bootstrap.get('elements', [])
    }


def get_qualification_standings(league_id=THE100_LEAGUE_ID):
    """
    Fetch qualification phase standings from FPL API.
    """
    cookies = get_cookies()
    
    # Fetch all standings (paginated)
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
    
    return standings


def calculate_live_points(picks, live_elements, chip=None, transfers_cost=0, player_info=None, fixtures=None):
    """
    Calculate live points for a manager including:
    - Captain/Vice-Captain logic
    - Auto-substitutions
    - Bonus points
    """
    if not picks or not live_elements:
        return 0
    
    # Find captain and vice-captain
    captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
    vice_captain_id = next((p['element'] for p in picks if p.get('is_vice_captain')), None)
    
    captain_data = live_elements.get(captain_id, {})
    captain_played = captain_data.get('minutes', 0) > 0
    
    # Check if captain's team has played
    captain_team_played = True
    if player_info and fixtures and captain_id:
        captain_team = player_info.get(captain_id, {}).get('team')
        if captain_team:
            for f in fixtures:
                if f.get('team_h') == captain_team or f.get('team_a') == captain_team:
                    captain_team_played = f.get('started', False) or f.get('finished', False)
                    break
    
    # Determine which players count (starting 11 or 15 for bench boost)
    players = picks[:15] if chip == 'bboost' else picks[:11]
    
    total_points = 0
    
    for pick in players:
        elem_id = pick['element']
        elem_data = live_elements.get(elem_id, {})
        pts = elem_data.get('total_points', 0)
        
        # Determine multiplier
        if pick.get('is_captain'):
            if captain_played:
                mult = 3 if chip == '3xc' else 2
            elif captain_team_played and not captain_played:
                # Captain didn't play but team did - vice takes over
                mult = 0
            else:
                # Captain's team hasn't played yet
                mult = 1
        elif pick.get('is_vice_captain'):
            if captain_team_played and not captain_played:
                # Vice-captain becomes captain
                mult = 3 if chip == '3xc' else 2
            else:
                mult = 1
        else:
            mult = 1
        
        total_points += pts * mult
    
    # Calculate auto-sub points (only if not bench boost)
    if chip != 'bboost' and player_info and fixtures:
        sub_points = calculate_auto_sub_points(picks, live_elements, player_info, fixtures)
        total_points += sub_points
    
    return total_points - transfers_cost


def calculate_auto_sub_points(picks, live_elements, player_info, fixtures):
    """Calculate points from auto-substitutions"""
    if not picks or len(picks) < 15:
        return 0
    
    def pos_of(eid):
        return player_info.get(eid, {}).get('position', 0)
    
    def team_played(eid):
        team = player_info.get(eid, {}).get('team')
        if not team:
            return True
        for f in fixtures:
            if f.get('team_h') == team or f.get('team_a') == team:
                return f.get('started', False) or f.get('finished', False)
        return False
    
    def formation_ok(d, m, f, g):
        return g == 1 and 3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3
    
    starters = picks[:11]
    bench = picks[11:15]
    
    # Calculate starting formation
    d = sum(1 for p in starters if pos_of(p['element']) == 2)
    m = sum(1 for p in starters if pos_of(p['element']) == 3)
    f = sum(1 for p in starters if pos_of(p['element']) == 4)
    g = sum(1 for p in starters if pos_of(p['element']) == 1)
    
    # Find non-playing starters whose team has played
    non_playing = [
        p for p in starters
        if live_elements.get(p['element'], {}).get('minutes', 0) == 0
        and team_played(p['element'])
    ]
    
    used_bench = set()
    sub_points = 0
    
    for starter in non_playing:
        s_id = starter['element']
        s_pos = pos_of(s_id)
        
        for b in bench:
            b_id = b['element']
            if b_id in used_bench:
                continue
            
            b_pos = pos_of(b_id)
            b_min = live_elements.get(b_id, {}).get('minutes', 0)
            b_played = b_min > 0
            b_team_played = team_played(b_id)
            
            # GK ↔ GK only
            if (s_pos == 1 and b_pos != 1) or (s_pos != 1 and b_pos == 1):
                continue
            
            # Bench player hasn't played and team hasn't played - reserve
            if not b_played and not b_team_played:
                used_bench.add(b_id)
                break
            
            # Bench player hasn't played but team has - skip (DNP)
            if not b_played and b_team_played:
                continue
            
            # Bench player has played - check formation
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
            
            # Accept substitution
            sub_points += live_elements[b_id]['total_points']
            used_bench.add(b_id)
            d, m, f, g = d2, m2, f2, g2
            break
    
    return sub_points


def calculate_projected_bonus(live_data, fixtures):
    """Calculate projected bonus points before official update"""
    bonus_points = {}
    
    for fixture in fixtures:
        if not fixture.get('started', False):
            continue
        
        fixture_id = fixture['id']
        fixture_players = []
        
        for elem in live_data.get('elements', []):
            for exp in elem.get('explain', []):
                if exp.get('fixture') == fixture_id:
                    if elem['stats']['bps'] > 0 or elem['stats']['minutes'] > 0:
                        fixture_players.append({
                            'id': elem['id'],
                            'bps': elem['stats']['bps']
                        })
                    break
        
        # Sort by BPS and assign bonus
        fixture_players.sort(key=lambda x: x['bps'], reverse=True)
        
        if not fixture_players:
            continue
        
        # Assign bonus points (3, 2, 1) handling ties
        position = 1
        i = 0
        while i < len(fixture_players) and position <= 3:
            current_bps = fixture_players[i]['bps']
            
            # Find all players with same BPS
            same_bps = [fixture_players[i]]
            j = i + 1
            while j < len(fixture_players) and fixture_players[j]['bps'] == current_bps:
                same_bps.append(fixture_players[j])
                j += 1
            
            # Assign bonus based on position
            if position == 1:
                bonus = 3
            elif position == 2:
                bonus = 2
            elif position == 3:
                bonus = 1
            else:
                bonus = 0
            
            for player in same_bps:
                bonus_points[player['id']] = bonus
            
            position += len(same_bps)
            i = j
    
    return bonus_points


def get_elimination_standings(current_gw, qualified_managers):
    """
    Calculate elimination phase standings with live GW points.
    """
    cookies = get_cookies()
    
    # Fetch bootstrap data
    bootstrap = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/", cookies)
    if not bootstrap:
        return None
    
    player_info = build_player_info(bootstrap)
    
    # Get fixtures for current GW
    fixtures = fetch_json(f"https://fantasy.premierleague.com/api/fixtures/?event={current_gw}", cookies) or []
    
    # Check if any fixture has started
    gw_started = any(f.get('started', False) for f in fixtures)
    # Check if all fixtures finished
    all_fixtures_finished = all(f.get('finished', False) or f.get('finished_provisional', False) for f in fixtures) if fixtures else False
    
    # Use the shared 24-hour buffer check ONLY for auto-saving eliminations
    from core.fpl_api import is_gameweek_finished as check_gw_finished
    gw_finished_for_save = check_gw_finished(current_gw, fixtures)
    
    # For display: GW is finished when all matches are done
    gw_finished_display = all_fixtures_finished
    
    # Get live data
    live_data = fetch_json(f"https://fantasy.premierleague.com/api/event/{current_gw}/live/", cookies)
    if not live_data:
        return None
    
    # Build live elements dictionary with projected bonus
    live_elements = {}
    bonus_points = calculate_projected_bonus(live_data, fixtures) if gw_started else {}
    
    for elem in live_data['elements']:
        elem_id = elem['id']
        official_bonus = elem['stats'].get('bonus', 0)
        projected_bonus = bonus_points.get(elem_id, 0)
        
        # Use projected bonus if official isn't set yet
        actual_bonus = official_bonus if official_bonus > 0 else projected_bonus
        base_points = elem['stats']['total_points'] - official_bonus
        
        live_elements[elem_id] = {
            'total_points': base_points + actual_bonus,
            'minutes': elem['stats']['minutes'],
            'bonus': actual_bonus
        }
    
    # Get all qualified entry IDs
    entry_ids = [m['entry_id'] for m in qualified_managers]
    
    # Fetch picks for all managers in parallel
    pick_urls = [f"https://fantasy.premierleague.com/api/entry/{eid}/event/{current_gw}/picks/" for eid in entry_ids]
    picks_data = fetch_multiple_parallel(pick_urls, cookies)
    
    # Calculate live points for each manager
    standings = []
    for manager in qualified_managers:
        entry_id = manager['entry_id']
        url = f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{current_gw}/picks/"
        
        gw_data = picks_data.get(url, {})
        picks = gw_data.get('picks', [])
        chip = gw_data.get('active_chip')
        transfers_cost = gw_data.get('entry_history', {}).get('event_transfers_cost', 0)
        
        if gw_started and picks:
            live_gw_points = calculate_live_points(
                picks, live_elements, chip, transfers_cost, player_info, fixtures
            )
        else:
            live_gw_points = gw_data.get('entry_history', {}).get('points', 0)
        
        # Get captain name
        captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
        captain_name = player_info.get(captain_id, {}).get('name', '-') if captain_id else '-'
        
        # Build player picks list with status
        player_picks = []
        if picks:
            # Determine auto-subs
            auto_subbed_in = set()
            auto_subbed_out = set()
            
            if gw_started and chip != 'bboost':
                # Calculate which players got auto-subbed
                starters = picks[:11]
                bench = picks[11:15]
                
                for starter in starters:
                    s_id = starter['element']
                    s_data = live_elements.get(s_id, {})
                    s_team = player_info.get(s_id, {}).get('team')
                    s_team_played = any(
                        (f.get('team_h') == s_team or f.get('team_a') == s_team) and 
                        (f.get('started', False) or f.get('finished', False))
                        for f in fixtures
                    ) if s_team else True
                    
                    # Starter didn't play but team has played
                    if s_data.get('minutes', 0) == 0 and s_team_played:
                        auto_subbed_out.add(s_id)
                        
                        # Find the bench player who came in
                        for b in bench:
                            b_id = b['element']
                            if b_id in auto_subbed_in:
                                continue
                            b_data = live_elements.get(b_id, {})
                            if b_data.get('minutes', 0) > 0:
                                # Check position compatibility (simplified)
                                s_pos = player_info.get(s_id, {}).get('position', 0)
                                b_pos = player_info.get(b_id, {}).get('position', 0)
                                # GK can only be replaced by GK
                                if (s_pos == 1 and b_pos == 1) or (s_pos != 1 and b_pos != 1):
                                    auto_subbed_in.add(b_id)
                                    break
            
            for i, pick in enumerate(picks):
                p_id = pick['element']
                p_info = player_info.get(p_id, {})
                p_data = live_elements.get(p_id, {})
                
                minutes = p_data.get('minutes', 0)
                points = p_data.get('total_points', 0)
                
                # Determine player status
                p_team = p_info.get('team')
                team_started = False
                team_finished = False
                
                for f in fixtures:
                    if f.get('team_h') == p_team or f.get('team_a') == p_team:
                        team_started = f.get('started', False)
                        team_finished = f.get('finished', False) or f.get('finished_provisional', False)
                        break
                
                if minutes > 0:
                    if team_finished:
                        status = 'played'  # Game finished, player played
                    else:
                        status = 'playing'  # Currently on pitch
                elif team_started or team_finished:
                    status = 'benched'  # Team played but player didn't
                else:
                    status = 'pending'  # Team hasn't played yet
                
                # Is this a starter or bench?
                is_starter = i < 11
                is_auto_sub_in = p_id in auto_subbed_in
                is_auto_sub_out = p_id in auto_subbed_out
                
                # Calculate display points
                is_captain = pick.get('is_captain', False)
                is_vice = pick.get('is_vice_captain', False)
                
                # Check if captain's team has played
                captain_team = player_info.get(captain_id, {}).get('team') if captain_id else None
                captain_team_played = False
                if captain_team:
                    for f in fixtures:
                        if f.get('team_h') == captain_team or f.get('team_a') == captain_team:
                            captain_team_played = f.get('started', False) or f.get('finished', False)
                            break
                
                captain_minutes = live_elements.get(captain_id, {}).get('minutes', 0) if captain_id else 0
                
                if minutes > 0 or status == 'benched':
                    if is_captain and minutes > 0:
                        display_points = points * (3 if chip == '3xc' else 2)
                    elif is_vice and captain_team_played and captain_minutes == 0 and minutes > 0:
                        # Vice becomes captain ONLY if captain's team played AND captain didn't play
                        display_points = points * (3 if chip == '3xc' else 2)
                    else:
                        display_points = points
                else:
                    display_points = None  # Will show as "-"
                
                player_picks.append({
                    'id': p_id,
                    'name': p_info.get('name', 'Unknown'),
                    'position': p_info.get('position', 0),
                    'points': display_points,
                    'minutes': minutes,
                    'status': status,
                    'is_captain': is_captain,
                    'is_vice': is_vice,
                    'is_starter': is_starter,
                    'is_auto_sub_in': is_auto_sub_in,
                    'is_auto_sub_out': is_auto_sub_out,
                })
        
        standings.append({
            'entry_id': entry_id,
            'manager_name': manager['manager_name'],
            'team_name': manager['team_name'],
            'qualification_rank': manager['qualification_rank'],
            'qualification_total': manager.get('qualification_total', 0),
            'live_gw_points': live_gw_points,
            'captain': captain_name,
            'chip': chip,
            'is_winner': manager.get('is_winner', False),
            'players': player_picks,
        })
    
    # Sort by GW points (highest first)
    standings.sort(key=lambda x: (-x['live_gw_points'], x['qualification_rank']))
    
    # Assign live ranks
    for i, team in enumerate(standings, 1):
        team['live_rank'] = i
    
    return {
        'standings': standings,
        'gameweek': current_gw,
        'gw_started': gw_started,
        'gw_finished': gw_finished_display,  # For display
        'gw_finished_for_save': gw_finished_for_save,  # For auto-save logic
        'is_live': gw_started and not all_fixtures_finished,
    }


def get_the100_standings(league_id=THE100_LEAGUE_ID):
    """
    Main function to get The 100 standings based on current phase.
    
    - GW1-19: Qualification phase (live FPL standings)
    - GW20-33: Elimination phase (live GW points, bottom 6 eliminated each week)
    - GW34-37: Championship phase (knockout bracket)
    """
    global _cache
    
    now = time.time()
    
    # Return cached data if valid
    if _cache['data'] and (now - _cache['timestamp']) < _cache['ttl']:
        return _cache['data']
    
    try:
        cookies = get_cookies()
        
        # Get current gameweek
        bootstrap = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/", cookies)
        if not bootstrap:
            raise RuntimeError("Failed to fetch bootstrap data")
        
        events = bootstrap["events"]
        current_gw = next((e["id"] for e in events if e.get("is_current")), None)
        if not current_gw:
            finished = [e for e in events if e.get("finished")]
            current_gw = max(finished, key=lambda e: e["id"])["id"] if finished else 1
        
        # Determine current phase
        if current_gw <= QUALIFICATION_END_GW:
            phase = 'qualification'
        elif current_gw <= ELIMINATION_END_GW:
            phase = 'elimination'
        else:
            phase = 'championship'
        
        # ============================================
        # QUALIFICATION PHASE (GW1-19)
        # ============================================
        if phase == 'qualification':
            standings = get_qualification_standings(league_id)
            
            if not standings:
                raise RuntimeError("No standings found")
            
            # Find winner's position
            winner_rank = None
            for row in standings:
                if row.get('entry') == WINNER_ENTRY_ID:
                    winner_rank = row.get('rank', 0)
                    break
            
            # Build standings list
            final_rows = []
            for row in standings:
                entry_id = row.get('entry')
                current_rank = row.get('rank', 0)
                last_rank = row.get('last_rank') or current_rank
                rank_change = last_rank - current_rank
                
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
                'phase': 'qualification',
                'standings': final_rows,
                'gameweek': current_gw,
                'total_managers': len(final_rows),
                'is_live': False,
                'qualification_cutoff': 99,
                'winner_entry_id': WINNER_ENTRY_ID,
                'winner_rank': winner_rank,
                'last_updated': datetime.now().strftime('%H:%M'),
                'phase_info': {
                    'name': 'مرحلة التأهل',
                    'name_en': 'Qualification Phase',
                    'gw_range': f'GW1-{QUALIFICATION_END_GW}',
                    'description': 'أفضل 99 + البطل السابق يتأهلون',
                }
            }
        
        # ============================================
        # ELIMINATION PHASE (GW20-33)
        # ============================================
        elif phase == 'elimination':
            # Try to get qualified managers from database first
            qualified = []
            
            if DB_AVAILABLE:
                try:
                    # Get from database (includes tracking of who's been eliminated)
                    db_managers = The100QualifiedManager.query.filter(
                        The100QualifiedManager.eliminated_gw.is_(None)
                    ).order_by(The100QualifiedManager.qualification_rank).all()
                    
                    if db_managers:
                        qualified = [{
                            'entry_id': m.entry_id,
                            'manager_name': m.manager_name,
                            'team_name': m.team_name,
                            'qualification_rank': m.qualification_rank,
                            'qualification_total': m.qualification_total,
                            'is_winner': m.is_winner
                        } for m in db_managers]
                except Exception as e:
                    print(f"Error fetching from database: {e}")
            
            # If database is empty or not available, fetch from FPL API
            if not qualified:
                qual_standings = get_qualification_standings(league_id)
                
                if not qual_standings:
                    raise RuntimeError("No qualification standings found")
                
                # Determine qualified managers (top 99 + winner)
                winner_in_top_99 = False
                
                # First pass: check if winner is in top 99
                for row in qual_standings:
                    if row.get('entry') == WINNER_ENTRY_ID and row.get('rank', 0) <= 99:
                        winner_in_top_99 = True
                        break
                
                # Second pass: build qualified list
                count_non_winner = 0
                for row in qual_standings:
                    entry_id = row.get('entry')
                    rank = row.get('rank', 0)
                    is_winner = (entry_id == WINNER_ENTRY_ID)
                    
                    # If winner is in top 99, just take top 100
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
                        # Winner not in top 99: take top 99 + winner
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
                
                # Save to database if available and this is the first time
                if DB_AVAILABLE and qualified:
                    try:
                        existing = The100QualifiedManager.query.first()
                        if not existing:
                            save_the100_qualified_managers(qualified)
                    except Exception as e:
                        print(f"Error saving to database: {e}")
            
            # Calculate how many GWs of elimination have passed
            elimination_gws_completed = current_gw - ELIMINATION_START_GW
            total_eliminated = elimination_gws_completed * ELIMINATIONS_PER_GW
            remaining_managers = max(16, 100 - total_eliminated)  # Minimum 16 for championship
            
            # Get elimination standings for current GW
            elim_data = get_elimination_standings(current_gw, qualified)
            
            if elim_data:
                standings = elim_data['standings']
                is_live = elim_data['is_live']
                gw_started = elim_data['gw_started']
                gw_finished = elim_data['gw_finished']
                gw_finished_for_save = elim_data.get('gw_finished_for_save', False)
                
                # AUTO-PROCESS ELIMINATIONS when 24 hours have passed
                if gw_finished_for_save and DB_AVAILABLE and standings:
                    try:
                        # Check if this GW's eliminations have already been processed
                        existing_elim = The100EliminationResult.query.filter_by(
                            gameweek=current_gw
                        ).first()
                        
                        if not existing_elim:
                            # Get bottom 6 (to be eliminated)
                            # But only from managers who haven't been eliminated yet
                            active_standings = [s for s in standings if s.get('live_rank')]
                            
                            if len(active_standings) > ELIMINATIONS_PER_GW:
                                eliminated = active_standings[-ELIMINATIONS_PER_GW:]
                                
                                eliminated_list = [{
                                    'entry_id': m['entry_id'],
                                    'manager_name': m['manager_name'],
                                    'team_name': m['team_name'],
                                    'gw_points': m['live_gw_points'],
                                    'gw_rank': m['live_rank']
                                } for m in eliminated]
                                
                                save_the100_elimination(current_gw, eliminated_list)
                                print(f"Auto-processed GW{current_gw} eliminations: {[m['manager_name'] for m in eliminated_list]}")
                    except Exception as e:
                        print(f"Error auto-processing eliminations: {e}")
            else:
                standings = qualified
                is_live = False
                gw_started = False
                gw_finished = False
            
            # Mark elimination zone (bottom 6)
            safe_count = remaining_managers - ELIMINATIONS_PER_GW
            elimination_zone_start = safe_count + 1
            
            for team in standings:
                rank = team.get('live_rank', 0)
                team['in_elimination_zone'] = rank >= elimination_zone_start
                team['is_safe'] = rank <= safe_count
            
            result = {
                'phase': 'elimination',
                'standings': standings,
                'gameweek': current_gw,
                'total_managers': len(standings),
                'remaining_managers': remaining_managers,
                'is_live': is_live,
                'gw_started': gw_started,
                'gw_finished': gw_finished,
                'safe_count': safe_count,
                'elimination_zone_start': elimination_zone_start,
                'eliminations_per_gw': ELIMINATIONS_PER_GW,
                'total_eliminated': total_eliminated,
                'gws_remaining': ELIMINATION_END_GW - current_gw,
                'winner_entry_id': WINNER_ENTRY_ID,
                'last_updated': datetime.now().strftime('%H:%M'),
                'phase_info': {
                    'name': 'مرحلة الإقصاء',
                    'name_en': 'Elimination Phase',
                    'gw_range': f'GW{ELIMINATION_START_GW}-{ELIMINATION_END_GW}',
                    'description': f'{ELIMINATIONS_PER_GW} يخرجون كل جولة',
                }
            }
        
        # ============================================
        # CHAMPIONSHIP PHASE (GW34-37)
        # ============================================
        else:
            # Championship bracket - to be implemented
            result = {
                'phase': 'championship',
                'standings': [],
                'gameweek': current_gw,
                'total_managers': 16,
                'is_live': False,
                'winner_entry_id': WINNER_ENTRY_ID,
                'last_updated': datetime.now().strftime('%H:%M'),
                'phase_info': {
                    'name': 'مرحلة البطولة',
                    'name_en': 'Championship Phase',
                    'gw_range': f'GW{CHAMPIONSHIP_START_GW}-{CHAMPIONSHIP_END_GW}',
                    'description': '16 متنافس في نظام خروج المغلوب',
                },
                'bracket': {
                    'round_of_16': [],
                    'quarter_finals': [],
                    'semi_finals': [],
                    'final': []
                }
            }
        
        # Cache the result
        _cache['data'] = result
        _cache['timestamp'] = now
        
        return result
        
    except Exception as e:
        print(f"Error fetching The 100 standings: {e}")
        import traceback
        traceback.print_exc()
        
        # Return cached data if available
        if _cache['data']:
            return _cache['data']
        
        return {
            'phase': 'unknown',
            'standings': [],
            'gameweek': None,
            'total_managers': 0,
            'is_live': False,
            'error': str(e)
        }


def get_the100_stats():
    """
    Get statistics for The 100 league
    """
    try:
        # Get current standings data
        standings_data = get_the100_standings()
        
        if not standings_data or standings_data.get('error'):
            return {
                'success': False,
                'error': standings_data.get('error', 'Failed to fetch standings')
            }
        
        standings = standings_data.get('standings', [])
        phase = standings_data.get('phase', 'unknown')
        gameweek = standings_data.get('gameweek')
        is_live = standings_data.get('is_live', False)
        
        if not standings:
            return {
                'success': False,
                'error': 'No standings data available'
            }
        
        # Get bootstrap data for player info
        bootstrap_data = get_bootstrap_data()
        player_info = build_player_info(bootstrap_data)
        
        # Initialize collectors
        gw_points = []
        manager_points = {}
        captains = []
        chips_used = []
        player_ownership = Counter()
        
        for team in standings:
            manager_name = team.get('manager_name', 'Unknown')
            points = team.get('live_gw_points', 0)
            
            gw_points.append(points)
            manager_points[manager_name] = points
            
            # Captain
            captain_name = team.get('captain', '-')
            if captain_name and captain_name != '-':
                captains.append({
                    'manager': manager_name,
                    'captain_name': captain_name
                })
            
            # Chips
            chip = team.get('chip')
            if chip:
                chips_used.append({
                    'manager': manager_name,
                    'chip': chip,
                    'chip_ar': get_chip_arabic(chip)
                })
            
            # Player ownership from players list
            players = team.get('players', [])
            for i, player in enumerate(players):
                if i >= 11 and not player.get('is_auto_sub_in'):
                    continue  # Skip bench players unless they auto-subbed in
                
                p_id = player.get('id')
                if not p_id:
                    continue
                
                if player.get('is_captain'):
                    if chip == '3xc':
                        player_ownership[p_id] += 3
                    else:
                        player_ownership[p_id] += 2
                else:
                    player_ownership[p_id] += 1
        
        # Calculate captain stats
        captain_counts = Counter([c['captain_name'] for c in captains])
        captain_stats = [
            {'name': name, 'count': count}
            for name, count in captain_counts.most_common()
        ]
        
        # Calculate points stats
        if gw_points:
            n = len(gw_points)
            min_points = min(gw_points)
            max_points = max(gw_points)
            
            min_managers = [name for name, pts in manager_points.items() if pts == min_points]
            max_managers = [name for name, pts in manager_points.items() if pts == max_points]
            
            points_stats = {
                'min': min_points,
                'min_managers': min_managers,
                'max': max_points,
                'max_managers': max_managers,
                'avg': round(sum(gw_points) / n, 1),
                'total_managers': n
            }
        else:
            points_stats = {
                'min': 0, 'min_managers': [],
                'max': 0, 'max_managers': [],
                'avg': 0, 'total_managers': 0
            }
        
        # Calculate effective ownership (top 15 players)
        effective_ownership = []
        total_managers = len(standings)
        
        for element_id, count in player_ownership.most_common(15):
            player = player_info.get(element_id, {})
            team_id = player.get('team', 0)
            team_name = ''
            for t in bootstrap_data.get('teams', []):
                if t['id'] == team_id:
                    team_name = t['short_name']
                    break
            
            percentage = round((count / total_managers) * 100, 1) if total_managers > 0 else 0
            
            effective_ownership.append({
                'name': player.get('name', 'Unknown'),
                'team': team_name,
                'count': count,
                'percentage': percentage
            })
        
        # Elimination phase specific stats
        elimination_stats = None
        if phase == 'elimination':
            remaining = standings_data.get('remaining_managers', 100)
            eliminated_count = 100 - remaining
            gws_remaining = standings_data.get('gws_remaining', 0)
            
            # Get managers in danger zone
            danger_zone = [
                team['manager_name'] 
                for team in standings 
                if team.get('in_elimination_zone')
            ]
            
            elimination_stats = {
                'remaining': remaining,
                'eliminated': eliminated_count,
                'gws_remaining': gws_remaining,
                'danger_zone': danger_zone
            }
        
        return {
            'success': True,
            'gameweek': gameweek,
            'phase': phase,
            'is_live': is_live,
            'captain_stats': captain_stats,
            'chips_used': chips_used,
            'points_stats': points_stats,
            'effective_ownership': effective_ownership,
            'total_managers': total_managers,
            'elimination_stats': elimination_stats,
            'last_updated': standings_data.get('last_updated')
        }
        
    except Exception as e:
        print(f"Error getting The 100 stats: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'error': str(e)
        }
