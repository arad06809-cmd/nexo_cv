import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nexo_internal_runner import execute_python

class InternalRunnerTests(unittest.TestCase):
    def test_callback_runs_and_collects_bot_reply(self):
        source = "def on_message(message, bot):\n    print('received')\n    bot.send_message(message['chat']['id'], 'hello')\n"
        result = execute_python(source, 'message', {'chat': {'id': 42}, 'text': 'hi'})
        self.assertTrue(result['ok'], result)
        self.assertIn('received', result['output'])
        self.assertEqual(result['sent'], [{'chat_id': 42, 'text': 'hello'}])

    def test_disallowed_import_is_rejected(self):
        result = execute_python('import os\n', 'start')
        self.assertFalse(result['ok'])
        self.assertIn('غیرمجاز', result['error'])

    def test_infinite_loop_is_stopped(self):
        result = execute_python('while True:\n    pass\n', 'start')
        self.assertFalse(result['ok'])

    def test_line_limit_is_enforced(self):
        result = execute_python('\n'.join(['print(1)'] * 101), 'start')
        self.assertFalse(result['ok'])
        self.assertIn('۱۰۰ خط', result['error'])

if __name__ == '__main__':
    unittest.main()
