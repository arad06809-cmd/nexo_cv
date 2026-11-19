import ast
import pathlib
import sqlite3
import unittest

ROOT = pathlib.Path(__file__).resolve().parent

class StaticReleaseTests(unittest.TestCase):
    def test_python_sources_compile(self):
        for name in ('app.py', 'infinity_ext.py', 'nexo_internal_runner.py'):
            source = (ROOT / name).read_text(encoding='utf-8')
            compile(source, name, 'exec')

    def test_extension_schema_is_valid_sqlite(self):
        tree = ast.parse((ROOT / 'infinity_ext.py').read_text(encoding='utf-8'))
        schema = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'schema' for t in node.targets):
                schema = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(schema)
        con = sqlite3.connect(':memory:')
        con.executescript(schema)
        names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        self.assertTrue({'nx_bots','nx_bot_updates','nx_bot_projects','nx_market_products','nx_market_orders','nx_market_wallets','nx_market_ledger'} <= names)
        con.close()

    def test_required_api_routes_are_present_in_source(self):
        source = (ROOT / 'infinity_ext.py').read_text(encoding='utf-8')
        for route in ('/api/bots','/api/bots/<int:bid>/rename','/api/bots/<int:bid>/project','/api/bots/<int:bid>/run','/api/bots/<int:bid>/stop','/api/bots/<int:bid>/logs','/api/bots/<int:bid>/message','/api/bots/<int:bid>/inbox','/bot<token>/getMe','/bot<token>/getUpdates','/bot<token>/sendMessage','/api/market/products','/api/market/orders','/api/market/wallet'):
            self.assertIn(route, source)

    def test_search_supports_exact_public_channel_and_bot_mentions(self):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        self.assertIn("order by case when lower(username)=lower(?) then 0 else 1 end", source)
        self.assertIn("openname:${username}", source)
        self.assertIn("s.lstrip('@#').strip()", source)


    def test_motion_emoji_and_studio_integration_is_present(self):
        app_source = (ROOT / 'app.py').read_text(encoding='utf-8')
        ext_source = (ROOT / 'infinity_ext.py').read_text(encoding='utf-8')
        for token in ("'custom_emoji'", "NEXO_STICKERS", 'emoji-play', 'rx-play', 'NEXO_STICKER_ART', "data-pt=\"mine\"", '/emoji-assets/'):
            self.assertIn(token, app_source)
        for token in ('/emoji-studio', '/bot-studio', '/emoji-assets/<int:eid>', 'X-CSRF', 'animateTransform', 'monaco-editor'):
            self.assertIn(token, ext_source)
        self.assertIn('nx_custom_emoji', ext_source)

    def test_botfather_private_chat_ui_is_present(self):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        for feature in ('data-a=\"botfather\"', 'function botFatherPage()', '/newbot', '/mybots', 'data-bf-token', 'data-bf-delete', 'data-bf-code', 'data-bf-save', 'data-bf-run', 'data-bf-test', 'data-bf-sendtest', 'bf-glass'):
            self.assertIn(feature, source)
        self.assertEqual(source.count('register_extensions(app,'), 1)

if __name__ == '__main__':
    unittest.main()
