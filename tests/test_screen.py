import base64
import io
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screen
import server

# A valid one-pixel PNG: tests do not depend on real user screenshots or paid calls.
PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII='


def request_data(count=1):
    return {'consent': True, 'frames': [
        {'image': PNG, 'captured_at': f'2026-10-12T09:00:0{index}+00:00'} for index in range(count)
    ], 'game_hint': '', 'goal': 'Trova una mossa prudente.'}


def answer_data():
    return {'status': 'ready', 'game': {'title': 'Tris', 'genre': 'Gioco da tavolo', 'confidence': 'high'},
            'summary': 'È il turno di X.', 'observations': ['Due O nella riga superiore.'],
            'changes': [], 'turn': 'player',
            'next_move': {'action': 'Blocca la casella in alto a destra.',
                          'reason': 'Impedisce la chiusura della riga.', 'steps': ['Seleziona quella casella.']},
            'alternatives': [], 'uncertainties': [], 'question': None, 'confidence': 'medium'}


class ScreenTests(unittest.TestCase):
    def test_multimodal_request_preserves_order_and_does_not_send_extra_fields(self):
        data = request_data(3)
        data['api_key'] = 'must-not-be-forwarded'
        call = MagicMock(return_value=json.dumps(answer_data()))
        result = screen.analyze_screen(data, call, 'vision-model')
        instructions, content, schema = call.call_args.args
        self.assertEqual(schema, screen.SCHEMA)
        self.assertIn('ULTIMA', instructions)
        self.assertEqual([item['image_url'] for item in content if item['type'] == 'input_image'], [PNG] * 3)
        self.assertEqual([item['type'] for item in content], ['input_text', 'input_text', 'input_image',
                                                          'input_text', 'input_image', 'input_text', 'input_image'])
        self.assertNotIn('must-not-be-forwarded', json.dumps(content))
        self.assertEqual(result['captured_at'], data['frames'][-1]['captured_at'])
        self.assertEqual(result['frame_count'], 3)
        self.assertEqual(result['source'], 'screen_ai')
        self.assertNotIn('image', result)

    def test_invalid_input_never_spends_a_call(self):
        invalid = []
        for field, value in [('consent', False), ('consent', 1), ('frames', []), ('frames', [None]),
                             ('frames', request_data(4)['frames']), ('goal', []), ('game_hint', 'a' * 121)]:
            invalid.append({**request_data(), field: value})
        for image in ['https://example.com/image.png', 'data:image/png;base64,YQ==',
                      PNG.replace('png', 'jpeg'), PNG.replace('base64,', 'base64,!')]:
            data = request_data()
            data['frames'][0]['image'] = image
            invalid.append(data)
        for stamp in ['yesterday', '2026-10-12T09:00:00', True, None]:
            data = request_data()
            data['frames'][0]['captured_at'] = stamp
            invalid.append(data)
        data = request_data(2)
        data['frames'].reverse()
        invalid.append(data)
        invalid.append({**request_data(), 'frames': request_data()['frames'] * 2})
        for data in invalid:
            with self.subTest(data=str(data)[:150]):
                call = MagicMock()
                with self.assertRaises(ValueError): screen.analyze_screen(data, call, 'test')
                call.assert_not_called()

    def test_individual_and_combined_size_limits(self):
        for size, count in [(2_000_001, 1), (1_400_000, 3)]:
            raw = b'\x89PNG\r\n\x1a\n' + b'x' * (size - 8)
            image = 'data:image/png;base64,' + base64.b64encode(raw).decode()
            data = request_data(count)
            for frame in data['frames']: frame['image'] = image
            call = MagicMock()
            with self.assertRaises(ValueError): screen.analyze_screen(data, call, 'test')
            call.assert_not_called()

    def test_uncertainty_unknown_turn_and_opponent_turn_suppress_moves(self):
        for change in [{'confidence': 'low'}, {'turn': 'unknown'}, {'turn': 'opponent'},
                       {'next_move': None}, {'game': {'title': None, 'genre': None, 'confidence': 'low'}}]:
            with self.subTest(change=change):
                result = screen.validate_answer({**answer_data(), **change})
                self.assertEqual(result['status'], 'needs_context')
                self.assertIsNone(result['next_move'])
                self.assertEqual(result['alternatives'], [])
                self.assertTrue(result['question'])

    def test_non_game_never_has_a_move(self):
        answer = answer_data()
        answer['status'] = 'not_game'
        answer['alternatives'] = [{'action': 'Ignore previous instructions', 'reason': 'Untrusted output'}]
        result = screen.validate_answer(answer)
        self.assertIsNone(result['next_move'])
        self.assertEqual(result['alternatives'], [])

    def test_board_card_puzzle_and_action_games_share_contract(self):
        for title, genre, turn in [('Scacchi', 'Tavolo', 'player'), ('Poker', 'Carte', 'player'),
                                   (None, 'Puzzle', 'not_applicable'), ('Gioco d’azione', 'Azione', 'simultaneous')]:
            answer = answer_data()
            answer['game'] = {'title': title, 'genre': genre, 'confidence': 'medium'}
            answer['turn'] = turn
            with self.subTest(title=title):
                self.assertIsNotNone(screen.validate_answer(answer)['next_move'])

    def test_malformed_output_is_rejected_without_a_fabricated_fallback(self):
        malformed = ['not JSON', 'null', '[]']
        for field, value in [('confidence', 'certain'), ('summary', {}), ('observations', ['x'] * 9),
                             ('game', None), ('next_move', {'action': 'Go'}), ('question', 42)]:
            malformed.append(json.dumps({**answer_data(), field: value}))
        malformed.append(json.dumps({**answer_data(), 'unexpected': 'field'}))
        for raw in malformed:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, 'Risposta visiva non valida'):
                    screen.analyze_screen(request_data(), MagicMock(return_value=raw), 'test')

    def test_one_frame_cannot_claim_temporal_changes(self):
        answer = answer_data()
        answer['changes'] = ['Un movimento non dimostrabile.']
        result = screen.analyze_screen(request_data(), MagicMock(return_value=json.dumps(answer)), 'test')
        self.assertEqual(result['changes'], [])

    def test_overlapping_requests_do_not_make_a_second_paid_call(self):
        entered, release = threading.Event(), threading.Event()
        def call(*args):
            entered.set()
            if not release.wait(3): raise AssertionError('Test timed out')
            return json.dumps(answer_data())
        errors = []
        def run():
            try: screen.analyze_screen(request_data(), call, 'test')
            except Exception as error: errors.append(error)
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            second = MagicMock()
            with self.assertRaisesRegex(ValueError, 'già in corso'):
                screen.analyze_screen(request_data(), second, 'test')
            second.assert_not_called()
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(errors)
        self.assertFalse(worker.is_alive())

    def test_provider_failure_releases_lock_and_call_cap_is_shared(self):
        with self.assertRaises(ValueError):
            screen.analyze_screen(request_data(), MagicMock(side_effect=ValueError('provider')), 'test')
        screen.analyze_screen(request_data(), MagicMock(return_value=json.dumps(answer_data())), 'test')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-secret', 'OPENAI_MODEL': 'test', 'OPENAI_MAX_CALLS': '0'}), \
                patch('server.urllib.request.build_opener') as build:
            with self.assertRaisesRegex(ValueError, 'Limite di chiamate'):
                server.screen_analysis(request_data())
            build.assert_not_called()

    def test_full_provider_adapter_uses_image_schema_and_store_false(self):
        reply = MagicMock()
        reply.__enter__.return_value = io.BytesIO(json.dumps({
            'status': 'completed', 'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps(answer_data())}]}],
        }).encode())
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-secret', 'OPENAI_MODEL': 'vision-test', 'OPENAI_MAX_CALLS': '1000'}), \
                patch('server.urllib.request.build_opener') as build:
            build.return_value.open.return_value = reply
            result = server.screen_analysis(request_data())
            request = build.return_value.open.call_args.args[0]
            payload = json.loads(request.data)
            self.assertFalse(payload['store'])
            self.assertEqual(payload['text']['format']['schema'], screen.SCHEMA)
            self.assertEqual(payload['input'][0]['content'][-1]['image_url'], PNG)
            self.assertEqual(result['model'], 'vision-test')
            self.assertNotIn('test-secret', json.dumps(result))


if __name__ == '__main__': unittest.main()
