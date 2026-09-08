import unittest
from unittest.mock import patch

from flask import Flask
from core.cities_planner import cities_planner, city_opponent
from core.fpl_api import FPLApiError


class CitiesPlannerTests(unittest.TestCase):
    roster = {'City A': [11, 12, 13], 'City B': [21, 22, 23]}

    def test_each_page_uses_its_own_theme_and_endpoint(self):
        app = Flask(__name__, template_folder='../templates')
        app.register_blueprint(cities_planner)
        client = app.test_client()
        for league, title in [('cities', 'دوري المدن'), ('libyan', 'الدوري الليبي'), ('arab', 'البطولة العربية')]:
            page = client.get(f'/league/{league}/planner')
            self.assertEqual(page.status_code, 200)
            html = page.get_data(as_text=True)
            self.assertIn(title, html)
            self.assertIn(f'css/{league}.css', html)
            self.assertIn(f'data-planner-api="/api/{league}/planner"', html)
            self.assertIn(f'{league}_mark_192.png', html)
            if league != 'cities':
                self.assertNotIn('css/cities.css', html)

    @patch('core.cities_planner.fetch_data')
    def test_other_leagues_use_correct_rosters_and_fixture_ids(self, fetch):
        from core.cities_planner import league_config
        for league in ('libyan', 'arab'):
            roster, league_id, _, _ = league_config(league)
            first, second = list(roster)[:2]
            fetch.return_value = {'results': [{'entry_1_entry': roster[first][0], 'entry_2_entry': roster[second][0]}]}
            self.assertEqual(city_opponent(first, 4, league), second)
            self.assertIn(f'/league/{league_id}/', fetch.call_args.args[0])

    @patch('core.cities_planner.TEAMS_FPL_IDS', roster)
    @patch('core.cities_planner.fetch_data')
    def test_representative_on_second_page_reversed(self, fetch):
        fetch.side_effect = [{'results': [], 'has_next': True}, {'results': [
            {'event': 4, 'entry_1_entry': 22, 'entry_2_entry': 13}], 'has_next': False}]
        self.assertEqual(city_opponent('City A', 4), 'City B')
        self.assertIn('page=2', fetch.call_args.args[0])

    @patch('core.cities_planner.TEAMS_FPL_IDS', roster)
    @patch('core.cities_planner.fetch_data')
    def test_reject_missing_bye_unknown_and_ambiguous_fixtures(self, fetch):
        for results in ([], [{'entry_1_entry': 11, 'entry_2_entry': None}],
                        [{'entry_1_entry': 11, 'entry_2_entry': 99}],
                        [{'entry_1_entry': 11, 'entry_2_entry': 21}] * 2):
            fetch.return_value = {'results': results}
            with self.assertRaises(ValueError):
                city_opponent('City A', 4)

    @patch('core.cities_planner.TEAMS_FPL_IDS', roster)
    @patch('core.cities_planner.manager_snapshot')
    @patch('core.cities_planner.city_opponent', return_value='City B')
    @patch('core.cities_planner.get_fixtures', return_value=[{'team_h': 1, 'team_a': 2}]*2)
    @patch('core.cities_planner.event_window', return_value=({'id': 4, 'deadline_time': '2026-09-20T12:00:00Z'}, {'id': 3}))
    @patch('core.cities_planner.get_bootstrap_data')
    def test_api_six_managers_and_no_partial_comparison(self, bootstrap, window, fixtures, opponent, snapshot):
        bootstrap.return_value = {'events': [], 'teams': [{'id': i, 'short_name': str(i)} for i in (1, 2, 3)],
            'elements': [{'id': i, 'web_name': str(i), 'element_type': 2, 'team': i, 'now_cost': 50, 'status': 'a'} for i in (1, 3)]}
        snapshot.side_effect = lambda entry, *args: {'id': entry}
        app = Flask(__name__); app.register_blueprint(cities_planner)
        client = app.test_client()
        self.assertEqual(client.get('/api/cities/planner?city=Unknown').status_code, 400)
        self.assertEqual(client.get('/api/cities/planner').json['cities'], list(self.roster))
        response = client.get('/api/cities/planner?city=City%20A')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m['id'] for m in response.json['teams'][0]['managers']], [11, 12, 13])
        self.assertEqual([m['id'] for m in response.json['teams'][1]['managers']], [21, 22, 23])
        self.assertEqual(response.json['players']['1']['fixtures'], ['2 (H)', '2 (H)'])
        self.assertEqual(response.json['players']['3']['fixtures'], [])
        snapshot.side_effect = FPLApiError('Unavailable')
        self.assertEqual(client.get('/api/cities/planner?city=City%20A').status_code, 502)


if __name__ == '__main__':
    unittest.main()
