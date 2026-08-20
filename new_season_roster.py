# -*- coding: utf-8 -*-
"""
New-season roster helper.

Given a (new) FPL league ID, lists every member of the league:
    entry_id | manager name | FPL team name

Use it to collect the new-season entry IDs for TEAMS_FPL_IDS in
core/cities_league.py, core/arab_league.py, core/libyan_league.py.
The FPL API can't tell us which 3 managers belong to which of your
teams — that's your grouping — but this gives you all IDs + names
in one place so you don't have to look up 60 profiles by hand.

Works before GW1: pre-season, league members appear in the API's
`new_entries` list instead of `standings`; both are read. Tries the
H2H endpoint first, then falls back to classic (for The 100 style
leagues).

Standalone — no DB needed, runs anywhere with `requests`.

Usage:
    python new_season_roster.py <league_id>
    python new_season_roster.py 1231867
"""

import sys
import time
import requests

TIMEOUT = 15
FPL_BASE = "https://fantasy.premierleague.com/api"


def fetch_json(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(2)
        except Exception as e:
            print(f"  fetch error ({attempt+1}/{retries}): {e}")
        time.sleep(1)
    return None


def collect_members(league_id, kind):
    """kind: 'h2h' or 'classic'. Returns list of dicts or None if league not found."""
    base = f"{FPL_BASE}/leagues-{kind}/{league_id}/standings/"
    members = {}
    league_name = None

    # 1) new_entries (pre-season / recently joined)
    page = 1
    while True:
        data = fetch_json(f"{base}?page_new_entries={page}")
        if data is None:
            return None, None
        league_name = data.get('league', {}).get('name', league_name)
        ne = data.get('new_entries', {}) or {}
        for e in ne.get('results', []):
            name = f"{e.get('player_first_name', '')} {e.get('player_last_name', '')}".strip()
            members[e['entry']] = {'entry': e['entry'], 'name': name,
                                   'team': e.get('entry_name', '')}
        if not ne.get('has_next'):
            break
        page += 1

    # 2) standings (after GW1)
    page = 1
    while True:
        data = fetch_json(f"{base}?page_standings={page}")
        if data is None:
            break
        st = data.get('standings', {}) or {}
        for e in st.get('results', []):
            members[e['entry']] = {'entry': e['entry'],
                                   'name': e.get('player_name', ''),
                                   'team': e.get('entry_name', '')}
        if not st.get('has_next'):
            break
        page += 1

    return list(members.values()), league_name


def main():
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        print("Usage: python new_season_roster.py <league_id>")
        return
    league_id = int(sys.argv[1])

    print(f"Fetching league {league_id}...")
    members, league_name = collect_members(league_id, 'h2h')
    kind = 'h2h'
    if members is None:
        members, league_name = collect_members(league_id, 'classic')
        kind = 'classic'
    if members is None:
        print(f"League {league_id} not found (tried h2h and classic endpoints).")
        return

    print(f"\nLeague: {league_name or '?'}  [{kind}]  — {len(members)} member(s)\n")
    print(f"  {'entry_id':>10}  {'manager':30s}  team name")
    print("  " + "-" * 70)
    for m in sorted(members, key=lambda x: x['name']):
        print(f"  {m['entry']:>10}  {m['name'][:30]:30s}  {m['team']}")

    print(f"\n{len(members)} members. To build TEAMS_FPL_IDS, group these into "
          f"teams of 3:\n")
    print('    "TEAM_NAME": [entry_id, entry_id, entry_id],')


if __name__ == '__main__':
    main()
