# -*- coding: utf-8 -*-
"""
Unified Dashboard Module
Combines standings and live points in a single view with smart switching
"""

import pandas as pd
from datetime import datetime
from collections import Counter
from core.fpl_api import (
    get_bootstrap_data,
    get_current_gameweek,
    get_live_data,
    get_fixtures,
    get_league_standings,
    get_league_matches,
    get_entry_data,
    get_entry_picks,
    get_multiple_entry_data,
    get_multiple_entry_picks,
    build_player_info,
    check_any_fixture_started,
    FPLApiError,
    GameweekNotStartedError
)
from config import LEAGUE_ID, POSTPONED_GAMES, EXCLUDED_PLAYERS, get_chip_arabic, is_chip_active


class DashboardData:
    """Main class to fetch and process all dashboard data"""
    
    def __init__(self, league_id=None):
        self.league_id = league_id or LEAGUE_ID
        self.bootstrap_data = None
        self.player_info = None
        self.fixtures = None
        self.live_elements_dict = None
        self.current_gameweek = None
        self.display_gameweek = None
        self.fixtures_gameweek = None
        self.gw_info = None
        self.team_fixture_started = {}
        self.is_live = False
        self.gw_finished = False
        self.showing_previous_gw = False
        self.fixtures_started = False
    
    def _initialize_base_data(self):
        """Initialize base data from bootstrap"""
        self.bootstrap_data = get_bootstrap_data()
        self.player_info = build_player_info(self.bootstrap_data)
        self.gw_info = get_current_gameweek(self.bootstrap_data)
        self.current_gameweek = self.gw_info['id']
        self.display_gameweek = self.current_gameweek
        self.gw_finished = self.gw_info.get('finished', False) and self.gw_info.get('data_checked', False)
        
        # Check if any fixture in current GW has started
        self.fixtures_started = check_any_fixture_started(self.current_gameweek)
        
        # Also check if ALL fixtures are finished (backup check)
        if not self.gw_finished and self.fixtures_started:
            fixtures = get_fixtures(self.current_gameweek)
            all_finished = all(f.get('finished', False) or f.get('finished_provisional', False) for f in fixtures) if fixtures else False
            if all_finished:
                self.gw_finished = True
    
    def _initialize_live_data(self):
        """Initialize live data for current gameweek"""
        self.is_live = True
        live_data = get_live_data(self.current_gameweek)
        self.fixtures = get_fixtures(self.current_gameweek)
        
        for fixture in self.fixtures:
            started = fixture.get('started', False)
            self.team_fixture_started[fixture['team_h']] = self.team_fixture_started.get(fixture['team_h'], False) or started
            self.team_fixture_started[fixture['team_a']] = self.team_fixture_started.get(fixture['team_a'], False) or started
        
        self.live_elements_dict = {
            elem['id']: {
                'total_points': elem['stats']['total_points'],
                'minutes': elem['stats']['minutes'],
                'bps': elem['stats']['bps'],
                'bonus': elem['stats'].get('bonus', 0),
                'explain': elem.get('explain', [])
            }
            for elem in live_data['elements']
        }
        
        # Build per-fixture BPS lookup from fixtures data for DGW support
        self.fixture_bps = {}
        for fix in self.fixtures:
            fix_id = fix.get('id')
            if fix_id is None:
                continue
            self.fixture_bps[fix_id] = {}
            for stat_group in fix.get('stats', []):
                if stat_group.get('identifier') == 'bps':
                    for entry in stat_group.get('h', []):
                        self.fixture_bps[fix_id][entry['element']] = entry['value']
                    for entry in stat_group.get('a', []):
                        self.fixture_bps[fix_id][entry['element']] = entry['value']

        self._calculate_and_apply_bonus(live_data)

    def _calculate_and_apply_bonus(self, live_data):
        """Calculate projected bonus points (DGW-safe: uses per-fixture BPS)"""
        players = []
        for player_data in live_data['elements']:
            player_id = player_data['id']
            minutes = player_data['stats']['minutes']

            for fixture_info in player_data.get('explain', []):
                fixture_id = fixture_info['fixture']
                player_fix_bps = self.fixture_bps.get(fixture_id, {}).get(player_id, 0)
                player_fix_mins = any(
                    s.get('value', 0) > 0
                    for s in fixture_info.get('stats', [])
                    if s.get('identifier') == 'minutes'
                )
                if player_fix_bps > 0 or player_fix_mins:
                    players.append({
                        'player_id': player_id,
                        'fixture_id': fixture_id,
                        'bps': player_fix_bps,
                        'total_points': player_data['stats']['total_points'],
                        'bonus': 0
                    })
        
        if not players:
            return
        
        df = pd.DataFrame(players)
        df = df.groupby('fixture_id', group_keys=False).apply(self._assign_bonus_points)
        
        bonus_points = df.set_index('player_id')['bonus'].to_dict()
        for player_id, stats in self.live_elements_dict.items():
            new_bonus = bonus_points.get(player_id, 0)
            stats['total_points'] += new_bonus - stats.get('bonus', 0)
            stats['bonus'] = new_bonus
    
    @staticmethod
    def _assign_bonus_points(group):
        """Assign bonus points based on BPS"""
        group = group.copy()
        group['bonus'] = 0
        group = group.sort_values(by='bps', ascending=False)
        unique_bps = group['bps'].unique()
        position = 1
        
        for bps_score in unique_bps:
            if position > 3:
                break
            players = group[group['bps'] == bps_score]
            num = len(players)
            
            if position == 1:
                group.loc[players.index, 'bonus'] = 3
                position += 2 if num > 1 else 1
            elif position == 2:
                group.loc[players.index, 'bonus'] = 2
                position = min(position + num, 4)
            elif position == 3:
                group.loc[players.index, 'bonus'] = 1
                position += 1
        
        return group
    
    def _is_game_complete_or_postponed(self, team_id):
        """Check if team's game is complete or postponed (started OR postponed)"""
        if team_id in POSTPONED_GAMES:
            return True
        # Check team_fixture_started first
        if self.team_fixture_started.get(team_id, False):
            return True
        for fixture in self.fixtures:
            if fixture['team_h'] == team_id or fixture['team_a'] == team_id:
                started = fixture.get('started', False)
                is_postponed = fixture.get('kickoff_time') is None
                return started or is_postponed
        return False
    
    def _are_all_team_fixtures_complete_or_postponed(self, team_id):
        """Check if all of a team's fixtures are complete or postponed"""
        team_fixtures = [f for f in self.fixtures if f['team_h'] == team_id or f['team_a'] == team_id]
        if not team_fixtures:
            return True
        for fixture in team_fixtures:
            if not (fixture.get('started', False) or fixture.get('kickoff_time') is None):
                return False
        return True
    
    def _calculate_sub_points(self, picks):
        """
        FPL auto-subs (live/expected):
          - For each non-playing starter (XI order), scan bench in order.
          - If a bench player has not played AND his team hasn't started/postponed -> RESERVE him for this starter and stop scanning (adds 0 now).
          - If a bench player has not played AND his team has started/postponed -> reject (DNP).
          - If a bench player has played -> test GK↔GK rule and formation; accept first valid one.
          - Formation after swap must be: GK=1, DEF 3–5, MID 2–5, FWD 1–3.
          - A bench player can be used/reserved at most once.
        Returns total points currently added by accepted bench subs.
        """
        def pos_of(eid):
            return self.player_info[eid]['position']
        
        def formation_ok(d, m, f, g):
            return g == 1 and 3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3
        
        def team_done(eid):
            return self._are_all_team_fixtures_complete_or_postponed(self.player_info[eid]['team'])
        
        starters = picks[:11]
        bench = picks[11:]
        
        # Baseline formation from original XI
        d = sum(1 for p in starters if pos_of(p['element']) == 2)
        m = sum(1 for p in starters if pos_of(p['element']) == 3)
        f = sum(1 for p in starters if pos_of(p['element']) == 4)
        g = sum(1 for p in starters if pos_of(p['element']) == 1)
        
        # Non-playing starters eligible for auto-sub (their team's game started/postponed)
        non_playing = [
            p for p in starters
            if self.live_elements_dict.get(p['element'], {}).get('minutes', 0) == 0
            and team_done(p['element'])
        ]
        
        used_bench_ids = set()  # includes both accepted AND reserved bench players
        sub_points = 0
        
        for starter in non_playing:
            s_id = starter['element']
            s_pos = pos_of(s_id)
            
            for b in bench:
                b_id = b['element']
                if b_id in used_bench_ids:
                    continue
                
                b_pos = pos_of(b_id)
                b_min = self.live_elements_dict.get(b_id, {}).get('minutes', 0)
                b_played = b_min > 0
                b_done = team_done(b_id)
                
                # GK ↔ GK only; outfield ↔ outfield only
                if (s_pos == 1 and b_pos != 1) or (s_pos != 1 and b_pos == 1):
                    continue
                
                # Not played yet and team not started -> RESERVE this bench slot for this starter
                if not b_played and not b_done:
                    used_bench_ids.add(b_id)  # reserved; adds 0 now
                    break  # stop scanning further bench for this starter
                
                # Not played and team started -> DNP, reject and continue
                if not b_played and b_done:
                    continue
                
                # Bench has played -> simulate swap and validate formation
                d2, m2, f2, g2 = d, m, f, g
                if   s_pos == 2: d2 -= 1
                elif s_pos == 3: m2 -= 1
                elif s_pos == 4: f2 -= 1
                elif s_pos == 1: g2 -= 1
                
                if   b_pos == 2: d2 += 1
                elif b_pos == 3: m2 += 1
                elif b_pos == 4: f2 += 1
                elif b_pos == 1: g2 += 1
                
                if not formation_ok(d2, m2, f2, g2):
                    continue
                
                # Accept this bench player
                sub_points += self.live_elements_dict[b_id]['total_points']
                used_bench_ids.add(b_id)
                d, m, f, g = d2, m2, f2, g2  # commit formation for next substitutions
                break  # move to next non-playing starter
        
        return sub_points
    
    def _calculate_live_points(self, picks, chip, transfers_cost=0):
        """Calculate live points for a team"""
        captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
        captain_data = self.live_elements_dict.get(captain_id, {})
        captain_played = captain_data.get('minutes', 0) > 0
        captain_team = self.player_info[captain_id]['team'] if captain_id else None
        captain_team_game_complete_or_postponed = captain_team and self._are_all_team_fixtures_complete_or_postponed(captain_team)
        
        players = picks[:15] if chip == 'bboost' else picks[:11]
        points = 0
        
        for pick in players:
            elem_id = pick['element']
            elem_data = self.live_elements_dict.get(elem_id, {})
            pts = elem_data.get('total_points', 0)
            
            if pick.get('is_captain'):
                if captain_played:
                    mult = 3 if chip == '3xc' else 2
                elif captain_team_game_complete_or_postponed:
                    mult = 0
                else:
                    mult = 1
            elif pick.get('is_vice_captain'):
                if captain_team_game_complete_or_postponed and not captain_played:
                    mult = 3 if chip == '3xc' else 2
                else:
                    mult = 1
            else:
                mult = 1
            
            points += mult * pts
        
        return points - transfers_cost
    
    def _process_team_live(self, entry_id):
        """Process a team for live points"""
        try:
            picks_data = get_entry_picks(entry_id, self.current_gameweek)
            picks = picks_data['picks']
            cost = picks_data['entry_history'].get('event_transfers_cost', 0)
            chip = picks_data['active_chip']
            
            live_pts = self._calculate_live_points(picks, chip, cost)
            sub_pts = self._calculate_sub_points(picks)
            
            captain_id = next((p['element'] for p in picks if p.get('is_captain')), None)
            captain = self.player_info.get(captain_id, {}).get('name', '-')
            
            return {
                'live_points': live_pts,
                'sub_points': sub_pts,
                'total_points': live_pts + sub_pts,
                'captain': captain,
                'chip': chip,
                'chip_ar': get_chip_arabic(chip),
                'chip_active': is_chip_active(chip),
                'picks': picks  # Include picks for unique players calculation
            }
        except FPLApiError:
            return {'total_points': 0, 'captain': '-', 'chip': None, 'chip_ar': get_chip_arabic(None), 'chip_active': False, 'picks': []}
    
    def _get_unique_players(self, team1_ids, team2_ids):
        """Returns players in team1 but not in team2, preserving multiplicities"""
        counter1 = Counter(team1_ids)
        counter2 = Counter(team2_ids)
        
        unique_players = []
        for pid, count1 in counter1.items():
            count2 = counter2.get(pid, 0)
            unique_count = count1 - count2
            
            if unique_count > 0:
                unique_players.extend([pid] * unique_count)
        
        return unique_players
    
    def _calculate_unique_players_for_match(self, entry_1_id, entry_2_id, gw):
        """Calculate unique players for each manager in a match with status"""
        try:
            # Fetch picks for both managers
            team1_data = get_entry_picks(entry_1_id, gw)
            team2_data = get_entry_picks(entry_2_id, gw)
            
            team1_picks = team1_data.get('picks', [])
            team2_picks = team2_data.get('picks', [])
            
            team1_chip = team1_data.get('active_chip')
            team2_chip = team2_data.get('active_chip')
            
            # Get live data for minutes - fetch if not available
            live_minutes = {}
            fixtures = self.fixtures or []
            
            if self.live_elements_dict:
                live_minutes = {pid: data.get('minutes', 0) for pid, data in self.live_elements_dict.items()}
            else:
                # Fetch live data for this gameweek
                try:
                    live_data = get_live_data(gw)
                    live_minutes = {e['id']: e['stats'].get('minutes', 0) for e in live_data.get('elements', [])}
                    fixtures = get_fixtures(gw)
                except:
                    pass
            
            def team_has_unstarted_fixture(team_id):
                """Check if team has at least one unstarted fixture"""
                for f in fixtures:
                    if f['team_h'] == team_id or f['team_a'] == team_id:
                        if not f.get('started', False) and f.get('kickoff_time') is not None:
                            return True
                return False
            
            def get_player_status(pid, is_sub=False):
                """
                Get player status (DGW-safe):
                - 'playing': minutes > 0 and has game in progress or unstarted (blue)
                - 'played': minutes > 0 and ALL games finished (grey)
                - 'pending': minutes == 0 - yet to play (purple)
                """
                minutes = live_minutes.get(pid, 0)
                team_id = self.player_info.get(pid, {}).get('team', 0)

                # Check if player's team has a game in progress or unstarted (DGW-safe)
                game_in_progress = False
                has_unstarted_fixture = False
                for f in fixtures:
                    if f['team_h'] == team_id or f['team_a'] == team_id:
                        started = f.get('started', False)
                        finished = f.get('finished', False) or f.get('finished_provisional', False)
                        if started and not finished:
                            game_in_progress = True
                        if not started and f.get('kickoff_time') is not None:
                            has_unstarted_fixture = True

                if minutes > 0:
                    if game_in_progress or has_unstarted_fixture:
                        return 'playing'  # Blue - still has game(s) to play
                    else:
                        return 'played'   # Grey - ALL games finished
                else:
                    return 'pending'      # Purple - yet to play
            
            def simulate_autosubs(picks, chip):
                """Simulate auto-subs and return XI ids + sub info"""
                if chip == 'bboost':
                    return [p['element'] for p in picks[:15]], set()
                
                starters = picks[:11]
                bench = picks[11:]
                
                xi_ids = [p['element'] for p in starters]
                subbed_in = set()
                
                # Count positions
                counts = {1: 0, 2: 0, 3: 0, 4: 0}
                for pid in xi_ids:
                    pos = self.player_info.get(pid, {}).get('position', 0)
                    if pos in counts:
                        counts[pos] += 1
                
                # Find non-playing starters (0 mins and team finished)
                non_playing = []
                for p in starters:
                    pid = p['element']
                    mins = live_minutes.get(pid, 0)
                    team_id = self.player_info.get(pid, {}).get('team', 0)
                    if mins == 0 and not team_has_unstarted_fixture(team_id):
                        non_playing.append(pid)
                
                # Process bench in order
                for b in bench:
                    bid = b['element']
                    bmins = live_minutes.get(bid, 0)
                    bteam = self.player_info.get(bid, {}).get('team', 0)
                    b_future = team_has_unstarted_fixture(bteam)
                    
                    # Bench eligible if played or has future fixture
                    if not (bmins > 0 or b_future):
                        continue
                    
                    bpos = self.player_info.get(bid, {}).get('position', 0)
                    
                    for sid in list(non_playing):
                        spos = self.player_info.get(sid, {}).get('position', 0)
                        
                        # GK can only replace GK
                        if bpos == 1 and spos != 1:
                            continue
                        if spos == 1 and bpos != 1:
                            continue
                        
                        # Check formation validity
                        new_counts = counts.copy()
                        new_counts[spos] -= 1
                        new_counts[bpos] += 1
                        
                        if new_counts.get(1, 0) < 1:  # GK
                            continue
                        if new_counts.get(2, 0) < 3:  # DEF
                            continue
                        if new_counts.get(3, 0) < 2:  # MID
                            continue
                        if new_counts.get(4, 0) < 1:  # FWD
                            continue
                        
                        # Commit sub
                        counts = new_counts
                        non_playing.remove(sid)
                        xi_ids.remove(sid)
                        xi_ids.append(bid)
                        subbed_in.add(bid)
                        break
                
                return xi_ids, subbed_in
            
            # Get captain and vice-captain IDs
            team1_captain_id = next((p['element'] for p in team1_picks if p.get('is_captain')), None)
            team2_captain_id = next((p['element'] for p in team2_picks if p.get('is_captain')), None)
            team1_vice_id = next((p['element'] for p in team1_picks if p.get('is_vice_captain')), None)
            team2_vice_id = next((p['element'] for p in team2_picks if p.get('is_vice_captain')), None)
            
            # Determine multipliers
            team1_multiplier = 3 if team1_chip == '3xc' else 2
            team2_multiplier = 3 if team2_chip == '3xc' else 2
            
            # Simulate auto-subs
            team1_xi, team1_subs = simulate_autosubs(team1_picks, team1_chip)
            team2_xi, team2_subs = simulate_autosubs(team2_picks, team2_chip)
            
            # Build player ID lists
            team1_players = list(team1_xi)
            team2_players = list(team2_xi)
            
            # Helper to check if captain played or has future fixture
            def captain_is_active(cap_id):
                if not cap_id:
                    return False
                cap_mins = live_minutes.get(cap_id, 0)
                cap_team = self.player_info.get(cap_id, {}).get('team', 0)
                cap_has_future = team_has_unstarted_fixture(cap_team)
                # Captain is active if he played OR his team hasn't played yet
                return cap_mins > 0 or cap_has_future
            
            # Team 1: Add captain multiplier or promote vice-captain
            if team1_captain_id and team1_captain_id in team1_players:
                if captain_is_active(team1_captain_id):
                    # Captain played or will play - give him the multiplier
                    team1_players.extend([team1_captain_id] * (team1_multiplier - 1))
                else:
                    # Captain DNP and team finished - promote vice-captain
                    if team1_vice_id and team1_vice_id in team1_players:
                        team1_players.extend([team1_vice_id] * (team1_multiplier - 1))
            elif team1_vice_id and team1_vice_id in team1_players:
                # Captain not in XI (maybe subbed out?) - check if vice should get multiplier
                if not captain_is_active(team1_captain_id):
                    team1_players.extend([team1_vice_id] * (team1_multiplier - 1))
            
            # Team 2: Add captain multiplier or promote vice-captain
            if team2_captain_id and team2_captain_id in team2_players:
                if captain_is_active(team2_captain_id):
                    # Captain played or will play - give him the multiplier
                    team2_players.extend([team2_captain_id] * (team2_multiplier - 1))
                else:
                    # Captain DNP and team finished - promote vice-captain
                    if team2_vice_id and team2_vice_id in team2_players:
                        team2_players.extend([team2_vice_id] * (team2_multiplier - 1))
            elif team2_vice_id and team2_vice_id in team2_players:
                # Captain not in XI - check if vice should get multiplier
                if not captain_is_active(team2_captain_id):
                    team2_players.extend([team2_vice_id] * (team2_multiplier - 1))
            
            # Calculate unique players
            unique_team1_ids = self._get_unique_players(team1_players, team2_players)
            unique_team2_ids = self._get_unique_players(team2_players, team1_players)
            
            # Aggregate duplicates with count and status
            def aggregate_players_with_status(player_ids, subs_set):
                counts = Counter(player_ids)
                result = []
                for pid, count in counts.items():
                    name = self.player_info.get(pid, {}).get('name', 'Unknown')
                    is_sub = pid in subs_set
                    status = get_player_status(pid, is_sub)
                    
                    if count > 1:
                        display_name = f"{name} x{count}"
                    else:
                        display_name = name
                    
                    result.append({
                        'name': display_name,
                        'status': status
                    })
                return result
            
            unique_team1 = aggregate_players_with_status(unique_team1_ids, team1_subs)
            unique_team2 = aggregate_players_with_status(unique_team2_ids, team2_subs)
            
            return unique_team1, unique_team2
            
        except Exception as e:
            return [], []
    
    def _get_gw_fixtures_final(self, gw, teams_league):
        """Get final fixtures for a completed gameweek (from API match results)"""
        fixtures = []
        try:
            matches = get_league_matches(self.league_id, gw)['results']
            elements = self.bootstrap_data.get('elements', [])
            
            for match in matches:
                entry_1 = match['entry_1_entry']
                entry_2 = match['entry_2_entry']
                
                team_1_info = next((t for t in teams_league if t['entry'] == entry_1), None)
                team_2_info = next((t for t in teams_league if t['entry'] == entry_2), None)
                
                if not team_1_info or not team_2_info:
                    continue
                
                name_1 = team_1_info['player_name']
                name_2 = team_2_info['player_name']
                
                if name_1 in EXCLUDED_PLAYERS or name_2 in EXCLUDED_PLAYERS:
                    continue
                
                # Use official points from match result
                pts_1 = match.get('entry_1_points', 0)
                pts_2 = match.get('entry_2_points', 0)
                
                # Get captain and chip info
                captain_1, chip_1, chip_active_1 = '-', get_chip_arabic(None), False
                captain_2, chip_2, chip_active_2 = '-', get_chip_arabic(None), False
                
                try:
                    gw_data_1 = get_entry_picks(entry_1, gw)
                    captain_id_1 = next((p['element'] for p in gw_data_1.get('picks', []) if p.get('is_captain')), None)
                    if captain_id_1:
                        capt = next((pl for pl in elements if pl.get('id') == captain_id_1), None)
                        captain_1 = capt.get('web_name') if capt else '-'
                    chip_raw_1 = gw_data_1.get('active_chip')
                    chip_1 = get_chip_arabic(chip_raw_1)
                    chip_active_1 = is_chip_active(chip_raw_1)
                except:
                    pass
                
                try:
                    gw_data_2 = get_entry_picks(entry_2, gw)
                    captain_id_2 = next((p['element'] for p in gw_data_2.get('picks', []) if p.get('is_captain')), None)
                    if captain_id_2:
                        capt = next((pl for pl in elements if pl.get('id') == captain_id_2), None)
                        captain_2 = capt.get('web_name') if capt else '-'
                    chip_raw_2 = gw_data_2.get('active_chip')
                    chip_2 = get_chip_arabic(chip_raw_2)
                    chip_active_2 = is_chip_active(chip_raw_2)
                except:
                    pass
                
                if pts_1 > pts_2:
                    winner = 1
                elif pts_2 > pts_1:
                    winner = 2
                else:
                    winner = 0
                
                # Calculate unique players
                unique_1, unique_2 = self._calculate_unique_players_for_match(entry_1, entry_2, gw)
                
                fixtures.append({
                    'team_1_name': name_1,
                    'team_2_name': name_2,
                    'team_1_points': pts_1,
                    'team_2_points': pts_2,
                    'points_diff': abs(pts_1 - pts_2),
                    'winner': winner,
                    'team_1_captain': captain_1,
                    'team_2_captain': captain_2,
                    'team_1_chip': chip_1,
                    'team_2_chip': chip_2,
                    'team_1_chip_active': chip_active_1,
                    'team_2_chip_active': chip_active_2,
                    'team_1_unique': unique_1,
                    'team_2_unique': unique_2,
                    'entry_1': entry_1,
                    'entry_2': entry_2
                })
        except Exception as e:
            print(f"Error fetching GW {gw} fixtures: {e}")
        
        return fixtures
    
    def get_dashboard_data(self):
        """Get all dashboard data - fixtures and standings"""
        try:
            self._initialize_base_data()
            
            # Get league data
            league_data = get_league_standings(self.league_id)
            league_name = league_data.get('league', {}).get('name', 'Elite League')
            teams_league = league_data['standings']['results']
            
            fixtures = []
            standings_dict = {}
            
            # ============================================
            # DECISION LOGIC FOR WHICH FIXTURES TO SHOW
            # ============================================
            # 3 States:
            # 1. GW FINISHED → Show current GW final results
            # 2. GW LIVE (started but not finished) → Show live scores
            # 3. GW NOT STARTED → Show previous GW final results
            # ============================================
            
            gw_not_started_flag = self.gw_info.get('not_started', False)
            
            if self.gw_finished:
                # STATE 1: Gameweek is FINISHED - show current GW final results
                fixtures = self._get_gw_fixtures_final(self.current_gameweek, teams_league)
                self.display_gameweek = self.current_gameweek
                self.fixtures_gameweek = self.current_gameweek
                self.showing_previous_gw = False
                self.is_live = False
                
            elif self.fixtures_started and not gw_not_started_flag:
                # STATE 2: Gameweek is LIVE - show live scores
                self._initialize_live_data()
                matches = get_league_matches(self.league_id, self.current_gameweek)['results']
                
                for match in matches:
                    entry_1 = match['entry_1_entry']
                    entry_2 = match['entry_2_entry']
                    
                    team_1_info = next((t for t in teams_league if t['entry'] == entry_1), None)
                    team_2_info = next((t for t in teams_league if t['entry'] == entry_2), None)
                    
                    if not team_1_info or not team_2_info:
                        continue
                    
                    name_1 = team_1_info['player_name']
                    name_2 = team_2_info['player_name']
                    
                    if name_1 in EXCLUDED_PLAYERS or name_2 in EXCLUDED_PLAYERS:
                        continue
                    
                    data_1 = self._process_team_live(entry_1)
                    data_2 = self._process_team_live(entry_2)
                    
                    pts_1 = data_1['total_points']
                    pts_2 = data_2['total_points']
                    
                    if pts_1 > pts_2:
                        winner = 1
                        result_1, result_2 = 'W', 'L'
                    elif pts_2 > pts_1:
                        winner = 2
                        result_1, result_2 = 'L', 'W'
                    else:
                        winner = 0
                        result_1, result_2 = 'D', 'D'
                    
                    # Calculate unique players
                    unique_1, unique_2 = self._calculate_unique_players_for_match(entry_1, entry_2, self.current_gameweek)
                    
                    fixtures.append({
                        'team_1_name': name_1,
                        'team_2_name': name_2,
                        'team_1_points': pts_1,
                        'team_2_points': pts_2,
                        'points_diff': abs(pts_1 - pts_2),
                        'winner': winner,
                        'team_1_captain': data_1['captain'],
                        'team_2_captain': data_2['captain'],
                        'team_1_chip': data_1['chip_ar'],
                        'team_2_chip': data_2['chip_ar'],
                        'team_1_chip_active': data_1['chip_active'],
                        'team_2_chip_active': data_2['chip_active'],
                        'team_1_unique': unique_1,
                        'team_2_unique': unique_2,
                        'entry_1': entry_1,
                        'entry_2': entry_2
                    })
                    
                    # Build standings data for live
                    for name, entry, data, result, opponent in [
                        (name_1, entry_1, data_1, result_1, name_2),
                        (name_2, entry_2, data_2, result_2, name_1)
                    ]:
                        if name not in standings_dict:
                            info = next((t for t in teams_league if t['entry'] == entry), {})
                            standings_dict[name] = {
                                'entry_id': entry,
                                'player_name': name,
                                'team_name': info.get('entry_name', ''),
                                'base_league_points': int(info.get('total', 0) or 0),
                                'projected_league_points': int(info.get('total', 0) or 0),
                                'current_gw_points': data['total_points'],
                                'captain': data['captain'],
                                'chip': data['chip_ar'],
                                'chip_active': data['chip_active'],
                                'result': result,
                                'opponent': opponent
                            }
                            
                            if result == 'W':
                                standings_dict[name]['projected_league_points'] += 3
                            elif result == 'D':
                                standings_dict[name]['projected_league_points'] += 1
                
                self.display_gameweek = self.current_gameweek
                self.fixtures_gameweek = self.current_gameweek
                self.showing_previous_gw = False
                
            else:
                # STATE 3: Gameweek NOT STARTED - show previous GW final results for fixtures
                # BUT show current GW captains/chips (what managers have selected)
                if self.current_gameweek > 1:
                    prev_gw = self.current_gameweek - 1
                    fixtures = self._get_gw_fixtures_final(prev_gw, teams_league)
                    self.display_gameweek = self.current_gameweek  # Show current GW number
                    self.fixtures_gameweek = prev_gw  # Fixtures are from previous GW
                    self.showing_previous_gw = True  # But note fixtures are from previous
                self.is_live = False
            
            # Fetch standings data for all teams - PARALLEL FETCH
            elements = self.bootstrap_data.get('elements', [])
            
            # Collect all entry IDs (excluding excluded players)
            entry_ids = []
            entry_to_name = {}
            for entry in teams_league:
                name = entry.get('player_name')
                if name in EXCLUDED_PLAYERS:
                    continue
                entry_id = entry.get('entry')
                entry_ids.append(entry_id)
                entry_to_name[entry_id] = name
            
            # Always fetch picks from CURRENT gameweek to show upcoming captains/chips
            fetch_gw = self.current_gameweek
            
            # BATCH FETCH: Get all entry data and picks in parallel
            all_entry_data = get_multiple_entry_data(entry_ids)
            all_picks_data = {}
            
            try:
                all_picks_data = get_multiple_entry_picks(entry_ids, fetch_gw)
            except Exception as e:
                print(f"Error fetching picks: {e}")
                # Continue without picks data
            
            # Process fetched data
            for entry in teams_league:
                name = entry.get('player_name')
                if name in EXCLUDED_PLAYERS:
                    continue
                
                entry_id = entry.get('entry')
                
                # Get from batch-fetched data
                entry_data = all_entry_data.get(entry_id, {})
                gw_data = all_picks_data.get(entry_id, {})
                
                overall_rank = entry_data.get('summary_overall_rank')
                
                captain = None
                chip = None
                chip_active = False
                gw_points = None
                
                if gw_data:
                    gw_points = gw_data.get('entry_history', {}).get('points')
                    captain_id = next((p['element'] for p in gw_data.get('picks', []) if p.get('is_captain')), None)
                    if captain_id:
                        capt = next((pl for pl in elements if pl.get('id') == captain_id), None)
                        captain = capt.get('web_name') if capt else None
                    chip_raw = gw_data.get('active_chip')
                    chip = get_chip_arabic(chip_raw)
                    chip_active = is_chip_active(chip_raw)
                else:
                    chip = get_chip_arabic(None)
                
                if name in standings_dict:
                    standings_dict[name]['overall_rank'] = overall_rank
                    standings_dict[name]['total_points'] = entry.get('points_for')
                else:
                    standings_dict[name] = {
                        'entry_id': entry_id,
                        'player_name': name,
                        'team_name': entry.get('entry_name', ''),
                        'base_league_points': int(entry.get('total', 0) or 0),
                        'projected_league_points': int(entry.get('total', 0) or 0),
                        'current_gw_points': gw_points or 0,
                        'total_points': entry.get('points_for'),
                        'overall_rank': overall_rank,
                        'captain': captain or '-',
                        'chip': chip,
                        'chip_active': chip_active,
                        'result': '-',
                        'opponent': '-'
                    }
            
            # Sort standings
            standings_list = list(standings_dict.values())
            standings_list.sort(key=lambda x: (
                -x['projected_league_points'],
                -(x.get('total_points') or 0),
                x.get('overall_rank') or float('inf')
            ))
            
            # Add ranks
            for i, team in enumerate(standings_list, 1):
                team['rank'] = i
                
                base_sorted = sorted(standings_dict.values(), key=lambda x: (
                    -x['base_league_points'],
                    -(x.get('total_points') or 0)
                ))
                base_rank = next((j+1 for j, t in enumerate(base_sorted) if t['player_name'] == team['player_name']), i)
                team['rank_change'] = base_rank - i
            
            return {
                'success': True,
                'gameweek': self.current_gameweek,
                'display_gameweek': self.display_gameweek,
                'fixtures_gameweek': self.fixtures_gameweek,
                'gw_info': self.gw_info,
                'league_name': league_name,
                'is_live': self.is_live,
                'gw_finished': self.gw_finished,
                'gw_not_started': not self.fixtures_started and not self.gw_finished,
                'showing_previous_gw': self.showing_previous_gw,
                'fixtures': fixtures,
                'standings': standings_list,
                'last_updated_utc': datetime.utcnow().isoformat() + 'Z'
            }
            
        except FPLApiError as e:
            return {
                'success': False,
                'error': str(e),
                'fixtures': [],
                'standings': []
            }


def get_dashboard():
    """Convenience function to get dashboard data"""
    dashboard = DashboardData()
    return dashboard.get_dashboard_data()
