/** Minimal SDK for implemented NEXO Bot API 1.0 methods (Node 18+). */
export class NexoBot {
  constructor(token, baseUrl) {
    this.token = token;
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }
  async call(method, payload = {}) {
    const res = await fetch(`${this.baseUrl}/bot${this.token}/${method}`, {
      method: 'POST', headers: { 'content-type': 'application/json', 'X-NEXO-BOT-TOKEN': this.token },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.description || data.error || `HTTP ${res.status}`);
    return data.result;
  }
  getMe() { return this.call('getMe'); }
  getUpdates(options = {}) { return this.call('getUpdates', options); }
  sendMessage(chat_id, text) { return this.call('sendMessage', { chat_id, text }); }
  getChat(chat_id) { return this.call('getChat', { chat_id }); }
  getUser(user_id) { return this.call('getUser', { chat_id: user_id }); }
}
