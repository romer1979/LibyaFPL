# -*- coding: utf-8 -*-
"""
Shared scoring for the team-based leagues (cities / arab / libyan).

House rules, which differ from standard FPL:
  - Captain counts 2x, and Triple Captain also counts only 2x
  - Bench Boost is ignored: only the starting XI (plus auto-subs) scores
  - Transfer hits are subtracted

Extracted from the old fix_gw24_libyan.py so the audit and repair tools
share one implementation and no longer depend on a one-off fix script that
carried a stale roster.
"""

import requests
import time

TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY = 2


def fetch_json(url, retries=MAX_RETRIES):
    """Fetch JSON with retries"""
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                print(f"  Rate limited, waiting {RETRY_DELAY * 2}s...")
                time.sleep(RETRY_DELAY * 2)
            else:
                print(f"  HTTP {r.status_code} for {url}")
        except Exception as e:
            print(f"  Error: {e}")

        if attempt < retries - 1:
            time.sleep(RETRY_DELAY)

    return None


def get_bootstrap_data():
    return fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/")


def get_live_data(gameweek):
    return fetch_json(f"https://fantasy.premierleague.com/api/event/{gameweek}/live/")


def get_picks(entry_id, gameweek):
    return fetch_json(f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gameweek}/picks/")


def build_player_info(bootstrap):
    return {
        p['id']: {
            'name': p['web_name'],
            'team': p['team'],
            'position': p['element_type'],
        }
        for p in bootstrap.get('elements', [])
    }


def build_live_elements(live_data):
    return {
        elem['id']: {
            'total_points': elem['stats']['total_points'],
            'minutes': elem['stats']['minutes'],
        }
        for elem in live_data.get('elements', [])
    }


def calculate_auto_subs(picks, live_elements, player_info):
    """Calculate auto-sub points"""
    def pos_of(eid):
        return player_info.get(eid, {}).get('position', 0)

    def formation_ok(d, m, f, g):
        return (g == 1 and 3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3)

    starters = picks[:11]
    bench = picks[11:]

    d = sum(1 for p in starters if pos_of(p['element']) == 2)
    m = sum(1 for p in starters if pos_of(p['element']) == 3)
    f = sum(1 for p in starters if pos_of(p['element']) == 4)
    g = sum(1 for p in starters if pos_of(p['element']) == 1)

    non_playing = [p for p in starters if live_elements.get(p['element'], {}).get('minutes', 0) == 0]

    used = set()
    sub_points = 0

    for starter in non_playing:
        s_id = starter['element']
        s_pos = pos_of(s_id)

        for b in bench:
            b_id = b['element']
            if b_id in used:
                continue

            b_pos = pos_of(b_id)
            b_min = live_elements.get(b_id, {}).get('minutes', 0)

            if (s_pos == 1 and b_pos != 1) or (s_pos != 1 and b_pos == 1):
                continue

            if b_min == 0:
                continue

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

            sub_points += live_elements.get(b_id, {}).get('total_points', 0)
            used.add(b_id)
            d, m, f, g = d2, m2, f2, g2
            break

    return sub_points


def calculate_manager_points(picks_data, live_elements, player_info):
    """Calculate manager points using custom rules"""
    if not picks_data:
        return 0

    picks = picks_data.get('picks', [])
    hits = picks_data.get('entry_history', {}).get('event_transfers_cost', 0)

    if not picks:
        return 0

    captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
    captain_minutes = live_elements.get(captain_id, {}).get('minutes', 0) if captain_id else 0
    captain_played = captain_minutes > 0

    total = 0
    for pick in picks[:11]:
        pid = pick['element']
        pts = live_elements.get(pid, {}).get('total_points', 0)

        if pick.get('is_captain'):
            pts = pts * 2 if captain_played else 0
        elif pick.get('is_vice_captain') and not captain_played:
            vc_min = live_elements.get(pid, {}).get('minutes', 0)
            if vc_min > 0:
                pts *= 2

        total += pts

    total += calculate_auto_subs(picks, live_elements, player_info)
    return total - hits


