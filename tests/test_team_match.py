import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from models import db
from core import team_match as tm
from core.elite_match_scoring import lineup, changes


def manager(entry, points, weight=1, hits=0):
    return {'entry': entry, 'name': str(entry), 'score': points * weight - hits, 'hits': hits,
            'players': [{'id': 7, 'name': 'Palmer', 'points': points * weight, 'weight': weight,
                         'active': bool(weight), 'parts': {}}]}


class TeamScoringTests(unittest.TestCase):
    def test_repeated_players_and_manager_effects(self):
        def state(points):
            return {'teams': [tm.aggregate([manager(1, points), manager(2, points, 0), manager(3, points, 0)], 'A'),
                              tm.aggregate([manager(i, points) for i in (4, 5, 6)], 'B')]}
        before, after = state(2), state(7)
        events = changes(before, after)
        self.assertEqual(events[0]['gap_delta'], -10)
        tm.manager_effects(events, before, after)
        self.assertEqual(len(events[0]['manager_effects']), 4)
        self.assertEqual(sum(e['delta'] for e in events[0]['manager_effects']), 20)

    def test_equal_exposure_cancels_and_hits_sum(self):
        def state(points):
            return {'teams': [tm.aggregate([manager(1, points, 2), manager(2, points, 1)], 'A'),
                              tm.aggregate([manager(i, points) for i in (4, 5, 6)], 'B')]}
        self.assertEqual(changes(state(2), state(7)), [])
        before, after = state(2), state(2)
        after['teams'][0] = tm.aggregate([manager(1, 2, 2, 4), manager(2, 2, 1, 8)], 'A')
        events = changes(before, after)
        self.assertEqual(events[0]['gap_delta'], -12)
        tm.manager_effects(events, before, after)
        self.assertEqual([e['delta'] for e in events[0]['manager_effects']], [-4, -8])

    def test_chip_rules_live_and_settled(self):
        positions = [1,2,2,2,3,3,3,3,4,4,4,1,3,2,2]
        players = {i+1: {'id': i+1, 'name': str(i+1), 'position': p, 'club': i+1} for i,p in enumerate(positions)}
        scores = {i: {'minutes': 90, 'points': 2, 'parts': {}} for i in players}
        picks = {'picks': [{'element': i, 'position': i, 'is_captain': i == 5, 'is_vice_captain': i == 6,
                            'multiplier': 3 if i == 5 else int(i <= 11)} for i in players], 'active_chip': '3xc'}
        for settled in (False, True):
            result = lineup(picks, players, scores, [], settled, team_rules=True)
            self.assertEqual(result['score'], 24)
            self.assertEqual(result['players'][4]['weight'], 2)
        picks['active_chip'] = 'bboost'
        for p in picks['picks']:
            p['multiplier'] = 2 if p['element'] == 5 else 1
        scores[5].update(minutes=0, points=0)
        fixtures = [{'id': i, 'team_h': i, 'team_a': 100+i, 'finished': True} for i in players]
        for settled in (False, True):
            result = lineup(picks, players, scores, fixtures, settled, team_rules=True)
            self.assertEqual(result['score'], 24)
            self.assertEqual(sum(p['weight'] > 0 for p in result['players']), 11)
            self.assertEqual(result['players'][5]['weight'], 2)
            self.assertEqual(result['substitutions'], [{'element_in': 13, 'element_out': 5}])

    def test_representatives_and_key_isolation(self):
        roster = {'A': [1,2,3], 'B': [4,5,6]}
        with patch.object(tm, 'league_config', return_value=(roster, 100, 'Title', 'TITLE')), \
             patch.object(tm, 'league_matches', return_value=[{'entry_1_entry': 2, 'entry_2_entry': 6}]):
            match = tm.fixtures_for('cities', 4)[0]
            self.assertEqual(match['manager_ids'], [[1,2,3], [4,5,6]])
            key = tm.match_key('cities', match, '2026', 4)
            self.assertNotEqual(key, tm.match_key('arab', match, '2026', 4))
            self.assertNotEqual(key, tm.match_key('cities', match, '2025', 4))
            match['manager_ids'][0] = [1,2,7]
            self.assertNotEqual(key, tm.match_key('cities', match, '2026', 4))
        with patch.object(tm, 'league_config', return_value=({'A':[1,2,2]}, 100, '', '')):
            with self.assertRaises(ValueError): tm.fixtures_for('cities', 4)


class TeamRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
        db.init_app(self.app)
        self.app.register_blueprint(tm.team_match)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def test_league_pages_and_invalid_fixture(self):
        client = self.app.test_client()
        for league in tm.LEAGUES:
            page = client.get(f'/league/{league}/match/4?first=A&second=B')
            self.assertEqual(page.status_code, 200)
            self.assertIn(f'planner-{league}', page.text)
            self.assertIn(f'css/{league}.css', page.text)
            self.assertIn(f'/api/{league}/match/4', page.text)
            with patch.object(tm, 'fixtures_for', return_value=[]):
                self.assertEqual(client.get(f'/api/{league}/match/4?first=A&second=B').status_code, 404)

    def test_incomplete_squad_does_not_save_partial_state(self):
        match = {'entry_1_entry':1, 'entry_2_entry':4, 'team_names':['A','B'], 'manager_ids':[[1,2,3],[4,5,6]]}
        ctx = {'gw':4, 'season':'2026', 'players':{}, 'scores':{}, 'fixtures':[], 'settled':False}
        with patch.object(tm, 'get_entry_picks', return_value={'picks':[]}), \
             patch.object(tm, 'manager_name', return_value='Manager'):
            with self.assertRaises(ValueError): tm.snapshot_team('cities', match, ctx)
        self.assertEqual(tm.MatchObservation.query.count(), 0)

    def test_all_leagues_serve_persisted_manager_impacts(self):
        match = {'entry_1_entry':1, 'entry_2_entry':4, 'team_names':['A','B'], 'manager_ids':[[1,2,3],[4,5,6]]}
        now = datetime.utcnow()
        for league in tm.LEAGUES:
            key = tm.match_key(league, match, '2026-08-01', 4)
            for points, observed in ((2, now-timedelta(seconds=60)), (7, now)):
                teams = [tm.aggregate([manager(1, points), manager(2, points, 0), manager(3, points, 0)], 'A'),
                         tm.aggregate([manager(i, points) for i in (4,5,6)], 'B')]
                state = {'team_league':league, 'gameweek':4, 'settled':False,
                         'teams':teams, 'gap':teams[0]['score']-teams[1]['score'], 'observed_at':tm.stamp(observed)}
                tm.record(key, state, observed)
            with patch.object(tm, 'fixtures_for', return_value=[match]), \
                 patch.object(tm, 'get_bootstrap_data', return_value={'events':[{'id':1,'deadline_time':'2026-08-01T10:00:00Z'}]}), \
                 patch.object(tm, 'context') as fetch_context:
                response = self.app.test_client().get(f'/api/{league}/match/4?first=B&second=A')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['gap'], -14)
                self.assertEqual(len(response.json['updates'][0]['events'][0]['manager_effects']), 4)
                fetch_context.assert_not_called()


if __name__ == '__main__':
    unittest.main()
