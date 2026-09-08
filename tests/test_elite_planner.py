import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from flask import Flask
from core.elite_planner import planner, event_window, opponent_for, members, chip_availability, finances, free_transfers, squad


class PlannerTests(unittest.TestCase):
    def test_deadline_boundary_and_season_end(self):
        events = [{'id': 1, 'deadline_time': '2026-08-01T10:00:00Z'}, {'id': 2, 'deadline_time': '2026-08-08T10:00:00Z'}]
        next_gw, published = event_window(events, datetime(2026, 8, 1, 10, tzinfo=timezone.utc))
        self.assertEqual((next_gw['id'], published['id']), (2, 1))
        self.assertIsNone(event_window(events, datetime(2026, 9, 1, tzinfo=timezone.utc))[0])
        self.assertIsNone(event_window(events, datetime(2026, 7, 1, tzinfo=timezone.utc))[1])

    @patch('core.elite_planner.fetch_data')
    def test_opponent_on_second_page_and_reversed_side(self, fetch):
        fetch.side_effect = [{'results': [], 'has_next': True}, {'results': [{'entry_1_entry': 22, 'entry_2_entry': 11, 'entry_1_player_name': 'Opponent'}], 'has_next': False}]
        self.assertEqual(opponent_for(11, 4), {'id': 22, 'name': 'Opponent'})
        self.assertIn('page=2', fetch.call_args.args[0])

    @patch('core.elite_planner.fetch_data')
    def test_bye_and_missing_schedule(self, fetch):
        for matches in [[], [{'entry_1_entry': 11, 'entry_2_entry': None}]]:
            fetch.return_value = {'results': matches, 'has_next': False}
            with self.assertRaises(ValueError): opponent_for(11, 4)

    @patch('core.elite_planner.fetch_data')
    def test_all_manager_pages(self, fetch):
        fetch.side_effect = [{'standings': {'results': [{'entry': 1, 'player_name': 'A'}], 'has_next': True}}, {'standings': {'results': [{'entry': 2, 'player_name': 'B'}], 'has_next': False}}]
        self.assertEqual([m['id'] for m in members()], [1, 2])

    @patch('core.elite_planner.finances', return_value={})
    @patch('core.elite_planner.chip_availability', return_value={'3xc': 'available'})
    @patch('core.elite_planner.get_entry_picks')
    @patch('core.elite_planner.get_fixtures')
    @patch('core.elite_planner.opponent_for')
    @patch('core.elite_planner.members')
    @patch('core.elite_planner.get_bootstrap_data')
    @patch('core.elite_planner.event_window')
    def test_api_squads_doubles_blanks_and_membership(self, window, bootstrap, roster, opponent, fixtures, picks, chips, finance):
        window.return_value = ({'id': 4, 'deadline_time': '2026-09-20T12:00:00Z'}, {'id': 3})
        bootstrap.return_value = {'events': [], 'teams': [{'id': i, 'short_name': str(i)} for i in range(1, 4)], 'elements': [{'id': i, 'web_name': str(i), 'element_type': 2, 'team': 1 if i < 15 else 3, 'now_cost': 50, 'status': 'a'} for i in range(1, 16)]}
        roster.return_value = [{'id': 11, 'name': 'Manager', 'team': 'Team'}]
        opponent.return_value = {'id': 22, 'name': 'Opponent'}
        fixtures.return_value = [{'team_h': 1, 'team_a': 2}, {'team_h': 2, 'team_a': 1}]
        picks.return_value = {'picks': [{'element': i, 'position': i} for i in range(1, 16)]}
        app = Flask(__name__); app.register_blueprint(planner)
        client = app.test_client()
        self.assertEqual(client.get('/api/elite/planner?entry=999').status_code, 400)
        response = client.get('/api/elite/planner?entry=11')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['players']['1']['fixtures'], ['2 (H)', '2 (A)'])
        self.assertEqual(response.json['players']['15']['fixtures'], [])
        self.assertEqual(response.json['published_gameweek'], 3)
        self.assertEqual(picks.call_args.args, (22, 3))
        picks.return_value = {'picks': []}
        self.assertEqual(client.get('/api/elite/planner?entry=11').status_code, 409)

    @patch('core.elite_planner.fetch_data')
    def test_chip_windows_and_unknown(self, fetch):
        fetch.return_value = {'chips': [{'name': '3xc', 'event': 5}, {'name': 'freehit', 'event': 19}]}
        self.assertEqual(chip_availability(11, 19)['3xc'], 'used')
        self.assertEqual(chip_availability(11, 20)['3xc'], 'available')
        self.assertEqual(chip_availability(11, 20)['freehit'], 'blocked')
        self.assertEqual(chip_availability(11, 21)['freehit'], 'available')
        fetch.return_value = {}
        self.assertTrue(all(v == 'unknown' for v in chip_availability(11, 4).values()))

    @patch('core.elite_planner.fetch_data')
    def test_selling_prices_history_and_freehit(self, fetch):
        fetch.side_effect = [{'chips': [{'name': 'freehit', 'event': 3}]}, [
            {'event': 2, 'time': 'a', 'element_out': 9, 'element_in': 1, 'element_in_cost': 70},
            {'event': 3, 'time': 'b', 'element_out': 1, 'element_in': 2, 'element_in_cost': 60},
            {'event': 5, 'time': 'c', 'element_out': 1, 'element_in': 3, 'element_in_cost': 50}]]
        result = finances(11, {'ids': [1, 2], 'bank': 12, 'snapshot_gameweek': 4}, {1: {'cost': 75}, 2: {'cost': 65}})
        self.assertEqual(result['bank'], 12)
        self.assertEqual(result['sales'][1]['value'], 72)
        self.assertEqual(result['sales'][2]['source'], 'market_estimate')
        fetch.return_value = {}; fetch.side_effect = None
        result = finances(11, {'ids': [1], 'bank': None, 'snapshot_gameweek': 4}, {1: {'cost': 75}})
        self.assertIsNone(result['bank'])
        self.assertEqual(result['sales'][1]['source'], 'market_estimate')

    @patch('core.elite_planner.get_entry_picks')
    def test_restore_pre_freehit_squad_and_bank(self, picks):
        picks.side_effect = [{'active_chip': 'freehit'}, {'picks': [
            {'element': i, 'position': i} for i in range(1, 16)], 'entry_history': {'bank': 17}}]
        result = squad(11, 4, {i: {} for i in range(1, 16)})
        self.assertEqual(result['snapshot_gameweek'], 3)
        self.assertEqual(result['bank'], 17)
        self.assertEqual(picks.call_args.args, (11, 3))

    @patch('core.elite_planner.fetch_data')
    def test_free_transfers_are_derived_and_self_checked(self, fetch):
        def history(rows, chips=()):
            return {'chips': [{'name': n, 'event': e} for n, e in chips],
                    'current': [{'event': gw, 'event_transfers': made, 'event_transfers_cost': cost}
                                for gw, made, cost in rows]}

        # No transfers: one banked per gameweek.
        fetch.return_value = history([(1, 0, 0), (2, 0, 0), (3, 0, 0)])
        self.assertEqual(free_transfers(11, 4), 3)

        # Two spent in GW3 against two banked leaves none, then GW4 restores one.
        fetch.return_value = history([(1, 0, 0), (2, 0, 0), (3, 2, 0)])
        self.assertEqual(free_transfers(11, 4), 1)

        # A hit: three made on one free transfer costs 8.
        fetch.return_value = history([(1, 0, 0), (2, 3, 8), (3, 0, 0)])
        self.assertEqual(free_transfers(11, 4), 2)

        # The bank is capped at five however long the manager sits still.
        fetch.return_value = history([(1, 0, 0)] + [(gw, 0, 0) for gw in range(2, 12)])
        self.assertEqual(free_transfers(11, 12), 5)

        # A Wildcard week is unlimited and free, and preserves the bank.
        fetch.return_value = history([(1, 0, 0), (2, 0, 0), (3, 9, 0)],
                                     chips=[('wildcard', 3)])
        self.assertEqual(free_transfers(11, 4), 3)

        # Only gameweeks before the one being planned are counted.
        fetch.return_value = history([(1, 0, 0), (2, 0, 0), (3, 0, 0)])
        self.assertEqual(free_transfers(11, 3), 2)

        # If the simulation cannot reproduce a published cost its assumptions
        # are wrong, so it reports nothing rather than a confident guess.
        fetch.return_value = history([(1, 0, 0), (2, 1, 12), (3, 0, 0)])
        self.assertIsNone(free_transfers(11, 4))

        fetch.side_effect = ValueError('unreachable')
        self.assertIsNone(free_transfers(11, 4))

    @patch('core.elite_planner.get_entry_picks')
    def test_freehit_walkback_edges(self, picks):
        players = {i: {} for i in range(1, 16)}
        lineup = [{'element': i, 'position': i} for i in range(1, 16)]

        # Wildcard is permanent, so it must NOT be stepped over.
        picks.return_value = {'picks': lineup, 'active_chip': 'wildcard'}
        self.assertEqual(squad(11, 5, players)['snapshot_gameweek'], 5)

        # GW19 and GW20 are different chip sets, so Free Hit can run twice in a
        # row; a single step back would land on the second one.
        picks.side_effect = [{'active_chip': 'freehit'}, {'active_chip': 'freehit'},
                             {'picks': lineup, 'active_chip': None}]
        self.assertEqual(squad(11, 20, players)['snapshot_gameweek'], 18)

        # Nothing but Free Hits back to the start is a stated error, not a crash.
        picks.side_effect = None
        picks.return_value = {'active_chip': 'freehit'}
        with self.assertRaises(ValueError): squad(11, 3, players)


if __name__ == '__main__': unittest.main()
