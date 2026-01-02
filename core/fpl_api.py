# -*- coding: utf-8 -*-
"""
FPL API Utility Functions - Optimized with caching and parallel requests
"""

import requests
from time import sleep, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from config import FPL_BASE_URL, COOKIES

# Session for connection pooling (reuses connections)
_session = None

def get_session():
    """Get or create a requests session with connection pooling"""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.cookies.update(COOKIES)
        # Enable connection pooling
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=3
        )
        _session.mount('https://', adapter)
    return _session


class FPLApiError(Exception):
    """Custom exception for FPL API errors"""
    pass


class GameweekNotStartedError(Exception):
    """Exception raised when gameweek hasn't started yet"""
    pass


# Simple time-based cache
_cache = {}
_cache_ttl = {}
CACHE_DURATION = 30  # seconds


def get_cached(key):
    """Get value from cache if not expired"""
    if key in _cache:
        if time() - _cache_ttl.get(key, 0) < CACHE_DURATION:
            return _cache[key]
    return None


def set_cached(key, value):
    """Set value in cache"""
    _cache[key] = value
    _cache_ttl[key] = time()


def clear_cache():
    """Clear the cache"""
    global _cache, _cache_ttl
    _cache = {}
    _cache_ttl = {}


def fetch_data(url, cookies=None, retries=2, timeout=8):
    """Fetch data from the FPL API with connection pooling"""
    # Check cache first
    cached = get_cached(url)
    if cached is not None:
        return cached
    
    session = get_session()
    
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                set_cached(url, data)
                return data
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                sleep(0.5)
    
    raise FPLApiError(f"Failed to fetch data from {url}")


def fetch_multiple_parallel(urls, max_workers=15):
    """Fetch multiple URLs in parallel - MUCH faster"""
    results = {}
    
    if not urls:
        return results
    
    session = get_session()
    
    def fetch_one(url):
        # Check cache first
        cached = get_cached(url)
        if cached is not None:
            return url, cached
        
        try:
            response = session.get(url, timeout=8)
            if response.status_code == 200:
                data = response.json()
                set_cached(url, data)
                return url, data
        except Exception as e:
            pass
        return url, None
    
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_one, url): url for url in urls}
            for future in as_completed(futures):
                try:
                    url, data = future.result()
                    if data:
                        results[url] = data
                except Exception:
                    pass
    except Exception as e:
        print(f"Parallel fetch error: {e}")
    
    return results


def get_bootstrap_data():
    """Fetch main bootstrap static data (cached)"""
    url = f"{FPL_BASE_URL}/bootstrap-static/"
    return fetch_data(url)


def get_current_gameweek(bootstrap_data=None):
    """Get the current gameweek info"""
    if bootstrap_data is None:
        bootstrap_data = get_bootstrap_data()
    
    events = bootstrap_data.get("events", [])
    
    # Find current gameweek
    current_event = next((e for e in events if e.get("is_current")), None)
    
    if current_event:
        return {
            'id': current_event['id'],
            'name': current_event.get('name', f"Gameweek {current_event['id']}"),
            'is_current': True,
            'finished': current_event.get('finished', False),
            'data_checked': current_event.get('data_checked', False),
            'deadline_time': current_event.get('deadline_time'),
            'not_started': False
        }
    
    # Check for next gameweek
    next_event = next((e for e in events if e.get("is_next")), None)
    if next_event:
        return {
            'id': next_event['id'],
            'name': next_event.get('name', f"Gameweek {next_event['id']}"),
            'is_current': False,
            'finished': False,
            'not_started': True,
            'deadline_time': next_event.get('deadline_time')
        }
    
    # Fallback
    if events:
        last_event = events[-1]
        return {
            'id': last_event['id'],
            'finished': True,
            'not_started': False
        }
    
    raise FPLApiError("Could not determine current gameweek")


def get_live_data(gameweek):
    """Fetch live data for a specific gameweek"""
    url = f"{FPL_BASE_URL}/event/{gameweek}/live/"
    return fetch_data(url)


def get_fixtures(gameweek):
    """Fetch fixtures for a specific gameweek"""
    url = f"{FPL_BASE_URL}/fixtures/?event={gameweek}"
    return fetch_data(url)


def get_league_standings(league_id):
    """Fetch H2H league standings"""
    url = f"{FPL_BASE_URL}/leagues-h2h/{league_id}/standings/"
    return fetch_data(url)


def get_league_matches(league_id, gameweek):
    """Fetch H2H matches for a league in a specific gameweek"""
    url = f"{FPL_BASE_URL}/leagues-h2h-matches/league/{league_id}/?event={gameweek}"
    return fetch_data(url)


def get_entry_data(entry_id):
    """Fetch entry (team) data"""
    url = f"{FPL_BASE_URL}/entry/{entry_id}/"
    return fetch_data(url)


def get_entry_picks(entry_id, gameweek):
    """Fetch picks for an entry in a specific gameweek"""
    url = f"{FPL_BASE_URL}/entry/{entry_id}/event/{gameweek}/picks/"
    return fetch_data(url)


def get_multiple_entry_data(entry_ids):
    """Fetch multiple entry data in parallel"""
    urls = [f"{FPL_BASE_URL}/entry/{eid}/" for eid in entry_ids]
    results = fetch_multiple_parallel(urls)
    return {
        int(url.split('/entry/')[1].rstrip('/')): data 
        for url, data in results.items()
    }


def get_multiple_entry_picks(entry_ids, gameweek):
    """Fetch multiple entry picks in parallel - THE BIG OPTIMIZATION"""
    urls = [f"{FPL_BASE_URL}/entry/{eid}/event/{gameweek}/picks/" for eid in entry_ids]
    results = fetch_multiple_parallel(urls)
    return {
        int(url.split('/entry/')[1].split('/event/')[0]): data 
        for url, data in results.items()
    }


def get_multiple_entry_history(entry_ids):
    """Fetch multiple entry histories in parallel"""
    urls = [f"{FPL_BASE_URL}/entry/{eid}/history/" for eid in entry_ids]
    results = fetch_multiple_parallel(urls)
    return {
        int(url.split('/entry/')[1].split('/history')[0]): data 
        for url, data in results.items()
    }


def build_player_info(bootstrap_data):
    """Build player info dictionary"""
    return {
        player['id']: {
            'name': player['web_name'],
            'status': player['status'],
            'position': player['element_type'],
            'team': player['team']
        }
        for player in bootstrap_data.get('elements', [])
    }


def check_any_fixture_started(gameweek):
    """Check if any fixture in the gameweek has started"""
    try:
        fixtures = get_fixtures(gameweek)
        return any(f.get('started', False) for f in fixtures)
    except:
        return False


def is_gameweek_finished(gameweek, fixtures=None):
    """
    Check if a gameweek is truly finished.
    Returns True only if:
    1. All fixtures have finished
    2. At least 24 hours have passed since the last match ended
    
    This ensures bonus points and corrections are finalized.
    """
    from datetime import datetime, timezone, timedelta
    
    if fixtures is None:
        try:
            fixtures = get_fixtures(gameweek)
        except:
            return False
    
    if not fixtures:
        return False
    
    # Check if all fixtures have finished
    all_finished = all(
        f.get('finished', False) or f.get('finished_provisional', False) 
        for f in fixtures
    )
    
    if not all_finished:
        return False
    
    # Find the latest kickoff time
    latest_kickoff = None
    for f in fixtures:
        kickoff_str = f.get('kickoff_time')
        if kickoff_str:
            try:
                # Parse kickoff time (format: 2024-01-13T14:00:00Z)
                kickoff = datetime.fromisoformat(kickoff_str.replace('Z', '+00:00'))
                if latest_kickoff is None or kickoff > latest_kickoff:
                    latest_kickoff = kickoff
            except:
                pass
    
    if latest_kickoff is None:
        return False
    
    # Add ~2.5 hours for match duration + 24 hours buffer = 26.5 hours
    # This ensures bonus points and corrections are finalized
    finish_threshold = latest_kickoff + timedelta(hours=26, minutes=30)
    now_utc = datetime.now(timezone.utc)
    
    return now_utc >= finish_threshold
