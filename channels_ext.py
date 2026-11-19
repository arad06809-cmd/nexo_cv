"""NEXO Telegram-style groups and channels: additive APIs and SQLite schema."""
import secrets, time
from flask import request, jsonify

def register_channel_extensions(app, *, auth, q, x, db, now, Error, audit=None):
    schema = """
    CREATE TABLE IF NOT EXISTS nx_chat_settings(
      chat_id INTEGER PRIMARY KEY, slow_mode INTEGER NOT NULL DEFAULT 0,
      join_approval INTEGER NOT NULL DEFAULT 0, members_visible INTEGER NOT NULL DEFAULT 1,
      only_admins INTEGER NOT NULL DEFAULT 0, max_members INTEGER NOT NULL DEFAULT 200000,
      welcome TEXT NOT NULL DEFAULT '', rules TEXT NOT NULL DEFAULT '', updated INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS nx_invites(
      id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, token TEXT NOT NULL UNIQUE,
      creator INTEGER NOT NULL, uses_limit INTEGER, uses INTEGER NOT NULL DEFAULT 0,
      expires_at INTEGER, revoked INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS nx_invites_chat ON nx_invites(chat_id,revoked);
    CREATE TABLE IF NOT EXISTS nx_join_requests(
      id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
      invite_id INTEGER, status TEXT NOT NULL DEFAULT 'pending', created INTEGER NOT NULL,
      reviewed_by INTEGER, reviewed_at INTEGER, UNIQUE(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS nx_polls(
      id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, message_id INTEGER,
      creator INTEGER NOT NULL, question TEXT NOT NULL, options TEXT NOT NULL,
      multiple INTEGER NOT NULL DEFAULT 0, anonymous INTEGER NOT NULL DEFAULT 1,
      closed INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS nx_poll_votes(
      poll_id INTEGER NOT NULL, user_id INTEGER NOT NULL, option_index INTEGER NOT NULL,
      created INTEGER NOT NULL, PRIMARY KEY(poll_id,user_id,option_index));
    CREATE TABLE IF NOT EXISTS nx_topics(
      id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, title TEXT NOT NULL,
      icon TEXT NOT NULL DEFAULT '💬', creator INTEGER NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
      created INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS nx_moderation(
      id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
      action TEXT NOT NULL, until_at INTEGER, reason TEXT NOT NULL DEFAULT '',
      actor INTEGER NOT NULL, created INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS nx_moderation_active ON nx_moderation(chat_id,user_id,action,until_at);
    CREATE TABLE IF NOT EXISTS nx_chat_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, actor INTEGER NOT NULL,
      action TEXT NOT NULL, target INTEGER, detail TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL);
    """
    with app.app_context():
        db().executescript(schema)
        db().commit()

    def chat(cid):
        c=q('SELECT * FROM chats WHERE id=? AND type IN ("group","channel")',(cid,),True)
        if not c: raise Error('گروه یا کانال پیدا نشد',404)
        return c
    def member(cid, uid):
        m=q('SELECT * FROM members WHERE chat_id=? AND user_id=?',(cid,uid),True)
        if not m: raise Error('عضو این گفتگو نیستید',403)
        return m
    def manager(m):
        if m['role'] not in ('owner','admin'): raise Error('فقط مالک یا مدیر اجازه دارد',403)
    def settings(cid):
        r=q('SELECT * FROM nx_chat_settings WHERE chat_id=?',(cid,),True)
        if not r:
            x('INSERT OR IGNORE INTO nx_chat_settings(chat_id,updated) VALUES(?,?)',(cid,now()))
            r=q('SELECT * FROM nx_chat_settings WHERE chat_id=?',(cid,),True)
        return dict(r)
    def event(cid, actor, action, target=None, detail=''):
        x('INSERT INTO nx_chat_events(chat_id,actor,action,target,detail,created) VALUES(?,?,?,?,?,?)',
          (cid,actor,action,target,str(detail)[:300],now()))
        if audit: audit('chat',action,'chat=%s target=%s %s'%(cid,target,detail),actor)

    @app.get('/api/chats/<int:cid>/features')
    def nx_chat_features(cid):
        u=auth(); c=chat(cid); m=member(cid,u['id']); s=settings(cid)
        banned=q("SELECT 1 FROM nx_moderation WHERE chat_id=? AND user_id=? AND action='ban' AND (until_at IS NULL OR until_at>?) ORDER BY id DESC LIMIT 1",(cid,u['id'],now()),True)
        if banned: raise Error('دسترسی شما به این گفتگو مسدود شده است',403)
        counts={'members':q('SELECT count(*) n FROM members WHERE chat_id=?',(cid,),True)['n'],
                'messages':q('SELECT count(*) n FROM messages WHERE chat_id=? AND deleted=0',(cid,),True)['n']}
        return jsonify({'settings':s,'counts':counts,'can_manage':m['role'] in ('owner','admin'),
          'pending_requests':q("SELECT count(*) n FROM nx_join_requests WHERE chat_id=? AND status='pending'",(cid,),True)['n'] if m['role'] in ('owner','admin') else 0})

    @app.put('/api/chats/<int:cid>/features')
    def nx_chat_settings_update(cid):
        u=auth(); chat(cid); m=member(cid,u['id']); manager(m); d=request.get_json(silent=True) or {}
        allowed={'slow_mode':(0,3600),'join_approval':(0,1),'members_visible':(0,1),'only_admins':(0,1),'max_members':(2,200000)}
        cur=settings(cid); vals={}
        for k,(lo,hi) in allowed.items():
            if k in d:
                try: v=int(d[k])
                except: raise Error('مقدار تنظیم نامعتبر است')
                if not lo<=v<=hi: raise Error('مقدار تنظیم خارج از محدوده است')
                vals[k]=v
            else: vals[k]=cur[k]
        welcome=str(d.get('welcome',cur['welcome']))[:500]
        rules=str(d.get('rules',cur['rules']))[:2000]
        x('UPDATE nx_chat_settings SET slow_mode=?,join_approval=?,members_visible=?,only_admins=?,max_members=?,welcome=?,rules=?,updated=? WHERE chat_id=?',
          (vals['slow_mode'],vals['join_approval'],vals['members_visible'],vals['only_admins'],vals['max_members'],welcome,rules,now(),cid))
        event(cid,u['id'],'settings_updated',None,'settings changed')
        return jsonify({'ok':True,'settings':settings(cid)})

    @app.post('/api/chats/<int:cid>/invites')
    def nx_invite_create(cid):
        u=auth(); chat(cid); m=member(cid,u['id']); manager(m); d=request.get_json(silent=True) or {}
        uses=d.get('uses_limit'); exp=d.get('expires_in')
        try: uses=int(uses) if uses not in (None,'') else None; exp=int(exp) if exp not in (None,'') else None
        except: raise Error('محدودیت دعوت نامعتبر است')
        if uses is not None and not 1<=uses<=10000: raise Error('تعداد استفاده باید بین ۱ تا ۱۰۰۰۰ باشد')
        if exp is not None and not 60<=exp<=31536000: raise Error('انقضا باید بین یک دقیقه تا یک سال باشد')
        token=secrets.token_urlsafe(24)
        iid=x('INSERT INTO nx_invites(chat_id,token,creator,uses_limit,expires_at,created) VALUES(?,?,?,?,?,?)',
          (cid,token,u['id'],uses,now()+exp if exp else None,now()))
        event(cid,u['id'],'invite_created',None,'invite=%s'%iid)
        return jsonify({'id':iid,'token':token,'link':'/join/'+token})

    @app.get('/api/chats/<int:cid>/invites')
    def nx_invite_list(cid):
        u=auth(); chat(cid); m=member(cid,u['id']); manager(m)
        rows=q('SELECT id,token,creator,uses_limit,uses,expires_at,revoked,created FROM nx_invites WHERE chat_id=? ORDER BY id DESC',(cid,))
        return jsonify({'items':[dict(r) for r in rows]})

    @app.delete('/api/chats/<int:cid>/invites/<int:iid>')
    def nx_invite_revoke(cid,iid):
        u=auth(); chat(cid); m=member(cid,u['id']); manager(m)
        x('UPDATE nx_invites SET revoked=1 WHERE id=? AND chat_id=?',(iid,cid))
        event(cid,u['id'],'invite_revoked',None,'invite=%s'%iid)
        return jsonify({'ok':True})

    @app.post('/api/invites/<token>/join')
    def nx_invite_join(token):
        u=auth(); inv=q('SELECT * FROM nx_invites WHERE token=?',(token,),True)
        if not inv or inv['revoked'] or (inv['expires_at'] and inv['expires_at']<=now()) or (inv['uses_limit'] is not None and inv['uses']>=inv['uses_limit']):
            raise Error('لینک دعوت معتبر نیست یا منقضی شده است',404)
        c=chat(inv['chat_id'])
        if q('SELECT 1 FROM nx_moderation WHERE chat_id=? AND user_id=? AND action="ban" AND (until_at IS NULL OR until_at>?)',(c['id'],u['id'],now()),True):
            raise Error('امکان عضویت ندارید',403)
        if q('SELECT 1 FROM members WHERE chat_id=? AND user_id=?',(c['id'],u['id']),True):
            return jsonify({'id':c['id'],'joined':True,'already_member':True})
        s=settings(c['id'])
        if q('SELECT count(*) n FROM members WHERE chat_id=?',(c['id'],),True)['n']>=s['max_members']:
            raise Error('ظرفیت این گفتگو تکمیل است',409)
        if s['join_approval']:
            x("INSERT OR IGNORE INTO nx_join_requests(chat_id,user_id,invite_id,status,created) VALUES(?,?,?,'pending',?)",(c['id'],u['id'],inv['id'],now()))
            return jsonify({'id':c['id'],'requested':True})
        x('INSERT OR IGNORE INTO members(chat_id,user_id,role) VALUES(?,?,"member")',(c['id'],u['id']))
        x('UPDATE nx_invites SET uses=uses+1 WHERE id=?',(inv['id'],))
        event(c['id'],u['id'],'joined')
        return jsonify({'id':c['id'],'joined':True})

    @app.get('/api/chats/<int:cid>/join-requests')
    def nx_requests(cid):
        u=auth(); chat(cid); m=member(cid,u['id']); manager(m)
        rows=q("SELECT r.id,r.user_id,r.created,u.username,u.nick,u.avatar FROM nx_join_requests r JOIN users u ON u.id=r.user_id WHERE r.chat_id=? AND r.status='pending' ORDER BY r.id",(cid,))
        return jsonify({'items':[dict(r) for r in rows]})

    @app.post('/api/chats/<int:cid>/join-requests/<int:rid>')
    def nx_request_review(cid,rid):
        u=auth(); chat(cid); m=member(cid,u['id']); manager(m); d=request.get_json(silent=True) or {}
        action=d.get('action')
        if action not in ('approve','reject'): raise Error('تصمیم نامعتبر است')
        r=q("SELECT * FROM nx_join_requests WHERE id=? AND chat_id=? AND status='pending'",(rid,cid),True)
        if not r: raise Error('درخواست پیدا نشد',404)
        if action=='approve':
            s=settings(cid)
            if q('SELECT count(*) n FROM members WHERE chat_id=?',(cid,),True)['n']>=s['max_members']: raise Error('ظرفیت تکمیل است',409)
            x('INSERT OR IGNORE INTO members(chat_id,user_id,role) VALUES(?,?,"member")',(cid,r['user_id']))
            if r['invite_id']: x('UPDATE nx_invites SET uses=uses+1 WHERE id=?',(r['invite_id'],))
        x('UPDATE nx_join_requests SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?',(action,u['id'],now(),rid))
        event(cid,u['id'],'join_'+action,r['user_id'])
        return jsonify({'ok':True})

    @app.post('/api/chats/<int:cid>/polls')
    def nx_poll_create(cid):
        u=auth(); c=chat(cid); m=member(cid,u['id']); s=settings(cid)
        if c['type']=='channel' and m['role'] not in ('owner','admin'): raise Error('فقط مدیر کانال می‌تواند نظرسنجی بسازد',403)
        d=request.get_json(silent=True) or {}; question=str(d.get('question') or '').strip()[:250]
        opts=d.get('options')
        if not question or not isinstance(opts,list): raise Error('سوال و گزینه‌ها الزامی است')
        opts=[str(v).strip()[:100] for v in opts if str(v).strip()]
        if not 2<=len(opts)<=10: raise Error('نظرسنجی باید ۲ تا ۱۰ گزینه داشته باشد')
        pid=x('INSERT INTO nx_polls(chat_id,creator,question,options,multiple,anonymous,created) VALUES(?,?,?,?,?,?,?)',
          (cid,u['id'],question,__import__('json').dumps(opts,ensure_ascii=False),int(bool(d.get('multiple'))),int(bool(d.get('anonymous',True))),now()))
        event(cid,u['id'],'poll_created',None,'poll=%s'%pid)
        return jsonify({'id':pid,'question':question,'options':opts,'multiple':bool(d.get('multiple')),'anonymous':bool(d.get('anonymous',True))})

    @app.get('/api/chats/<int:cid>/polls')
    def nx_poll_list(cid):
        import json
        u=auth(); chat(cid); member(cid,u['id'])
        rows=q('SELECT * FROM nx_polls WHERE chat_id=? ORDER BY id DESC LIMIT 100',(cid,))
        out=[]
        for r in rows:
            opts=json.loads(r['options']); votes=[]
            for i,opt in enumerate(opts):
                votes.append({'index':i,'option':opt,'count':q('SELECT count(*) n FROM nx_poll_votes WHERE poll_id=? AND option_index=?',(r['id'],i),True)['n']})
            mine=[v['option_index'] for v in q('SELECT option_index FROM nx_poll_votes WHERE poll_id=? AND user_id=?',(r['id'],u['id']))]
            out.append({'id':r['id'],'question':r['question'],'options':votes,'multiple':bool(r['multiple']),'anonymous':bool(r['anonymous']),'closed':bool(r['closed']),'my_votes':mine})
        return jsonify({'items':out})

    @app.post('/api/polls/<int:pid>/vote')
    def nx_poll_vote(pid):
        import json
        u=auth(); p=q('SELECT * FROM nx_polls WHERE id=?',(pid,),True)
        if not p: raise Error('نظرسنجی پیدا نشد',404)
        member(p['chat_id'],u['id'])
        if p['closed']: raise Error('این نظرسنجی بسته شده است',409)
        d=request.get_json(silent=True) or {}; indexes=d.get('options',[])
        if not isinstance(indexes,list) or not indexes: raise Error('حداقل یک گزینه انتخاب کنید')
        opts=json.loads(p['options'])
        try: indexes=list(dict.fromkeys(int(i) for i in indexes))
        except: raise Error('گزینه نامعتبر است')
        if any(i<0 or i>=len(opts) for i in indexes): raise Error('گزینه نامعتبر است')
        if not p['multiple'] and len(indexes)!=1: raise Error('فقط یک گزینه مجاز است')
        db().execute('DELETE FROM nx_poll_votes WHERE poll_id=? AND user_id=?',(pid,u['id']))
        db().executemany('INSERT INTO nx_poll_votes(poll_id,user_id,option_index,created) VALUES(?,?,?,?)',[(pid,u['id'],i,now()) for i in indexes]); db().commit()
        return jsonify({'ok':True})

    @app.post('/api/polls/<int:pid>/close')
    def nx_poll_close(pid):
        u=auth(); p=q('SELECT * FROM nx_polls WHERE id=?',(pid,),True)
        if not p: raise Error('نظرسنجی پیدا نشد',404)
        m=member(p['chat_id'],u['id'])
        if m['role'] not in ('owner','admin') and p['creator']!=u['id']: raise Error('دسترسی ندارید',403)
        x('UPDATE nx_polls SET closed=1 WHERE id=?',(pid,))
        return jsonify({'ok':True})

    @app.post('/api/chats/<int:cid>/topics')
    def nx_topic_create(cid):
        u=auth(); c=chat(cid); m=member(cid,u['id'])
        if c['type']!='group': raise Error('موضوع فقط برای گروه‌هاست')
        manager(m); d=request.get_json(silent=True) or {}; title=str(d.get('title') or '').strip()[:60]
        if not title: raise Error('عنوان موضوع الزامی است')
        if q('SELECT count(*) n FROM nx_topics WHERE chat_id=?',(cid,),True)['n']>=100: raise Error('حداکثر ۱۰۰ موضوع')
        tid=x('INSERT INTO nx_topics(chat_id,title,icon,creator,created) VALUES(?,?,?,?,?)',(cid,title,str(d.get('icon') or '💬')[:8],u['id'],now()))
        event(cid,u['id'],'topic_created',None,title)
        return jsonify({'id':tid,'title':title})

    @app.get('/api/chats/<int:cid>/topics')
    def nx_topic_list(cid):
        u=auth(); chat(cid); member(cid,u['id'])
        return jsonify({'items':[dict(r) for r in q('SELECT id,title,icon,creator,closed,created FROM nx_topics WHERE chat_id=? ORDER BY id',(cid,))]})

    @app.patch('/api/topics/<int:tid>')
    def nx_topic_update(tid):
        u=auth(); t=q('SELECT * FROM nx_topics WHERE id=?',(tid,),True)
        if not t: raise Error('موضوع پیدا نشد',404)
        m=member(t['chat_id'],u['id']); manager(m); d=request.get_json(silent=True) or {}
        if 'title' in d:
            title=str(d['title']).strip()[:60]
            if not title: raise Error('عنوان الزامی است')
            x('UPDATE nx_topics SET title=? WHERE id=?',(title,tid))
        if 'closed' in d: x('UPDATE nx_topics SET closed=? WHERE id=?',(int(bool(d['closed'])),tid))
        return jsonify({'ok':True})

    @app.post('/api/chats/<int:cid>/moderation')
    def nx_moderate(cid):
        u=auth(); chat(cid); m=member(cid,u['id']); manager(m); d=request.get_json(silent=True) or {}
        target=int(d.get('user_id') or 0); action=d.get('action')
        if target==u['id'] or action not in ('kick','ban','mute','unmute','unban','promote','demote'):
            raise Error('عملیات یا عضو نامعتبر است')
        tm=q('SELECT * FROM members WHERE chat_id=? AND user_id=?',(cid,target),True)
        if action!='unban' and not tm: raise Error('عضو پیدا نشد',404)
        if tm and (tm['role']=='owner' or (tm['role']=='admin' and m['role']!='owner')): raise Error('نمی‌توانید روی این مدیر اقدام کنید',403)
        reason=str(d.get('reason') or '')[:300]
        until=None
        if action in ('mute','ban') and d.get('duration'):
            try: duration=int(d['duration'])
            except: raise Error('مدت نامعتبر است')
            if not 60<=duration<=2592000: raise Error('مدت باید بین یک دقیقه تا ۳۰ روز باشد')
            until=now()+duration
        if action=='kick':
            x('DELETE FROM members WHERE chat_id=? AND user_id=?',(cid,target))
        elif action in ('ban','mute'):
            x('INSERT INTO nx_moderation(chat_id,user_id,action,until_at,reason,actor,created) VALUES(?,?,?,?,?,?,?)',(cid,target,action,until,reason,u['id'],now()))
            if action=='ban': x('DELETE FROM members WHERE chat_id=? AND user_id=?',(cid,target))
            else: x('UPDATE members SET muted=1 WHERE chat_id=? AND user_id=?',(cid,target))
        elif action=='unmute':
            x("UPDATE members SET muted=0 WHERE chat_id=? AND user_id=?",(cid,target))
            x("UPDATE nx_moderation SET until_at=? WHERE chat_id=? AND user_id=? AND action='mute' AND (until_at IS NULL OR until_at>?)",(now(),cid,target,now()))
        elif action=='unban':
            x("UPDATE nx_moderation SET until_at=? WHERE chat_id=? AND user_id=? AND action='ban' AND (until_at IS NULL OR until_at>?)",(now(),cid,target,now()))
        elif action=='promote':
            if m['role']!='owner': raise Error('فقط مالک می‌تواند مدیر تعیین کند',403)
            x("UPDATE members SET role='admin' WHERE chat_id=? AND user_id=?",(cid,target))
        elif action=='demote':
            if m['role']!='owner': raise Error('فقط مالک می‌تواند مدیر را عزل کند',403)
            x("UPDATE members SET role='member' WHERE chat_id=? AND user_id=?",(cid,target))
        event(cid,u['id'],action,target,reason)
        return jsonify({'ok':True})

    @app.get('/api/chats/<int:cid>/events')
    def nx_chat_events(cid):
        u=auth(); chat(cid); m=member(cid,u['id']); manager(m)
        rows=q('SELECT e.*,u.username,u.nick FROM nx_chat_events e LEFT JOIN users u ON u.id=e.actor WHERE e.chat_id=? ORDER BY e.id DESC LIMIT 200',(cid,))
        return jsonify({'items':[dict(r) for r in rows]})


    @app.get('/join/<token>')
    def nx_join_page(token):
        """Deep-link for invite tokens; SPA bootstrap loads index and client joins."""
        from flask import redirect
        # Keep SPA entry; token is read by client from path
        return redirect('/?join=' + token)

    @app.get('/api/chats/<int:cid>/stats')
    def nx_chat_stats(cid):
        u=auth(); c=chat(cid); m=member(cid,u['id']); manager(m)
        members=q('SELECT count(*) n FROM members WHERE chat_id=?',(cid,),True)['n']
        posts=q('SELECT count(*) n FROM messages WHERE chat_id=? AND deleted=0',(cid,),True)['n']
        daily=q('SELECT substr(datetime(created,"unixepoch"),1,10) day,count(*) count FROM messages WHERE chat_id=? AND created>? GROUP BY day ORDER BY day',(cid,now()-30*86400))
        return jsonify({'members':members,'messages':posts,'daily_messages':[dict(r) for r in daily]})
