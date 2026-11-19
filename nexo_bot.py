"""Small official-style SDK for the implemented NEXO Bot API 1.0 methods."""
import time
import requests

class NexoBot:
    def __init__(self, token, base_url):
        self.token = token
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'X-NEXO-BOT-TOKEN': token})

    def call(self, method, **payload):
        response = self.session.post(f'{self.base_url}/bot{self.token}/{method}', json=payload, timeout=35)
        data = response.json()
        if not response.ok or not data.get('ok'):
            raise RuntimeError(data.get('description') or data.get('error') or f'HTTP {response.status_code}')
        return data.get('result')

    def get_me(self): return self.call('getMe')
    def get_updates(self, offset=0, limit=20, timeout=15):
        return self.call('getUpdates', offset=offset, limit=limit, timeout=timeout)
    def send_message(self, chat_id, text): return self.call('sendMessage', chat_id=chat_id, text=text)
    def get_chat(self, chat_id): return self.call('getChat', chat_id=chat_id)
    def get_user(self, user_id): return self.call('getUser', chat_id=user_id)

    def polling(self, handler, stop=lambda: False):
        """Long polling loop. handler(update) is called before advancing offset."""
        offset = 0
        while not stop():
            for update in self.get_updates(offset=offset, timeout=15):
                handler(update)
                offset = int(update['update_id']) + 1

if __name__ == '__main__':
    import os
    bot = NexoBot(os.environ['NEXO_BOT_TOKEN'], os.environ['NEXO_BASE_URL'])
    print(bot.get_me())
