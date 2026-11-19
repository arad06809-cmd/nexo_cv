import ast, pathlib, sqlite3, unittest
ROOT=pathlib.Path(__file__).resolve().parent
class ChannelExtensionTests(unittest.TestCase):
    def test_extension_compiles(self):
        source=(ROOT/'channels_ext.py').read_text(encoding='utf-8')
        compile(source,'channels_ext.py','exec')
    def test_channel_schema_executes(self):
        tree=ast.parse((ROOT/'channels_ext.py').read_text(encoding='utf-8'))
        schema=None
        for node in ast.walk(tree):
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='schema' for t in node.targets):
                schema=ast.literal_eval(node.value); break
        self.assertIsNotNone(schema)
        con=sqlite3.connect(':memory:'); con.executescript(schema)
        names={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'nx_chat_settings','nx_invites','nx_join_requests','nx_polls','nx_poll_votes','nx_topics','nx_moderation','nx_chat_events'}<=names)
        con.close()
    def test_api_routes_registered_in_source(self):
        s=(ROOT/'channels_ext.py').read_text(encoding='utf-8')
        for route in ('/api/chats/<int:cid>/features','/api/chats/<int:cid>/invites','/api/invites/<token>/join','/api/chats/<int:cid>/join-requests','/api/chats/<int:cid>/polls','/api/polls/<int:pid>/vote','/api/chats/<int:cid>/topics','/api/chats/<int:cid>/moderation','/api/chats/<int:cid>/events','/api/chats/<int:cid>/stats'):
            self.assertIn(route,s)
        self.assertIn('register_channel_extensions(app,', (ROOT/'app.py').read_text(encoding='utf-8'))
if __name__=='__main__': unittest.main()
