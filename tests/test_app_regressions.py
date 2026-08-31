import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app as scoreboard


class AppRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.paths = {
            'CONFIG_FILE': root / 'config.json',
            'PLAYERS_FILE': root / 'players.json',
            'SCORES_FILE': root / 'scores.json',
            'BOT_SCORES_FILE': root / 'bot_scores.json',
            'IMPORTED_MATCHES_FILE': root / 'imported_matches.json',
            'AUTODARTS_STATUS_FILE': root / 'autodarts_status.json',
            'AUTODARTS_LAST_RESULT_FILE': root / 'autodarts_last_result.json',
            'AUTODARTS_SYNC_STATE_FILE': root / 'autodarts_sync_state.json',
            'CEC_CONFIG_DIR': root / 'runtime',
            'CEC_CONFIG_FILE': root / 'runtime/cec.conf',
            'SCREENSAVER_CONFIG_FILE': root / 'runtime/screensaver.conf',
            'KIOSK_CONFIG_FILE': root / 'runtime/kiosk.conf',
            'SCREENSAVER_PID_FILE': root / 'runtime/screensaver.pid',
        }
        for name in ('PLAYERS_FILE', 'SCORES_FILE', 'BOT_SCORES_FILE', 'IMPORTED_MATCHES_FILE'):
            self.paths[name].write_text('[]\n', encoding='utf-8')
        self.paths['CONFIG_FILE'].write_text(json.dumps({
            'autodarts_email': 'legacy@example.test',
            'autodarts_password': 'do-not-change',
            'autodarts_enabled': True,
            'autodarts_interval_minutes': 37,
        }), encoding='utf-8')
        self.paths['AUTODARTS_SYNC_STATE_FILE'].write_text('{}\n', encoding='utf-8')
        self.patchers = [patch.object(scoreboard, name, str(value)) for name, value in self.paths.items()]
        for patcher in self.patchers:
            patcher.start()
        scoreboard.app.config.update(TESTING=True)
        self.client = scoreboard.app.test_client()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    @patch('app.subprocess.run')
    def test_saving_kiosk_settings_preserves_autodarts_credentials(self, run):
        response = self.client.post('/admin', data={
            'action': 'save_config',
            'kiosk_url': 'http://127.0.0.1:5000/',
            'kiosk_display_mode': 'auto',
            'kiosk_hide_cursor': 'on',
        })
        self.assertEqual(302, response.status_code)
        config = json.loads(self.paths['CONFIG_FILE'].read_text(encoding='utf-8'))
        self.assertEqual('legacy@example.test', config['autodarts_email'])
        self.assertEqual('do-not-change', config['autodarts_password'])
        self.assertTrue(config['autodarts_enabled'])
        self.assertEqual(37, config['autodarts_interval_minutes'])

    def test_cec_adapter_and_name_are_sanitized_in_runtime_file(self):
        scoreboard.write_cec_config({
            'cec_enabled': True,
            'cec_device_name': 'bad\nname',
            'cec_adapter': '--help',
            'cec_standby_time': '22:00',
            'cec_wake_time': '08:00',
            'cec_check_interval': 50,
        })
        runtime = self.paths['CEC_CONFIG_FILE'].read_text(encoding='utf-8')
        self.assertIn("CEC_NAME='Dart Scoreboard'", runtime)
        self.assertIn("CEC_ADAPTER=''", runtime)
        self.assertNotIn('--help', runtime)

    def test_addon_action_allowlist_rejects_shell_like_action(self):
        response = self.client.post('/admin/addons/cec/start;reboot')
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()['ok'])

    def test_autodarts_missing_credentials_path_remains_non_networked(self):
        self.paths['CONFIG_FILE'].write_text('{}\n', encoding='utf-8')
        result = scoreboard.autodarts_collect_and_import(max_pages=1)
        self.assertFalse(result['ok'])
        self.assertIn('Credentials', result['error'])

    def test_admin_groups_scores_by_import_day(self):
        self.paths['PLAYERS_FILE'].write_text(
            json.dumps([{'id': 1, 'name': 'Test', 'image': 'dummy.png'}]), encoding='utf-8'
        )
        self.paths['SCORES_FILE'].write_text(json.dumps([
            {'player_id': 1, 'date': '27.08.2026 10:00', 'legs': 1},
            {'player_id': 1, 'date': '27.08.2026 11:00', 'legs': 2},
            {'player_id': 1, 'date': '28.08.2026 09:00', 'legs': 3},
        ]), encoding='utf-8')
        html = self.client.get('/admin').get_data(as_text=True)
        self.assertEqual(2, html.count('class="score-date-group"'))
        self.assertIn('27.08.2026', html)
        self.assertIn('28.08.2026', html)

    def test_scoreboard_template_renders_all_statistic_stages(self):
        response = self.client.get('/')

        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('id="grid1-stage"', html)
        self.assertIn('id="grid2-stage"', html)
        self.assertIn('id="grid3-stage"', html)
        self.assertIn('id="leaderboard-stage"', html)
        self.assertIn('Zack... 26', html)

    @patch('app.requests.get')
    def test_match_list_uses_explicit_sort_and_page(self, get):
        response = Mock(ok=True)
        response.json.return_value = {'items': [{'id': 'new'}], 'last': False}
        get.return_value = response

        items, is_last = scoreboard._autodarts_list_page('token', 3, '-finished_at')

        self.assertEqual([{'id': 'new'}], items)
        self.assertTrue(is_last)
        self.assertEqual({
            'size': scoreboard.AUTODARTS_PAGE_SIZE,
            'page': 3,
            'sort': '-finished_at',
        }, get.call_args.kwargs['params'])

    @patch('app._autodarts_import_one_match')
    @patch('app._autodarts_list_page')
    @patch('app.autodarts_api_login', return_value=('token', None))
    def test_incremental_import_stops_on_fully_known_page(self, _login, list_page, import_one):
        self.paths['IMPORTED_MATCHES_FILE'].write_text('["known"]\n', encoding='utf-8')
        self.paths['AUTODARTS_SYNC_STATE_FILE'].write_text(json.dumps({
            'initial_import_completed': True,
            'pending_matches': {},
        }), encoding='utf-8')
        list_page.side_effect = [
            ([{'id': 'new'}], False),
            ([{'id': 'known'}], False),
        ]

        def import_success(_token, match_id, _metadata, imported, _state):
            imported.append(match_id)
            return True, None, True

        import_one.side_effect = import_success
        result = scoreboard.autodarts_collect_and_import(mode='incremental')

        self.assertTrue(result['ok'])
        self.assertEqual(['new'], result['imported_matches'])
        self.assertEqual(2, result['pages_scanned'])
        self.assertEqual([
            (('token', 0, '-finished_at'),),
            (('token', 1, '-finished_at'),),
        ], [(call.args,) for call in list_page.call_args_list])

    @patch('app._autodarts_import_one_match')
    @patch('app._autodarts_list_page')
    @patch('app.autodarts_api_login', return_value=('token', None))
    def test_backfill_resumes_oldest_first_and_marks_completion(self, _login, list_page, import_one):
        self.paths['AUTODARTS_SYNC_STATE_FILE'].write_text(json.dumps({
            'initial_import_completed': False,
            'backfill_next_page': 2,
            'pending_matches': {},
        }), encoding='utf-8')
        list_page.return_value = ([{'id': 'historic'}], True)

        def import_success(_token, match_id, _metadata, imported, _state, force=False):
            imported.append(match_id)
            return True, None, True

        import_one.side_effect = import_success
        result = scoreboard.autodarts_collect_and_import(mode='backfill')
        state = json.loads(self.paths['AUTODARTS_SYNC_STATE_FILE'].read_text(encoding='utf-8'))

        self.assertTrue(result['initial_import_completed'])
        self.assertEqual(0, state['backfill_next_page'])
        self.assertTrue(state['initial_import_completed'])
        list_page.assert_called_once_with('token', 2, 'finished_at')

    def test_autodarts_match_uses_real_play_checkout_and_180_times(self):
        result = {
            'id': '01800000-0000-7000-8000-000000000001',
            'createdAt': '2026-08-20T21:30:00Z',
            'finishedAt': '2026-08-20T22:02:00Z',
            'players': [{'id': 'p1', 'name': 'Alice'}],
            'scores': [{'legs': 2}],
            'matchStats': [{
                'playerId': 'p1', 'dartsThrown': 30, 'average': 50,
                'first9Average': 55, 'plus60': 1, 'plus100': 1,
                'plus140': 0, 'plus170': 0, 'total180': 1,
                'checkoutPoints': 100, 'checkouts': 2, 'checkoutsHit': 2,
            }],
            'games': [
                {
                    'finishedAt': '2026-08-20T21:59:00Z',
                    'winnerPlayerId': 'p1', 'winner': 0,
                    'turns': [
                        {'playerId': 'p1', 'points': 180, 'finishedAt': '2026-08-20T21:50:00Z', 'throws': []},
                        {
                            'playerId': 'p1', 'points': 100, 'score': 0,
                            'finishedAt': '2026-08-20T21:59:00Z',
                            'throws': [
                                {'createdAt': '2026-08-20T21:58:55Z', 'segment': {'name': 'S20', 'number': 20, 'multiplier': 1}},
                                {'createdAt': '2026-08-20T21:58:57Z', 'segment': {'name': 'D15', 'number': 15, 'multiplier': 2}},
                                {'createdAt': '2026-08-20T21:58:59Z', 'segment': {'name': 'D25', 'number': 25, 'multiplier': 2}},
                            ],
                        },
                    ],
                },
                {
                    'finishedAt': '2026-08-20T22:02:00Z',
                    'winnerPlayerId': 'p1', 'winner': 0,
                    'turns': [
                        {
                            'playerId': 'p1', 'points': 50, 'score': 40,
                            'finishedAt': '2026-08-20T22:01:00Z',
                            'throws': [
                                {'createdAt': '2026-08-20T22:00:59Z', 'segment': {'name': 'D25', 'number': 25, 'multiplier': 2}},
                            ],
                        },
                        {
                            'playerId': 'p1', 'points': 40, 'score': 0,
                            'finishedAt': '2026-08-20T22:02:00Z',
                            'throws': [
                                {'createdAt': '2026-08-20T22:01:59Z', 'segment': {'name': 'D20', 'number': 20, 'multiplier': 2}},
                            ],
                        },
                    ],
                },
            ],
            'legStats': [
                {'winner': 0, 'stats': [{'playerId': 'p1', 'checkoutPoints': 100, 'dartsThrown': 15}]},
                {'winner': 0, 'stats': [{'playerId': 'p1', 'checkoutPoints': 40, 'dartsThrown': 15}]},
            ],
        }

        scoreboard.import_match_result_to_scores(
            result, games_len=2, match_id=result['id'], imported_at='2026-08-28T10:00:00Z'
        )
        score = json.loads(self.paths['SCORES_FILE'].read_text(encoding='utf-8'))[0]
        cumulative, _players, _display, _ids = scoreboard.get_cumulative_stats()
        player_id = score['player_id']

        self.assertEqual('2026-08-20T22:02:00Z', score['played_at'])
        self.assertEqual('2026-08-28T10:00:00Z', score['imported_at'])
        self.assertEqual('2026-08-20T21:50:00Z', score['last_180_at'])
        self.assertEqual('2026-08-20T21:59:00Z', score['best_checkout_at'])
        self.assertEqual(1, score['bull_finishes'])
        self.assertEqual('2026-08-20T21:58:59Z', score['last_bull_finish_at'])
        self.assertEqual(
            scoreboard.format_local_datetime('2026-08-20T21:50:00Z'),
            cumulative[player_id]['last180_date'],
        )
        self.assertEqual(
            scoreboard.format_local_datetime('2026-08-20T22:02:00Z'),
            score['date'],
        )
        self.assertEqual(1, cumulative[player_id]['bull_finishes'])
        self.assertEqual(
            scoreboard.format_local_datetime('2026-08-20T21:58:59Z'),
            cumulative[player_id]['last_bull_finish_date'],
        )
        player_card = self.client.get('/api/player_card').get_json()['player']
        self.assertEqual(1, player_card['bull_finishes'])
        self.assertEqual(
            scoreboard.format_local_datetime('2026-08-20T21:58:59Z'),
            player_card['last_bull_finish_date'],
        )
        with patch('app.render_template', return_value='ok') as render:
            self.client.get('/')
        highest_finish = render.call_args.kwargs['highest_finish']
        self.assertEqual(
            scoreboard.format_local_datetime('2026-08-20T21:59:00Z'),
            highest_finish[0]['finish_date'],
        )

    def test_classic_26_and_checkout_frequency_are_preserved_and_aggregated(self):
        result = {
            'id': '01800000-0000-7000-8000-000000000026',
            'finishedAt': '2026-08-20T20:00:00Z',
            'players': [{'id': 'p1', 'name': 'Alice'}],
            'scores': [{'legs': 2}],
            'matchStats': [{
                'playerId': 'p1', 'dartsThrown': 18, 'average': 50,
                'checkoutPoints': 40, 'checkouts': 3, 'checkoutsHit': 2,
            }],
            'games': [
                {
                    'winnerPlayerId': 'p1', 'winner': 0,
                    'turns': [
                        {
                            'playerId': 'p1', 'points': 26, 'score': 275, 'busted': False,
                            'throws': [
                                {'segment': {'name': 'S20', 'number': 20, 'multiplier': 1}},
                                {'segment': {'name': 'S1', 'number': 1, 'multiplier': 1}},
                                {'segment': {'name': 'S5', 'number': 5, 'multiplier': 1}},
                            ],
                        },
                        {
                            'playerId': 'p1', 'points': 40, 'score': 0, 'busted': False,
                            'throws': [{'segment': {'name': 'D20', 'number': 20, 'multiplier': 2}}],
                        },
                    ],
                },
                {
                    'winnerPlayerId': 'p1', 'winner': 0,
                    'turns': [
                        {
                            'playerId': 'p1', 'points': 26, 'score': 275, 'busted': True,
                            'throws': [
                                {'segment': {'name': 'S1', 'number': 1, 'multiplier': 1}},
                                {'segment': {'name': 'S5', 'number': 5, 'multiplier': 1}},
                                {'segment': {'name': 'S20', 'number': 20, 'multiplier': 1}},
                            ],
                        },
                        {
                            'playerId': 'p1', 'points': 40, 'score': 0, 'busted': False,
                            'throws': [{'segment': {'name': 'D20', 'number': 20, 'multiplier': 2}}],
                        },
                    ],
                },
            ],
            'legStats': [
                {'winner': 0, 'stats': [{'playerId': 'p1', 'checkoutPoints': 40, 'dartsThrown': 9}]},
                {'winner': 0, 'stats': [{'playerId': 'p1', 'checkoutPoints': 40, 'dartsThrown': 9}]},
            ],
        }

        scoreboard.import_match_result_to_scores(result, match_id=result['id'])
        score = json.loads(self.paths['SCORES_FILE'].read_text(encoding='utf-8'))[0]
        cumulative, _players, _display, _ids = scoreboard.get_cumulative_stats()

        self.assertEqual(1, score['classic_26'])
        self.assertEqual({'40': 2}, score['checkout_finishes'])
        self.assertEqual(1, cumulative[score['player_id']]['classic_26'])
        self.assertEqual({'40': 2}, cumulative[score['player_id']]['checkout_finishes'])

        with patch('app.render_template', return_value='ok') as render:
            self.client.get('/')
        self.assertEqual(1, render.call_args.kwargs['most_classic_26'][0]['classic_26'])
        self.assertEqual(40, render.call_args.kwargs['favorite_checkouts'][0]['favorite_checkout'])
        self.assertEqual(2, render.call_args.kwargs['favorite_checkouts'][0]['favorite_checkout_count'])

        player_card = self.client.get('/api/player_card').get_json()['player']
        self.assertEqual(1, player_card['classic_26'])
        self.assertEqual(40, player_card['favorite_checkout'])
        self.assertEqual(2, player_card['favorite_checkout_count'])
        self.assertEqual(2, player_card['most_hit_count'])
        self.assertIn(player_card['most_hit_segment'], {'S20', 'S1', 'S5', 'D20'})

    def test_full_refresh_enriches_legacy_score_instead_of_duplicating_it(self):
        result = {
            'id': '01800000-0000-7000-8000-000000000002',
            'finishedAt': '2026-08-20T20:00:00Z',
            'players': [{'id': 'p1', 'name': 'Alice'}],
            'scores': [{'legs': 1}],
            'matchStats': [{
                'playerId': 'p1', 'dartsThrown': 18, 'average': 50,
                'total180': 0, 'checkoutPoints': 40,
            }],
            'games': [{
                'finishedAt': '2026-08-20T20:00:00Z',
                'winnerPlayerId': 'p1', 'winner': 0,
                'turns': [{'playerId': 'p1', 'points': 40, 'finishedAt': '2026-08-20T20:00:00Z'}],
            }],
            'legStats': [{'winner': 0, 'stats': [{'playerId': 'p1', 'checkoutPoints': 40, 'dartsThrown': 18}]}],
        }
        match_id = result['id']
        scoreboard.import_match_result_to_scores(result, match_id=match_id)
        scores = json.loads(self.paths['SCORES_FILE'].read_text(encoding='utf-8'))
        legacy = scores[0]
        for key in ('autodarts_match_id', 'source', 'played_at', 'imported_at', 'best_checkout_at', 'last_180_at'):
            legacy.pop(key, None)
        legacy['date'] = '28.08.2026 10:00'
        legacy['score_hash'] = scoreboard.compute_score_hash(legacy)
        self.paths['SCORES_FILE'].write_text(json.dumps([legacy]), encoding='utf-8')

        scoreboard.import_match_result_to_scores(result, match_id=match_id)
        migrated = json.loads(self.paths['SCORES_FILE'].read_text(encoding='utf-8'))

        self.assertEqual(1, len(migrated))
        self.assertEqual(match_id, migrated[0]['autodarts_match_id'])
        self.assertEqual(
            scoreboard.format_local_datetime('2026-08-20T20:00:00Z'),
            migrated[0]['date'],
        )

    def test_multiplayer_matches_keep_one_score_per_player_on_refresh(self):
        for player_count in (2, 3, 4):
            with self.subTest(player_count=player_count):
                for name in ('PLAYERS_FILE', 'SCORES_FILE', 'BOT_SCORES_FILE'):
                    self.paths[name].write_text('[]\n', encoding='utf-8')

                match_id = f'01800000-0000-7000-8000-00000000000{player_count}'
                player_ids = [f'p{index}' for index in range(1, player_count + 1)]

                def match_result(order, average_offset=0):
                    return {
                        'id': match_id,
                        'finishedAt': '2026-08-20T20:00:00Z',
                        'players': [
                            {'id': player_id, 'name': f'Player {player_id[1:]}'}
                            for player_id in order
                        ],
                        'scores': [
                            {'legs': index}
                            for index, _player_id in enumerate(order, start=1)
                        ],
                        'matchStats': [
                            {
                                'playerId': player_id,
                                'dartsThrown': 30,
                                'average': 40 + int(player_id[1:]) + average_offset,
                                'total180': 0,
                                'checkoutPoints': 0,
                            }
                            for player_id in order
                        ],
                        'games': [],
                    }

                scoreboard.import_match_result_to_scores(
                    match_result(player_ids), match_id=match_id
                )
                first_import = json.loads(
                    self.paths['SCORES_FILE'].read_text(encoding='utf-8')
                )
                self.assertEqual(player_count, len(first_import))
                self.assertEqual(
                    player_count,
                    len({score['player_id'] for score in first_import}),
                )

                # Ein vollständiger Neuabgleich kann Spieler in anderer Reihenfolge
                # liefern. Trotzdem muss genau die Zeile dieses Spielers aktualisiert
                # werden und keine andere Zeile desselben Matches.
                scoreboard.import_match_result_to_scores(
                    match_result(list(reversed(player_ids)), average_offset=10),
                    match_id=match_id,
                )
                refreshed = json.loads(
                    self.paths['SCORES_FILE'].read_text(encoding='utf-8')
                )
                players = json.loads(
                    self.paths['PLAYERS_FILE'].read_text(encoding='utf-8')
                )
                names_by_id = {player['id']: player['name'] for player in players}
                averages_by_name = {
                    names_by_id[score['player_id']]: score['average']
                    for score in refreshed
                }

                self.assertEqual(player_count, len(refreshed))
                self.assertEqual(
                    {
                        f'Player {index}': 50 + index
                        for index in range(1, player_count + 1)
                    },
                    averages_by_name,
                )

    @patch('app.start_autodarts_import', return_value=True)
    def test_scheduler_immediately_runs_overdue_persisted_check(self, start_import):
        now = scoreboard.parse_datetime('2026-08-28T10:00:00Z')
        self.paths['AUTODARTS_SYNC_STATE_FILE'].write_text(json.dumps({
            'initial_import_completed': True,
            'next_check_at': '2026-08-28T09:00:00Z',
            'interval_minutes': 37,
            'pending_matches': {},
        }), encoding='utf-8')

        started = scoreboard.autodarts_scheduler_tick({
            'autodarts_enabled': True,
            'autodarts_interval_minutes': 37,
        }, now=now)
        state = json.loads(self.paths['AUTODARTS_SYNC_STATE_FILE'].read_text(encoding='utf-8'))

        self.assertTrue(started)
        start_import.assert_called_once_with(mode='auto')
        self.assertEqual('2026-08-28T10:05:00Z', state['next_check_at'])


if __name__ == '__main__':
    unittest.main()
