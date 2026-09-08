import copy
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from flask import Flask
from models import db
from core.elite_match import match_centre, record, MatchObservation, MatchUpdate, acquire_lease
from core.elite_match_scoring import player_scores, lineup, changes


def element(pid, stats, fixture=1, minutes=60):
    parts = [{'identifier': key, 'points': value, 'value': minutes if key == 'minutes' else 1} for key, value in stats.items()]
    return {'id': pid, 'stats': {'total_points': sum(stats.values()), 'minutes': minutes},
            'explain': [{'fixture': fixture, 'stats': parts}]}


def state(points=1, weight=1, other_weight=0, identifier='minutes', value=59):
    parts = {'1:'+identifier: {'identifier': identifier, 'fixture': 1, 'points': points, 'value': value, 'provisional': False}}
    teams = [{'score': points*w, 'hits': 0, 'players': [{'id': 1, 'name': 'Palmer', 'points': points*w, 'weight': w, 'parts': copy.deepcopy(parts)}]} for w in (weight, other_weight)]
    return {'teams': teams, 'gap': points*(weight-other_weight)}


class ScoringTests(unittest.TestCase):
    def test_sixty_minutes_and_all_point_categories(self):
        update = changes(state(), state(2, value=60))[0]
        self.assertEqual(update['parts'][0]['label'], 'بلوغ 60 دقيقة')
        for category, points in [('goals_scored', 5), ('assists', 3), ('clean_sheets', 4), ('saves', 1),
                                 ('yellow_cards', -1), ('red_cards', -3), ('own_goals', -2),
                                 ('penalties_saved', 5), ('penalties_missed', -2), ('goals_conceded', -1),
                                 ('defensive_contribution', 2), ('future_category', 2), ('bonus', 3)]:
            with self.subTest(category=category):
                update = changes(state(0, 2, 1, category), state(points, 2, 1, category))[0]
                self.assertEqual(update['gap_delta'], points)
                self.assertEqual(update['delta'], [points*2, points])
        self.assertEqual(changes(state(1, 2, 2), state(8, 2, 2)), [])
        # FPL keeps a subbed defender's clean sheet; do not recalculate from team score.
        before = state(4, identifier='clean_sheets')
        self.assertEqual(changes(before, copy.deepcopy(before)), [])
        self.assertEqual(changes(before, state(0, identifier='clean_sheets'))[0]['gap_delta'], -4)

    def test_bonus_doubles_ties_and_confirmation(self):
        elements = [element(1, {'minutes': 2}), element(2, {'minutes': 2}), element(3, {'minutes': 2})]
        elements[0]['explain'].append(element(1, {'minutes': 2}, fixture=2)['explain'][0])
        elements[0]['stats']['total_points'] = 4
        elements[0]['stats']['minutes'] = 120
        fixtures = [{'id': 1, 'started': True, 'stats': [{'identifier': 'bps', 'h': [{'element': 1, 'value': 30}, {'element': 2, 'value': 30}], 'a': [{'element': 3, 'value': 20}]}]},
                    {'id': 2, 'started': True, 'stats': [{'identifier': 'bps', 'h': [{'element': 1, 'value': 40}], 'a': []}]}]
        result = player_scores(elements, fixtures)
        self.assertEqual(result[1]['points'], 10)  # 4 appearance + 3 + 3 bonus.
        self.assertEqual(result[2]['points'], 5)
        self.assertEqual(result[3]['points'], 3)  # Two tied first; next gets one.
        self.assertEqual(player_scores(elements, fixtures, settled=True)[1]['points'], 4)
        self.assertTrue(result[1]['parts']['2:bonus']['provisional'])

    def test_substitutions_chips_captain_and_double_gameweek_wait(self):
        positions = [1,2,2,2,3,3,3,3,4,4,4,1,3,2,2]
        players = {i+1: {'id': i+1, 'name': str(i+1), 'position': pos, 'club': i+1} for i,pos in enumerate(positions)}
        scores = {i: {'minutes': 90, 'points': 2, 'parts': {}} for i in players}
        picks = {'picks': [{'element': i, 'position': i, 'is_captain': i == 5, 'is_vice_captain': i == 6, 'multiplier': 2 if i == 5 else int(i <= 11)} for i in players]}
        fixtures = [{'id': i, 'team_h': i, 'team_a': 100+i, 'finished': True} for i in players]
        scores[5]['minutes'] = 0;scores[5]['points'] = 0
        fixtures.append({'id': 100, 'team_h': 5, 'team_a': 99, 'finished': False})
        result = lineup(picks, players, scores, fixtures)
        self.assertEqual(result['substitutions'], [])
        self.assertEqual(next(p for p in result['players'] if p['id'] == 6)['weight'], 1)
        fixtures[-1]['finished'] = True
        result = lineup(picks, players, scores, fixtures)
        self.assertEqual(result['substitutions'], [{'element_in': 13, 'element_out': 5}])
        self.assertEqual(next(p for p in result['players'] if p['id'] == 6)['weight'], 2)
        picks['active_chip'] = '3xc'
        self.assertEqual(next(p for p in lineup(picks,players,scores,fixtures)['players'] if p['id'] == 6)['weight'], 3)
        picks['active_chip'] = 'bboost'
        result = lineup(picks,players,scores,fixtures)
        self.assertEqual(result['substitutions'], [])
        self.assertEqual(sum(p['weight']>0 for p in result['players']), 15)
        # A change of multiplier counts even without a new player-scoring event.
        self.assertEqual(changes(state(4,1),state(4,2))[0]['gap_delta'],4)

    def test_event_rows_reconcile_gap_and_transfer_hits(self):
        before, after = state(2,2,1), state(7,3,1)
        after['teams'][0]['hits'] = 4;after['teams'][0]['score'] -= 4;after['gap'] -= 4
        self.assertEqual(sum(e['gap_delta'] for e in changes(before,after)), after['gap']-before['gap'])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
        db.init_app(self.app);self.app.register_blueprint(match_centre)
        self.ctx = self.app.app_context();self.ctx.push();db.create_all()

    def tearDown(self):
        db.session.remove();db.drop_all();self.ctx.pop()

    def test_baseline_no_duplicates_old_read_and_resume(self):
        now = datetime.utcnow()
        record('match',state(),now)
        self.assertEqual(MatchUpdate.query.count(),0)
        record('match',state(2,value=60),now+timedelta(seconds=60))
        record('match',state(2,value=60),now+timedelta(seconds=60))
        record('match',state(7),now-timedelta(seconds=60))
        self.assertEqual(MatchUpdate.query.count(),1)
        self.assertEqual(db.session.get(MatchObservation,'match',populate_existing=True).state['gap'],2)
        record('match',state(2,value=60),now+timedelta(minutes=6))
        self.assertTrue(MatchUpdate.query.order_by(MatchUpdate.id.desc()).first().payload['coverage_gap'])
        db.session.remove()
        self.assertEqual(MatchUpdate.query.count(),2)

    def test_lease_and_fixture_validation(self):
        self.assertTrue(acquire_lease());self.assertFalse(acquire_lease())
        with patch('core.elite_match.league_matches',return_value=[]):
            self.assertEqual(self.app.test_client().get('/api/elite/match/4/11/22').status_code,404)

    def test_api_serves_shared_recent_observation(self):
        from config import LEAGUE_ID
        key = f'{LEAGUE_ID}:2026-08-01:4:11:22'
        stored = dict(state(2), observed_at='2026-09-01T00:00:00+00:00', gameweek=4, settled=False)
        record(key, stored, datetime.utcnow())
        with patch('core.elite_match.league_matches', return_value=[{'entry_1_entry':11,'entry_2_entry':22}]), \
             patch('core.elite_match.get_bootstrap_data', return_value={'events':[{'id':1,'deadline_time':'2026-08-01T10:00:00Z'}]}), \
             patch('core.elite_match.context') as fetch_context:
            response = self.app.test_client().get('/api/elite/match/4/22/11')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['gap'],2)
            fetch_context.assert_not_called()


if __name__ == '__main__':
    unittest.main()
