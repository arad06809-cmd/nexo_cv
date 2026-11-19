"""Additive NEXO Infinity foundation: Bot API 1.0 and NEXO Market."""
import hashlib, json, re, secrets, time, urllib.parse, os, threading, uuid
from nexo_internal_runner import execute_python
from functools import wraps
from flask import request, jsonify, session


def register_extensions(app, *, auth, q, x, db, stars_move, now, Error, audit=None):
    schema = '''
    CREATE TABLE IF NOT EXISTS nx_bots(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER NOT NULL,name TEXT NOT NULL,username TEXT NOT NULL UNIQUE COLLATE NOCASE,token_hash TEXT NOT NULL UNIQUE,token_hint TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created INTEGER NOT NULL,revoked_at INTEGER);
    CREATE TABLE IF NOT EXISTS nx_bot_updates(id INTEGER PRIMARY KEY AUTOINCREMENT,bot_id INTEGER NOT NULL,update_id INTEGER NOT NULL,payload TEXT NOT NULL,created INTEGER NOT NULL,claimed_at INTEGER,UNIQUE(bot_id,update_id));
    CREATE INDEX IF NOT EXISTS nx_bot_updates_pending ON nx_bot_updates(bot_id,claimed_at,id);
    CREATE TABLE IF NOT EXISTS nx_bot_outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,bot_id INTEGER NOT NULL,chat_id INTEGER NOT NULL,text TEXT NOT NULL,created INTEGER NOT NULL,delivered INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS nx_bot_webhooks(bot_id INTEGER PRIMARY KEY,url TEXT NOT NULL,secret_hash TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,updated INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS nx_bot_stats(bot_id INTEGER PRIMARY KEY,updates_count INTEGER NOT NULL DEFAULT 0,sent_count INTEGER NOT NULL DEFAULT 0,users_count INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS nx_bot_users(bot_id INTEGER NOT NULL,user_id INTEGER NOT NULL,first_seen INTEGER NOT NULL,PRIMARY KEY(bot_id,user_id));
    CREATE TABLE IF NOT EXISTS nx_bot_projects(bot_id INTEGER PRIMARY KEY,language TEXT NOT NULL DEFAULT 'python',source TEXT NOT NULL DEFAULT '',updated INTEGER NOT NULL,run_id TEXT,run_status TEXT NOT NULL DEFAULT 'stopped',last_logs TEXT NOT NULL DEFAULT '',runtime_started INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS nx_market_stores(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL UNIQUE,title TEXT NOT NULL,about TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,created INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS nx_market_products(id INTEGER PRIMARY KEY AUTOINCREMENT,seller_id INTEGER NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',category TEXT NOT NULL DEFAULT 'عمومی',price_toman INTEGER NOT NULL,stock INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'active',created INTEGER NOT NULL,updated INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS nx_market_products_feed ON nx_market_products(status,category,created);
    CREATE TABLE IF NOT EXISTS nx_market_wallets(user_id INTEGER PRIMARY KEY,balance_toman INTEGER NOT NULL DEFAULT 0,updated INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS nx_market_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,delta_toman INTEGER NOT NULL,reason TEXT NOT NULL,ref TEXT NOT NULL,created INTEGER NOT NULL,UNIQUE(user_id,ref));
    CREATE TABLE IF NOT EXISTS nx_market_orders(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER NOT NULL,buyer_id INTEGER NOT NULL,seller_id INTEGER NOT NULL,quantity INTEGER NOT NULL,unit_price_toman INTEGER NOT NULL,total_toman INTEGER NOT NULL,stars_paid INTEGER NOT NULL,status TEXT NOT NULL,idempotency_key TEXT NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL,UNIQUE(buyer_id,idempotency_key));
    CREATE INDEX IF NOT EXISTS nx_market_orders_buyer ON nx_market_orders(buyer_id,created);
    CREATE INDEX IF NOT EXISTS nx_market_orders_seller ON nx_market_orders(seller_id,created);
    CREATE TABLE IF NOT EXISTS nx_custom_emoji(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,asset TEXT NOT NULL,created INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS nx_custom_emoji_owner ON nx_custom_emoji(owner_id,created);
    CREATE TABLE IF NOT EXISTS nx_market_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER NOT NULL UNIQUE,buyer_id INTEGER NOT NULL,seller_id INTEGER NOT NULL,rating INTEGER NOT NULL,comment TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL);
    '''
    with app.app_context():
        db().executescript(schema)
        cols={r['name'] for r in db().execute('PRAGMA table_info(nx_bot_projects)').fetchall()}
        if 'runtime_started' not in cols: db().execute('ALTER TABLE nx_bot_projects ADD COLUMN runtime_started INTEGER NOT NULL DEFAULT 0')
        # Backfill persistent chat entries for bots created before the BotFather/chat integration.
        legacy_bots=db().execute('SELECT owner_id,name,username FROM nx_bots').fetchall()
        for bot_row in legacy_bots:
            exists=db().execute("SELECT 1 FROM chats WHERE type='bot' AND owner=? AND username=?",(bot_row['owner_id'],bot_row['username'])).fetchone()
            if not exists:
                username_taken=db().execute('SELECT 1 FROM chats WHERE username=?',(bot_row['username'],)).fetchone()
                if not username_taken:
                    chat_id=db().execute("INSERT INTO chats(type,title,owner,username,public,about,created) VALUES('bot',?,?,?,1,?,?)",
                        (bot_row['name'],bot_row['owner_id'],bot_row['username'],'ربات ساخته‌شده در NEXO BotFather',now())).lastrowid
                    db().execute("INSERT OR IGNORE INTO members(chat_id,user_id,role) VALUES(?,?,'owner')",(chat_id,bot_row['owner_id']))
        db().commit()

    # NEXO Motion Studio: private custom emoji assets. Creation is Premium-only.
    import base64, re, xml.etree.ElementTree as ET
    from flask import Response, redirect
    def premium_user(u): return int(u['premium_until'] or 0) > now()
    @app.get('/api/emoji-studio/items')
    def emoji_studio_items():
        u=auth(); rows=q('SELECT id,name,kind,created FROM nx_custom_emoji WHERE owner_id=? ORDER BY id DESC',(u['id'],))
        return jsonify({'items':[dict(r) for r in rows]})
    @app.post('/api/emoji-studio/items')
    def emoji_studio_create():
        u=auth()
        if not premium_user(u): raise Error('ساخت ایموجی اختصاصی با عکس یا کد، مخصوص NEXO Premium است',403)
        d=request.get_json(silent=True) or {}; name=str(d.get('name') or 'ایموجی من').strip()[:32]; kind=str(d.get('kind') or ''); asset=str(d.get('asset') or '')
        if kind not in ('image','svg'): raise Error('نوع ایموجی نامعتبر است')
        if not asset or len(asset)>2_000_000: raise Error('فایل ایموجی خالی یا بیش از حد بزرگ است')
        if kind=='image':
            if not re.fullmatch(r'data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+',asset): raise Error('تصویر باید PNG، JPG یا WebP باشد')
            try:
                raw=base64.b64decode(asset.split(',',1)[1],validate=True)
                if len(raw)>1_400_000: raise ValueError()
            except Exception: raise Error('تصویر نامعتبر یا بزرگ‌تر از ۱.۴ مگابایت است')
        else:
            if len(asset)>100_000 or not asset.lstrip().startswith('<svg') or re.search(r'<(script|foreignObject|iframe|image)|on[a-z]+\s*=|javascript:|https?://',asset,re.I): raise Error('کد SVG شامل عنصر یا ویژگی غیرمجاز است')
            try:
                root=ET.fromstring(asset)
                if root.tag.split('}')[-1]!='svg': raise ValueError()
                allowed={'svg','g','path','circle','ellipse','rect','line','polyline','polygon','animate','animateTransform'}
                if any(el.tag.split('}')[-1] not in allowed for el in root.iter()): raise ValueError()
                allowed_attrs={'viewBox','width','height','fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','d','cx','cy','r','rx','ry','x','y','x1','y1','x2','y2','points','transform','opacity','attributeName','values','dur','repeatCount','begin','from','to','type','additive','calcMode','keyTimes'}
                for el in root.iter():
                    for key,val in list(el.attrib.items()):
                        if key.split('}')[-1] not in allowed_attrs: del el.attrib[key]
                        elif 'url(' in val.lower() or 'javascript' in val.lower(): raise ValueError()
                        elif k=='attributeName' and val not in {'opacity','transform','fill','r','cx','cy','stroke-width'}: raise ValueError()
                        elif k=='dur' and not re.fullmatch(r'(?:[0-9](?:\.[0-9]{1,2})?)s',val): raise ValueError()
                        elif k=='repeatCount' and val!='indefinite' and not re.fullmatch(r'[1-9][0-9]?',val): raise ValueError()
                asset=ET.tostring(root,encoding='unicode')
            except Exception: raise Error('ساختار SVG معتبر یا مجاز نیست')
        eid=x('INSERT INTO nx_custom_emoji(owner_id,name,kind,asset,created) VALUES(?,?,?,?,?)',(u['id'],name,kind,asset,now()))
        return jsonify({'ok':True,'id':eid,'name':name})
    @app.get('/api/emoji-studio/items/<int:eid>')
    def emoji_studio_item(eid):
        u=auth(); row=q('SELECT id,name,kind,asset FROM nx_custom_emoji WHERE id=? AND owner_id=?',(eid,u['id']),True)
        if not row: raise Error('ایموجی پیدا نشد',404)
        return jsonify({'item':dict(row)})
    @app.delete('/api/emoji-studio/items/<int:eid>')
    def emoji_studio_delete(eid):
        u=auth(); x('DELETE FROM nx_custom_emoji WHERE id=? AND owner_id=?',(eid,u['id'])); return jsonify({'ok':True})
    @app.get('/emoji-assets/<int:eid>')
    def emoji_asset(eid):
        auth(); row=q('SELECT kind,asset FROM nx_custom_emoji WHERE id=?',(eid,),True)
        if not row: raise Error('ایموجی پیدا نشد',404)
        if row['kind']=='svg': return Response(row['asset'],mimetype='image/svg+xml',headers={'Cache-Control':'private, max-age=3600'})
        try:
            head,data=row['asset'].split(',',1); raw=base64.b64decode(data,validate=True)
            mime=head[5:].split(';',1)[0]
            return Response(raw,mimetype=mime,headers={'Cache-Control':'private, max-age=3600'})
        except Exception: raise Error('فایل ایموجی خراب است',500)
    @app.get('/emoji-studio')
    def emoji_studio_page():
        auth(); return Response(EMOJI_STUDIO_HTML.replace('__CSRF__',session.get('csrf','')),mimetype='text/html')
    @app.get('/bot-studio')
    def bot_studio_page():
        auth(); return Response(BOT_STUDIO_HTML.replace('__CSRF__',session.get('csrf','')),mimetype='text/html')

    EMOJI_STUDIO_HTML = r'''<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEXO Motion Studio</title>
<style>:root{color-scheme:dark;--bg:#0b1019;--panel:#121b2a;--line:#26354b;--muted:#91a0b8;--text:#eef3ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#1c2a49,transparent 35%),var(--bg);color:var(--text);font:15px system-ui}header{height:66px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 22px}a{color:#aabaff;text-decoration:none}.wrap{max-width:1100px;margin:30px auto;padding:0 18px}.grid{display:grid;grid-template-columns:1.1fr .9fr;gap:18px;margin-top:24px}.card{background:linear-gradient(145deg,#172337,#111a28);border:1px solid var(--line);border-radius:18px;padding:20px}.field{width:100%;background:#0a111d;color:white;border:1px solid var(--line);border-radius:10px;padding:12px;margin:8px 0 14px}.preview{height:240px;border:1px dashed #435574;border-radius:16px;display:grid;place-items:center;background:#0a111d;overflow:hidden}.preview img{max-width:180px;max-height:180px;object-fit:contain}.preview svg{width:180px;height:180px}.muted{color:var(--muted)}button{border:1px solid var(--line);background:#1b2940;color:white;padding:10px 14px;border-radius:10px;cursor:pointer}.primary{background:linear-gradient(135deg,#697cff,#9369ed);border:0}.tabs{display:flex;gap:8px;margin:16px 0}.tab.active{background:#596ee8}.notice{padding:12px;border-radius:10px;background:#342c18;color:#f7d88c;margin:12px 0}.items{display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:10px}.item{background:#0b1422;border:1px solid var(--line);border-radius:12px;padding:12px;text-align:center}@media(max-width:760px){.grid{grid-template-columns:1fr}}</style>
<header><b>✦ NEXO Motion Studio</b><a href="/">بازگشت به نکسو ←</a></header><main class="wrap"><h1>استودیوی ایموجی اختصاصی</h1><div class="muted">ایموجی‌های شخصی بساز؛ با تصویر خودت یا طراحی برداری با کد.</div><div class="notice">ساخت ایموجی اختصاصی با عکس و کد مخصوص NEXO Premium است. ایموجی‌های ساخته‌شده پس از ذخیره، در برگه «ایموجی‌های من» داخل چت قابل ارسال‌اند؛ انیمیشن ورود در پیام و واکنش‌ها فعال است.</div><div class="grid"><section class="card"><h3>ساخت ایموجی جدید</h3><input id="name" class="field" placeholder="نام ایموجی"><div class="tabs"><button class="tab active" data-mode="image">تبدیل عکس به ایموجی</button><button class="tab" data-mode="svg">ساخت با کد SVG</button></div><div id="imagePane"><input id="file" type="file" accept="image/png,image/jpeg,image/webp" class="field"><div class="muted">تصویر به شکل مربع برش می‌خورد؛ حداکثر ۱.۴ مگابایت.</div></div><div id="svgPane" hidden><textarea id="code" class="field" rows="9" dir="ltr" spellcheck="false" placeholder="<svg viewBox=&quot;0 0 128 128&quot;><circle cx=&quot;64&quot; cy=&quot;64&quot; r=&quot;40&quot; fill=&quot;#ffcc44&quot;><animate attributeName=&quot;r&quot; values=&quot;40;46;40&quot; dur=&quot;1s&quot; repeatCount=&quot;indefinite&quot;/></circle></svg>"></textarea><div class="muted">SVG امن: شکل‌های پایه و مسیرها؛ بدون اسکریپت یا لینک خارجی.</div></div><div class="preview" id="preview"><span class="muted">پیش‌نمایش</span></div><button class="primary" id="save">ذخیره ایموجی</button> <span id="status" class="muted"></span></section><section class="card"><h3>کتابخانه من</h3><div id="items" class="items">در حال بارگذاری…</div></section></div></main>
<script>const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));let mode='image',asset='',kind='image';const CSRF='__CSRF__';async function api(url,opt={}){let r=await fetch(url,{...opt,headers:{'Content-Type':'application/json','X-CSRF':CSRF,...(opt.headers||{})}});let d=await r.json();if(!r.ok)throw Error(d.error||'خطا');return d}document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{mode=b.dataset.mode;document.querySelectorAll('[data-mode]').forEach(x=>x.classList.toggle('active',x===b));$('#imagePane').hidden=mode!=='image';$('#svgPane').hidden=mode!=='svg';asset='';$('#preview').innerHTML='<span class="muted">پیش‌نمایش</span>'});$('#file').onchange=()=>{let f=$('#file').files[0];if(!f)return;if(f.size>1400000){$('#status').textContent='حجم تصویر بیشتر از ۱.۴ مگابایت است';return}let rd=new FileReader();rd.onload=()=>{let im=new Image();im.onload=()=>{let c=document.createElement('canvas');c.width=c.height=256;let x=c.getContext('2d'),side=Math.min(im.width,im.height);x.save();x.beginPath();x.arc(128,128,119,0,Math.PI*2);x.clip();x.drawImage(im,(im.width-side)/2,(im.height-side)/2,side,side,0,0,256,256);x.restore();x.beginPath();x.arc(128,128,119,0,Math.PI*2);x.lineWidth=9;x.strokeStyle='#ffffff';x.stroke();asset=c.toDataURL('image/png');kind='image';$('#preview').innerHTML='<img src="'+asset+'">'};im.src=rd.result};rd.readAsDataURL(f)};$('#code').oninput=()=>{asset=$('#code').value;kind='svg';$('#preview').innerHTML='<img alt="SVG preview" src="data:image/svg+xml;charset=utf-8,'+encodeURIComponent(asset)+'">'};$('#save').onclick=async()=>{try{if(mode==='svg'){asset=$('#code').value;kind='svg'}if(!asset)throw Error('اول تصویر یا کد SVG را وارد کن');await api('/api/emoji-studio/items',{method:'POST',body:JSON.stringify({name:$('#name').value,kind,asset})});$('#status').textContent='ذخیره شد ✓';load()}catch(e){$('#status').textContent=e.message}};async function load(){try{let d=await api('/api/emoji-studio/items');$('#items').innerHTML=d.items.map(i=>`<div class="item"><img src="/emoji-assets/${i.id}" alt="${esc(i.name)}" style="width:56px;height:56px;object-fit:contain"><div>${esc(i.name)}</div><button data-del="${i.id}">حذف</button></div>`).join('')||'هنوز ایموجی نساختی.';document.querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{await api('/api/emoji-studio/items/'+b.dataset.del,{method:'DELETE'});load()})}catch(e){$('#items').textContent=e.message}}load();</script></html>'''

    BOT_STUDIO_HTML = r'''<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEXO Bot Studio</title><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs/editor/editor.main.min.css"><style>:root{color-scheme:dark;--bg:#0b0d12;--line:#2a303d;--text:#e5e9f0;--muted:#929baa}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui;height:100vh;overflow:hidden}.top{height:48px;background:#171b24;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px;padding:0 14px}.brand{font-weight:800;color:#aebcff}.top a{color:#aebcff;text-decoration:none;margin-right:auto}.layout{height:calc(100vh - 48px);display:grid;grid-template-columns:250px 1fr}.side{background:#11151d;border-left:1px solid var(--line);padding:14px;overflow:auto}.side h3{font-size:12px;color:var(--muted)}.bot{display:block;width:100%;text-align:right;background:transparent;border:1px solid transparent;color:var(--text);padding:10px;border-radius:7px;cursor:pointer}.bot.active,.bot:hover{background:#252c3a;border-color:#343d50}.main{min-width:0;display:flex;flex-direction:column}.toolbar{height:48px;display:flex;align-items:center;gap:8px;padding:0 12px;border-bottom:1px solid var(--line);background:#141923}.toolbar button,.toolbar select{background:#252c3a;border:1px solid #394253;color:white;padding:7px 12px;border-radius:6px;cursor:pointer}.toolbar .run{background:#256b48}.toolbar .stop{background:#703737}.crumb{color:#abb4c4;margin-left:auto}#editor{height:calc(100% - 180px);min-height:180px}.console{height:132px;border-top:1px solid var(--line);background:#090c11;padding:10px;overflow:auto;direction:ltr;text-align:left;color:#b8c4d8;font:12px ui-monospace,monospace;white-space:pre-wrap}.status{height:24px;background:#202631;padding:3px 12px;color:#aab5c7;font-size:12px}.empty{padding:40px;color:var(--muted)}@media(max-width:700px){.layout{grid-template-columns:150px 1fr}.side{padding:7px}.toolbar{gap:4px;padding:0 5px}.toolbar button{padding:6px;font-size:11px}}</style><header class="top"><span class="brand">NEXO ◈ BOT STUDIO</span><span id="current">پروژه‌ای انتخاب نشده</span><a href="/">بازگشت به نکسو</a></header><div class="layout"><aside class="side"><h3>EXPLORER · ربات‌های من</h3><div id="bots">در حال بارگذاری…</div><p style="color:#778397;font-size:11px;line-height:1.8">هر ربات پروژه مستقل دارد. موتور اجرای فعلی فقط Python را پشتیبانی می‌کند.</p></aside><main class="main"><div class="toolbar"><select id="lang"><option value="python">Python 3</option></select><button id="save">ذخیره</button><button id="run" class="run">▶ اجرا</button><button id="test">آزمایش /start</button><button id="stop" class="stop">■ توقف</button><button id="logs">گزارش</button><span class="crumb" id="file">main.py</span></div><div id="editor"><div class="empty">از ستون کناری یک ربات انتخاب کن.</div></div><div class="status" id="status">آماده</div><div class="console" id="console">NEXO Bot Studio · Console</div></main></div><script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs/loader.min.js"></script><script>const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));let bots=[],bid=null,editor=null;const CSRF='__CSRF__';async function api(url,method='GET',body){let r=await fetch('/api/'+url,{method,headers:{'Content-Type':'application/json','X-CSRF':CSRF},body:body?JSON.stringify(body):undefined});let d=await r.json();if(!r.ok)throw Error(d.error||'خطا');return d}function status(t){$('#status').textContent=t}async function loadBots(){let d=await api('bots');bots=d.bots||[];$('#bots').innerHTML=bots.map(b=>`<button class="bot" data-id="${b.id}">▸ ${esc(b.name)}<br><small>@${esc(b.username)}</small></button>`).join('')||'<div class="empty">رباتی نداری؛ اول از BotFather بساز.</div>';document.querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>selectBot(+b.dataset.id))}async function selectBot(id){bid=id;let b=bots.find(x=>x.id===id);$('#current').textContent=b.name;$('#file').textContent=b.name+'/main.py';document.querySelectorAll('[data-id]').forEach(x=>x.classList.toggle('active',+x.dataset.id===id));let d=await api(`bots/${id}/project`),p=d.project;$('#lang').value='python';$('#console').textContent=p.logs||'کنسول آماده است.';if(editor){editor.setValue(p.source||'');monaco.editor.setModelLanguage(editor.getModel(),'python')}else{$('#editor').innerHTML='';require.config({paths:{vs:'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs'}});require(['vs/editor/editor.main'],()=>{editor=monaco.editor.create($('#editor'),{value:p.source||'',language:'python',theme:'vs-dark',automaticLayout:true,minimap:{enabled:true},fontSize:14,tabSize:4,wordWrap:'on',scrollBeyondLastLine:false})})}}async function save(){if(!bid)throw Error('اول یک ربات انتخاب کن');let source=editor?editor.getValue():'';if(source.split('\n').length>100)throw Error('حداکثر ۱۰۰ خط');await api(`bots/${bid}/project`,'PUT',{language:$('#lang').value,source});status('ذخیره شد')}$('#save').onclick=async()=>{try{await save()}catch(e){status(e.message)}};$('#run').onclick=async()=>{try{await save();let d=await api(`bots/${bid}/run`,'POST',{});status('در حال اجرا');$('#console').textContent=d.logs||''}catch(e){status(e.message)}};$('#test').onclick=async()=>{try{await api(`bots/${bid}/start`,'POST',{});status('رویداد /start ارسال شد')}catch(e){status(e.message)}};$('#stop').onclick=async()=>{try{await api(`bots/${bid}/stop`,'POST',{});status('متوقف شد')}catch(e){status(e.message)}};$('#logs').onclick=async()=>{try{let d=await api(`bots/${bid}/logs`);$('#console').textContent=d.logs||'بدون گزارش';status(d.status)}catch(e){status(e.message)}};$('#lang').onchange=()=>{if(editor)monaco.editor.setModelLanguage(editor.getModel(),'python')};loadBots().catch(e=>status(e.message));</script></html>'''


    def ok(**kw): return jsonify({'ok': True, **kw})
    def bot_auth(fn):
        @wraps(fn)
        def inner(*a, **kw):
            token=request.headers.get('X-NEXO-BOT-TOKEN','') or (request.view_args or {}).get('token','')
            h=hashlib.sha256(token.encode()).hexdigest() if token else ''
            bot=q('SELECT * FROM nx_bots WHERE token_hash=? AND active=1',(h,),True)
            if not bot: return jsonify({'ok':False,'error_code':401,'description':'Invalid bot token'}),401
            request.nexo_bot=bot
            return fn(*a,**kw)
        return inner

    @app.post('/api/bots')
    def create_bot():
        u=auth(); d=request.get_json(silent=True) or {}; name=str(d.get('name') or '').strip()[:64]; un=str(d.get('username') or '').strip().lstrip('@')
        if len(name)<2 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}bot',un,re.I): raise Error('نام یا آیدی ربات نامعتبر است؛ آیدی باید به bot ختم شود')
        if q('SELECT 1 FROM nx_bots WHERE username=?',(un,),True): raise Error('این آیدی قبلاً گرفته شده است')
        if q('SELECT 1 FROM chats WHERE username=?',(un,),True): raise Error('این آیدی قبلاً در نکسو استفاده شده است')
        token=secrets.token_urlsafe(36); bid=db().execute('INSERT INTO nx_bots(owner_id,name,username,token_hash,token_hint,created) VALUES(?,?,?,?,?,?)',(u['id'],name,un,hashlib.sha256(token.encode()).hexdigest(),token[-6:],now())).lastrowid
        db().execute('INSERT INTO nx_bot_stats(bot_id) VALUES(?)',(bid,))
        # Give every bot a persistent NEXO conversation so it appears in the owner's chat list.
        chat_id=db().execute("INSERT INTO chats(type,title,owner,username,public,about,created) VALUES('bot',?,?,?,1,?,?)",
            (name,u['id'],un,'ربات ساخته‌شده در NEXO BotFather',now())).lastrowid
        db().execute("INSERT INTO members(chat_id,user_id,role) VALUES(?,?,'owner')",(chat_id,u['id']))
        db().commit()
        if audit: audit('bot','create',f'bot={bid} chat={chat_id} username={un}',u['id'])
        return ok(bot={'id':bid,'name':name,'username':un,'active':True,'chat_id':chat_id},token=token),201

    @app.get('/api/bots')
    def list_bots():
        u=auth(); rows=q('''SELECT b.id,b.name,b.username,b.active,b.created,b.token_hint,s.updates_count,s.sent_count,s.users_count FROM nx_bots b LEFT JOIN nx_bot_stats s ON s.bot_id=b.id WHERE b.owner_id=? ORDER BY b.id DESC''',(u['id'],))
        return ok(bots=[dict(r) for r in rows])

    @app.post('/api/bots/<int:bid>/rename')
    def rename_bot(bid):
        u=auth(); b=q('SELECT id FROM nx_bots WHERE id=? AND owner_id=?',(bid,u['id']),True)
        if not b: raise Error('ربات پیدا نشد',404)
        d=request.get_json(silent=True) or {}; name=str(d.get('name') or '').strip()[:64]
        if len(name)<2: raise Error('نام ربات باید حداقل ۲ نویسه باشد')
        x('UPDATE nx_bots SET name=? WHERE id=?',(name,bid))
        if audit: audit('bot','rename',f'bot={bid}',u['id'])
        return ok(name=name)

    @app.post('/api/bots/<int:bid>/token')
    def rotate_bot_token(bid):
        u=auth(); b=q('SELECT id FROM nx_bots WHERE id=? AND owner_id=?',(bid,u['id']),True)
        if not b: raise Error('ربات پیدا نشد',404)
        token=secrets.token_urlsafe(36); x('UPDATE nx_bots SET token_hash=?,token_hint=? WHERE id=?',(hashlib.sha256(token.encode()).hexdigest(),token[-6:],bid))
        return ok(token=token,message='توکن قبلی باطل شد؛ توکن جدید فقط همین بار نمایش داده می‌شود')

    @app.post('/api/bots/<int:bid>/<action>')
    def bot_lifecycle(bid,action):
        u=auth(); b=q('SELECT id FROM nx_bots WHERE id=? AND owner_id=?',(bid,u['id']),True)
        if not b: raise Error('ربات پیدا نشد',404)
        if action not in ('enable','disable'): raise Error('عملیات نامعتبر است',404)
        x('UPDATE nx_bots SET active=?,revoked_at=? WHERE id=?',(1 if action=='enable' else 0,None if action=='enable' else now(),bid))
        if action=='disable': x("UPDATE nx_bot_projects SET run_status='stopped',run_id=NULL,runtime_started=0 WHERE bot_id=?",(bid,))
        return ok()

    @app.delete('/api/bots/<int:bid>')
    def delete_bot(bid):
        u=auth(); b=q('SELECT id FROM nx_bots WHERE id=? AND owner_id=?',(bid,u['id']),True)
        if not b: raise Error('ربات پیدا نشد',404)
        bot=q('SELECT username FROM nx_bots WHERE id=? AND owner_id=?',(bid,u['id']),True)
        linked=q("SELECT id FROM chats WHERE type='bot' AND owner=? AND username=?",(u['id'],bot['username']),True)
        if linked:
            db().execute('DELETE FROM messages WHERE chat_id=?',(linked['id'],))
            db().execute('DELETE FROM members WHERE chat_id=?',(linked['id'],))
            db().execute('DELETE FROM chats WHERE id=?',(linked['id'],))
        for t in ('nx_bot_updates','nx_bot_outbox','nx_bot_webhooks','nx_bot_stats','nx_bot_users','nx_bot_projects'): db().execute(f'DELETE FROM {t} WHERE bot_id=?',(bid,))
        db().execute('DELETE FROM nx_bots WHERE id=?',(bid,)); db().commit(); return ok()

    def owned_bot(bid, user_id):
        bot=q('SELECT id,name,username,active FROM nx_bots WHERE id=? AND owner_id=?',(bid,user_id),True)
        if not bot: raise Error('ربات پیدا نشد یا دسترسی ندارید',404)
        return bot

    @app.get('/api/bots/<int:bid>/project')
    def get_bot_project(bid):
        u=auth(); owned_bot(bid,u['id'])
        row=q('SELECT * FROM nx_bot_projects WHERE bot_id=?',(bid,),True)
        if not row:
            source="# NEXO Bot Studio - Python\n# این نمونه پس از زدن اجرا، به رویداد /start پاسخ می‌دهد.\n\ndef on_start(bot):\n    print('ربات آماده دریافت پیام است.')\n\ndef on_message(message, bot):\n    chat_id = message.get('chat_id') or message.get('chat', {}).get('id')\n    text = message.get('text', '')\n    if text == '/start':\n        bot.send_message(chat_id, 'سلام! به ربات ساخته‌شده با NEXO خوش آمدی 🤖')\n    else:\n        bot.send_message(chat_id, 'پیامت دریافت شد: ' + text)\n"
            x("INSERT OR IGNORE INTO nx_bot_projects(bot_id,language,source,updated) VALUES(?,'python',?,?)",(bid,source,now()))
            row=q('SELECT * FROM nx_bot_projects WHERE bot_id=?',(bid,),True)
        return ok(project={'language':row['language'],'source':row['source'],'updated':row['updated'],'run_status':row['run_status'],'run_id':row['run_id'],'logs':row['last_logs']})

    @app.put('/api/bots/<int:bid>/project')
    def save_bot_project(bid):
        u=auth(); owned_bot(bid,u['id']); d=request.get_json(silent=True) or {}
        language=str(d.get('language') or 'python').lower(); source=str(d.get('source') or '')
        if language not in ('python','javascript'): raise Error('زبان برنامه‌نویسی پشتیبانی نمی‌شود')
        line_count = source.count('\n') + 1 if source else 0
        if line_count > 100: raise Error('کد هر ربات حداکثر می‌تواند ۱۰۰ خط باشد')
        if len(source.encode('utf-8'))>100_000: raise Error('حجم کد بیشتر از ۱۰۰ کیلوبایت است')
        x('''INSERT INTO nx_bot_projects(bot_id,language,source,updated) VALUES(?,?,?,?)
             ON CONFLICT(bot_id) DO UPDATE SET language=excluded.language,source=excluded.source,updated=excluded.updated''',(bid,language,source,now()))
        if audit: audit('bot','code_save',f'bot={bid} language={language}',u['id'])
        return ok(saved=True,updated=now())

    @app.post('/api/bots/<int:bid>/run')
    def start_bot_project(bid):
        u=auth(); bot=owned_bot(bid,u['id'])
        if not bot['active']: raise Error('ابتدا ربات را فعال کنید',409)
        project=q('SELECT * FROM nx_bot_projects WHERE bot_id=?',(bid,),True)
        if not project or not project['source'].strip(): raise Error('اول کد ربات را ذخیره کنید',400)
        if project['language']!='python':
            raise Error('موتور داخلی فعلی فقط اجرای Python را پشتیبانی می‌کند؛ JavaScript فعلاً قابل ذخیره است اما اجرا نمی‌شود.',400)
        result=execute_python(project['source'],mode='start')
        if not result.get('ok'):
            logs='خطای شروع: '+str(result.get('error') or 'کد نامعتبر')+'\n'+str(result.get('output') or '')
            x("UPDATE nx_bot_projects SET run_id=NULL,run_status='error',runtime_started=0,last_logs=? WHERE bot_id=?",(logs[-12000:],bid))
            raise Error(str(result.get('error') or 'اجرای کد ناموفق بود'),422)
        run_id=uuid.uuid4().hex
        logs=('موتور داخلی NEXO فعال شد.\n'+str(result.get('output') or '')).strip()
        x("UPDATE nx_bot_projects SET run_id=?,run_status='running',runtime_started=1,last_logs=? WHERE bot_id=?",(run_id,logs[-12000:],bid))
        for item in result.get('sent',[]):
            _deliver_bot_message(bid,item)
        return ok(run_id=run_id,status='running',logs=logs)

    @app.post('/api/bots/<int:bid>/stop')
    def stop_bot_project(bid):
        u=auth(); owned_bot(bid,u['id'])
        x("UPDATE nx_bot_projects SET run_status='stopped',run_id=NULL,runtime_started=0,last_logs=last_logs||'\\nربات توسط صاحب آن متوقف شد.' WHERE bot_id=?",(bid,))
        return ok(status='stopped')

    @app.get('/api/bots/<int:bid>/logs')
    def bot_project_logs(bid):
        u=auth(); owned_bot(bid,u['id']); project=q('SELECT * FROM nx_bot_projects WHERE bot_id=?',(bid,),True)
        if not project: return ok(status='stopped',logs='')
        return ok(status=project['run_status'],logs=project['last_logs'])

    def _deliver_bot_message(bid,item):
        try:
            uid=int(item.get('chat_id')); text=str(item.get('text') or '').strip()
        except Exception: return
        if not text or len(text)>4000: return
        if not q('SELECT 1 FROM nx_bot_users WHERE bot_id=? AND user_id=?',(bid,uid),True): return
        x('INSERT INTO nx_bot_outbox(bot_id,chat_id,text,created) VALUES(?,?,?,?)',(bid,uid,text,now()))
        x('UPDATE nx_bot_stats SET sent_count=sent_count+1 WHERE bot_id=?',(bid,))

    @app.get('/api/bots/<int:bid>/inbox')
    def bot_owner_inbox(bid):
        u=auth(); owned_bot(bid,u['id'])
        rows=q('SELECT id,text,created FROM nx_bot_outbox WHERE bot_id=? AND chat_id=? ORDER BY id DESC LIMIT 30',(bid,u['id']))
        return ok(messages=[dict(r) for r in reversed(rows)])

    @app.post('/api/bots/<int:bid>/message')
    def send_message_to_bot(bid):
        u=auth(); bot=q('SELECT id,owner_id FROM nx_bots WHERE id=? AND active=1',(bid,),True)
        if not bot: raise Error('ربات پیدا نشد یا غیرفعال است',404)
        # Bot Studio's built-in test panel acts as the owner test chat:
        # automatically register the owner as an opted-in test user.
        # Ordinary users must still explicitly start the bot first.
        opted_in=q('SELECT 1 FROM nx_bot_users WHERE bot_id=? AND user_id=?',(bid,u['id']),True)
        if not opted_in and int(bot['owner_id'])==int(u['id']):
            db().execute('INSERT OR IGNORE INTO nx_bot_users(bot_id,user_id,first_seen) VALUES(?,?,?)',(bid,u['id'],now()))
            db().execute('UPDATE nx_bot_stats SET users_count=users_count+1 WHERE bot_id=?',(bid,))
            db().commit()
            opted_in=True
        if not opted_in:
            raise Error('ابتدا ربات را شروع کنید',403)
        project=q("SELECT run_status FROM nx_bot_projects WHERE bot_id=?",(bid,),True)
        if not project or project['run_status']!='running': raise Error('ربات در حال اجرا نیست',409)
        d=request.get_json(silent=True) or {}; text=str(d.get('text') or '').strip()
        if not text or len(text)>4000: raise Error('متن پیام نامعتبر است')
        next_id=q('SELECT COALESCE(MAX(update_id),0)+1 n FROM nx_bot_updates WHERE bot_id=?',(bid,),True)['n']
        payload={'update_id':next_id,'message':{'message_id':next_id,'from':{'id':u['id'],'username':u['username'],'first_name':u['nick']},'chat':{'id':u['id'],'type':'private'},'chat_id':u['id'],'text':text}}
        x('INSERT INTO nx_bot_updates(bot_id,update_id,payload,created) VALUES(?,?,?,?)',(bid,next_id,json.dumps(payload,ensure_ascii=False),now()))
        x('UPDATE nx_bot_stats SET updates_count=updates_count+1 WHERE bot_id=?',(bid,))
        return ok(queued=True,update_id=next_id)

    def _internal_bot_worker():
        # One lightweight worker in the existing NEXO web process. Each callback
        # is executed in a short-lived, resource-limited child process.
        while True:
            try:
                with app.app_context():
                    projects=q("SELECT p.bot_id,p.source FROM nx_bot_projects p JOIN nx_bots b ON b.id=p.bot_id WHERE p.run_status='running' AND b.active=1 ORDER BY p.bot_id LIMIT 50")
                    for project in projects:
                        bid=project['bot_id']
                        updates=q('SELECT id,payload FROM nx_bot_updates WHERE bot_id=? AND claimed_at IS NULL ORDER BY id LIMIT 5',(bid,))
                        for update in updates:
                            x('UPDATE nx_bot_updates SET claimed_at=? WHERE id=? AND claimed_at IS NULL',(now(),update['id']))
                            row=q('SELECT claimed_at FROM nx_bot_updates WHERE id=?',(update['id'],),True)
                            if not row or not row['claimed_at']: continue
                            try:
                                payload=json.loads(update['payload']); message=payload.get('message') or {}
                                result=execute_python(project['source'],mode='message',message=message)
                                for item in result.get('sent',[]): _deliver_bot_message(bid,item)
                                detail=result.get('output') or ''
                                if not result.get('ok'): detail+='\nخطای اجرا: '+str(result.get('error') or 'خطای نامشخص')
                                if detail:
                                    old=q('SELECT last_logs FROM nx_bot_projects WHERE bot_id=?',(bid,),True)
                                    logs=((old['last_logs']+'\n') if old and old['last_logs'] else '')+('[رویداد '+str(payload.get('update_id'))+']\n'+detail)
                                    x('UPDATE nx_bot_projects SET last_logs=? WHERE bot_id=?',(logs[-12000:],bid))
                            except Exception as exc:
                                old=q('SELECT last_logs FROM nx_bot_projects WHERE bot_id=?',(bid,),True)
                                logs=((old['last_logs']+'\n') if old and old['last_logs'] else '')+'خطای موتور داخلی: '+str(exc)[:1000]
                                x('UPDATE nx_bot_projects SET last_logs=? WHERE bot_id=?',(logs[-12000:],bid))
            except Exception:
                # A transient DB error must not permanently kill the worker.
                pass
            time.sleep(0.7)

    if not app.extensions.get('nexo_internal_bot_worker'):
        app.extensions['nexo_internal_bot_worker']=True
        threading.Thread(target=_internal_bot_worker,name='nexo-internal-bots',daemon=True).start()

    @app.post('/api/bots/<int:bid>/webhook')
    def set_webhook(bid):
        u=auth(); b=q('SELECT id FROM nx_bots WHERE id=? AND owner_id=?',(bid,u['id']),True)
        if not b: raise Error('ربات پیدا نشد',404)
        d=request.get_json(silent=True) or {}; url=str(d.get('url') or '').strip()
        if not url: x('DELETE FROM nx_bot_webhooks WHERE bot_id=?',(bid,)); return ok(enabled=False)
        p=urllib.parse.urlparse(url)
        if p.scheme!='https' or not p.hostname or p.username or p.password: raise Error('Webhook فقط HTTPS معتبر می‌پذیرد')
        secret=str(d.get('secret') or secrets.token_urlsafe(24))
        x('INSERT OR REPLACE INTO nx_bot_webhooks(bot_id,url,secret_hash,enabled,updated) VALUES(?,?,?,?,?)',(bid,url,hashlib.sha256(secret.encode()).hexdigest(),1,now()))
        return ok(enabled=True,secret=secret,delivery='queued; outbound delivery worker is a separate deployment requirement')

    @app.route('/bot<token>/getMe',methods=['GET','POST'])
    @app.route('/api/bot/getMe',methods=['GET','POST'])
    @bot_auth
    def bot_get_me(token=""):
        b=request.nexo_bot; return jsonify({'ok':True,'result':{'id':b['id'],'is_bot':True,'first_name':b['name'],'username':b['username'],'api_version':'1.0'}})

    @app.route('/bot<token>/getUpdates',methods=['GET','POST'])
    @app.route('/api/bot/getUpdates',methods=['GET','POST'])
    @bot_auth
    def bot_get_updates(token=""):
        b=request.nexo_bot; d=request.get_json(silent=True) or request.args
        try: offset=max(0,int(d.get('offset',0))); limit=max(1,min(100,int(d.get('limit',20)))); timeout=max(0,min(20,int(d.get('timeout',0))))
        except Exception: raise Error('پارامتر نامعتبر است')
        end=time.time()+timeout
        while True:
            db().execute('DELETE FROM nx_bot_updates WHERE bot_id=? AND update_id<?',(b['id'],offset)); db().commit()
            rows=q('SELECT id,update_id,payload FROM nx_bot_updates WHERE bot_id=? AND update_id>=? ORDER BY id LIMIT ?',(b['id'],offset,limit))
            if rows or time.time()>=end: break
            time.sleep(.4)
        return jsonify({'ok':True,'result':[json.loads(r['payload']) for r in rows]})

    @app.route('/bot<token>/sendMessage',methods=['POST'])
    @app.route('/api/bot/sendMessage',methods=['POST'])
    @bot_auth
    def bot_send_message(token=""):
        b=request.nexo_bot; d=request.get_json(silent=True) or {}
        try: uid=int(d.get('chat_id')); text=str(d.get('text') or '').strip()
        except Exception: raise Error('chat_id و text الزامی هستند')
        user=q('SELECT id,banned FROM users WHERE id=?',(uid,),True)
        if not user or user['banned'] or not text or len(text)>4000: raise Error('گیرنده یا متن نامعتبر است')
        if not q('SELECT 1 FROM nx_bot_users WHERE bot_id=? AND user_id=?',(b['id'],uid),True): raise Error('کاربر هنوز ربات را شروع نکرده است',403)
        mid=x('INSERT INTO nx_bot_outbox(bot_id,chat_id,text,created) VALUES(?,?,?,?)',(b['id'],uid,text,now())); x('UPDATE nx_bot_stats SET sent_count=sent_count+1 WHERE bot_id=?',(b['id'],))
        return jsonify({'ok':True,'result':{'message_id':mid,'chat':{'id':uid,'type':'private'},'text':text}})

    @app.route('/bot<token>/getChat',methods=['GET','POST'])
    @app.route('/api/bot/getChat',methods=['GET','POST'])
    @bot_auth
    def bot_get_chat(token=""):
        b=request.nexo_bot; d=request.get_json(silent=True) or request.args
        try: uid=int(d.get('chat_id'))
        except Exception: raise Error('chat_id نامعتبر است')
        u=q('SELECT id,username,nick FROM users WHERE id=?',(uid,),True)
        if not u or not q('SELECT 1 FROM nx_bot_users WHERE bot_id=? AND user_id=?',(b['id'],uid),True): raise Error('کاربر پیدا نشد',404)
        return jsonify({'ok':True,'result':{'id':u['id'],'type':'private','username':u['username'],'first_name':u['nick']}})

    @app.route('/bot<token>/getUser',methods=['GET','POST'])
    @app.route('/api/bot/getUser',methods=['GET','POST'])
    @bot_auth
    def bot_get_user(token=""): return bot_get_chat(token)

    @app.post('/api/bots/<int:bid>/start')
    def start_bot(bid):
        u=auth(); b=q('SELECT id FROM nx_bots WHERE id=? AND active=1',(bid,),True)
        if not b: raise Error('ربات پیدا نشد',404)
        existed=q('SELECT 1 FROM nx_bot_users WHERE bot_id=? AND user_id=?',(bid,u['id']),True)
        db().execute('INSERT OR IGNORE INTO nx_bot_users(bot_id,user_id,first_seen) VALUES(?,?,?)',(bid,u['id'],now()))
        db().execute('UPDATE nx_bot_stats SET users_count=users_count+? WHERE bot_id=?',(0 if existed else 1,bid))
        next_id=q('SELECT COALESCE(MAX(update_id),0)+1 n FROM nx_bot_updates WHERE bot_id=?',(bid,),True)['n']
        payload={'update_id':next_id,'message':{'message_id':next_id,'from':{'id':u['id'],'username':u['username'],'first_name':u['nick']},'chat':{'id':u['id'],'type':'private'},'text':'/start'}}
        db().execute('INSERT OR IGNORE INTO nx_bot_updates(bot_id,update_id,payload,created) VALUES(?,?,?,?)',(bid,payload['update_id'],json.dumps(payload,ensure_ascii=False),now())); db().execute('UPDATE nx_bot_stats SET updates_count=updates_count+1 WHERE bot_id=?',(bid,)); db().commit()
        return ok(started=True)

    @app.get('/api/bot-inbox')
    def bot_inbox():
        u=auth(); rows=q('SELECT o.id,o.bot_id,b.name,o.text,o.created FROM nx_bot_outbox o JOIN nx_bots b ON b.id=o.bot_id WHERE o.chat_id=? AND o.delivered=0 ORDER BY o.id',(u['id'],))
        if rows: db().execute('UPDATE nx_bot_outbox SET delivered=1 WHERE chat_id=? AND delivered=0',(u['id'],)); db().commit()
        return ok(messages=[dict(r) for r in rows])

    # NEXO Market: prices are Toman; Stars checkout uses 1 Star = 1,000 Toman.
    @app.post('/api/market/store')
    def market_store_create():
        u=auth(); d=request.get_json(silent=True) or {}; title=str(d.get('title') or '').strip()[:70]; about=str(d.get('about') or '').strip()[:500]
        if len(title)<2: raise Error('نام فروشگاه باید حداقل ۲ نویسه باشد')
        old=q('SELECT id FROM nx_market_stores WHERE user_id=?',(u['id'],),True)
        if old: x('UPDATE nx_market_stores SET title=?,about=?,active=1 WHERE user_id=?',(title,about,u['id'])); sid=old['id']
        else: sid=x('INSERT INTO nx_market_stores(user_id,title,about,created) VALUES(?,?,?,?)',(u['id'],title,about,now()))
        x('INSERT OR IGNORE INTO nx_market_wallets(user_id,updated) VALUES(?,?)',(u['id'],now())); return ok(store_id=sid)

    @app.get('/api/market/store/<int:uid>')
    def market_store_get(uid):
        s=q('SELECT s.*,u.username,u.nick FROM nx_market_stores s JOIN users u ON u.id=s.user_id WHERE s.user_id=? AND s.active=1',(uid,),True)
        if not s: raise Error('فروشگاه پیدا نشد',404)
        r=q('SELECT ROUND(AVG(rating),2) rating,COUNT(*) count FROM nx_market_reviews WHERE seller_id=?',(uid,),True)
        return ok(store=dict(s),rating=r['rating'] or 0,reviews=r['count'])

    @app.route('/api/market/products',methods=['GET','POST'])
    def market_products():
        if request.method=='GET':
            d=request.args; sql="SELECT p.*,s.title store_title,u.username seller_username FROM nx_market_products p JOIN nx_market_stores s ON s.user_id=p.seller_id JOIN users u ON u.id=p.seller_id WHERE p.status='active' AND s.active=1"; args=[]
            if d.get('category'): sql+=' AND p.category=?'; args.append(d['category'][:50])
            term=(d.get('q') or '').strip()[:60]
            if term: sql+=' AND (p.title LIKE ? OR p.description LIKE ?)'; args += ['%'+term+'%','%'+term+'%']
            return ok(products=[dict(r) for r in q(sql+' ORDER BY p.created DESC LIMIT 100',tuple(args))])
        u=auth(); d=request.get_json(silent=True) or {}
        if not q('SELECT 1 FROM nx_market_stores WHERE user_id=? AND active=1',(u['id'],),True): raise Error('ابتدا فروشگاه خود را بسازید')
        title=str(d.get('title') or '').strip()[:100]; desc=str(d.get('description') or '').strip()[:2000]; cat=str(d.get('category') or 'عمومی').strip()[:50]
        try: price=int(d.get('price_toman')); stock=max(0,min(100000,int(d.get('stock',0))))
        except Exception: raise Error('قیمت و موجودی باید عدد صحیح باشند')
        if len(title)<2 or not 1000<=price<=2_000_000_000: raise Error('عنوان یا قیمت نامعتبر است')
        pid=x('INSERT INTO nx_market_products(seller_id,title,description,category,price_toman,stock,created,updated) VALUES(?,?,?,?,?,?,?,?)',(u['id'],title,desc,cat,price,stock,now(),now()))
        return ok(product_id=pid),201

    @app.put('/api/market/products/<int:pid>')
    def market_product_edit(pid):
        u=auth(); p=q('SELECT id FROM nx_market_products WHERE id=? AND seller_id=?',(pid,u['id']),True)
        if not p: raise Error('محصول پیدا نشد',404)
        d=request.get_json(silent=True) or {}; fields={}
        for k,lim in [('title',100),('description',2000),('category',50)]:
            if k in d: fields[k]=str(d[k]).strip()[:lim]
        if 'price_toman' in d:
            v=int(d['price_toman'])
            if not 1000<=v<=2_000_000_000: raise Error('قیمت نامعتبر است')
            fields['price_toman']=v
        if 'stock' in d: fields['stock']=max(0,min(100000,int(d['stock'])))
        if d.get('status') in ('active','paused'): fields['status']=d['status']
        if fields: fields['updated']=now(); x('UPDATE nx_market_products SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),pid))
        return ok()

    @app.delete('/api/market/products/<int:pid>')
    def market_product_delete(pid):
        u=auth(); p=q('SELECT id FROM nx_market_products WHERE id=? AND seller_id=?',(pid,u['id']),True)
        if not p: raise Error('محصول پیدا نشد',404)
        x("UPDATE nx_market_products SET status='deleted',updated=? WHERE id=?",(now(),pid)); return ok()

    @app.post('/api/market/orders')
    def market_order_create():
        u=auth(); d=request.get_json(silent=True) or {}; idem=str(request.headers.get('Idempotency-Key') or d.get('idempotency_key') or '').strip()
        if not 12<=len(idem)<=100: raise Error('Idempotency-Key یکتا با ۱۲ تا ۱۰۰ نویسه الزامی است')
        old=q('SELECT id,status FROM nx_market_orders WHERE buyer_id=? AND idempotency_key=?',(u['id'],idem),True)
        if old: return ok(order_id=old['id'],status=old['status'],duplicate=True)
        try: pid=int(d.get('product_id')); qty=int(d.get('quantity',1))
        except Exception: raise Error('محصول یا تعداد نامعتبر است')
        if not 1<=qty<=100: raise Error('تعداد باید بین ۱ تا ۱۰۰ باشد')
        p=q("SELECT * FROM nx_market_products WHERE id=? AND status='active'",(pid,),True)
        if not p or p['seller_id']==u['id']: raise Error('محصول در دسترس نیست')
        total=p['price_toman']*qty; stars=(total+999)//1000
        db().execute('BEGIN IMMEDIATE')
        try:
            cur=db().execute("UPDATE nx_market_products SET stock=stock-?,updated=? WHERE id=? AND stock>=? AND status='active'",(qty,now(),pid,qty))
            if cur.rowcount!=1: raise Error('موجودی محصول کافی نیست')
            stars_move(u['id'],-stars,'خرید NEXO Market، محصول #%d'%pid,ref='market:'+idem)
            oid=db().execute('INSERT INTO nx_market_orders(product_id,buyer_id,seller_id,quantity,unit_price_toman,total_toman,stars_paid,status,idempotency_key,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(pid,u['id'],p['seller_id'],qty,p['price_toman'],total,stars,'paid',idem,now(),now())).lastrowid
            db().commit()
        except Exception:
            db().rollback()
            duplicate=q('SELECT id,status FROM nx_market_orders WHERE buyer_id=? AND idempotency_key=?',(u['id'],idem),True)
            if duplicate: return ok(order_id=duplicate['id'],status=duplicate['status'],duplicate=True)
            raise
        return ok(order_id=oid,status='paid',total_toman=total,stars_paid=stars),201

    @app.get('/api/market/orders')
    def market_orders():
        u=auth(); rows=q('''SELECT o.*,p.title product_title,ub.username buyer_username,us.username seller_username FROM nx_market_orders o JOIN nx_market_products p ON p.id=o.product_id JOIN users ub ON ub.id=o.buyer_id JOIN users us ON us.id=o.seller_id WHERE o.buyer_id=? OR o.seller_id=? ORDER BY o.created DESC LIMIT 100''',(u['id'],u['id']))
        return ok(orders=[dict(r) for r in rows])

    @app.post('/api/market/orders/<int:oid>/status')
    def market_order_status(oid):
        u=auth(); status=(request.get_json(silent=True) or {}).get('status'); o=q('SELECT * FROM nx_market_orders WHERE id=?',(oid,),True)
        if not o: raise Error('سفارش پیدا نشد',404)
        db().execute('BEGIN IMMEDIATE')
        try:
            if status=='shipped' and o['seller_id']==u['id'] and o['status']=='paid':
                db().execute("UPDATE nx_market_orders SET status='shipped',updated=? WHERE id=?",(now(),oid))
            elif status=='completed' and o['buyer_id']==u['id'] and o['status']=='shipped':
                db().execute("UPDATE nx_market_orders SET status='completed',updated=? WHERE id=?",(now(),oid))
                db().execute('INSERT OR IGNORE INTO nx_market_wallets(user_id,updated) VALUES(?,?)',(o['seller_id'],now()))
                ref='market-order:%d'%oid
                db().execute('INSERT INTO nx_market_ledger(user_id,delta_toman,reason,ref,created) VALUES(?,?,?,?,?)',(o['seller_id'],o['total_toman'],'تسویه داخلی سفارش تکمیل‌شده',ref,now()))
                db().execute('UPDATE nx_market_wallets SET balance_toman=balance_toman+?,updated=? WHERE user_id=?',(o['total_toman'],now(),o['seller_id']))
            elif status=='cancelled' and o['seller_id']==u['id'] and o['status']=='paid':
                db().execute("UPDATE nx_market_orders SET status='cancelled',updated=? WHERE id=?",(now(),oid))
                stars_move(o['buyer_id'],o['stars_paid'],'بازپرداخت سفارش لغوشده #%d'%oid,ref='market-refund:%d'%oid)
                db().execute('UPDATE nx_market_products SET stock=stock+?,updated=? WHERE id=?',(o['quantity'],now(),o['product_id']))
            else: raise Error('تغییر وضعیت برای این کاربر یا وضعیت سفارش مجاز نیست',403)
            db().commit()
        except Exception: db().rollback(); raise
        return ok(status=status)

    @app.get('/api/market/wallet')
    def market_wallet():
        u=auth(); w=q('SELECT balance_toman FROM nx_market_wallets WHERE user_id=?',(u['id'],),True); rows=q('SELECT delta_toman,reason,created FROM nx_market_ledger WHERE user_id=? ORDER BY id DESC LIMIT 50',(u['id'],))
        return ok(balance_toman=w['balance_toman'] if w else 0,withdrawals_enabled=False,bank_card_transfer_enabled=False,ledger=[dict(r) for r in rows])

    @app.post('/api/market/orders/<int:oid>/review')
    def market_review(oid):
        u=auth(); d=request.get_json(silent=True) or {}; o=q("SELECT * FROM nx_market_orders WHERE id=? AND buyer_id=? AND status='completed'",(oid,u['id']),True)
        if not o: raise Error('فقط خریدار سفارش تکمیل‌شده می‌تواند نظر بدهد')
        try: rating=int(d.get('rating'))
        except Exception: raise Error('امتیاز نامعتبر است')
        if not 1<=rating<=5: raise Error('امتیاز باید بین ۱ تا ۵ باشد')
        x('INSERT INTO nx_market_reviews(order_id,buyer_id,seller_id,rating,comment,created) VALUES(?,?,?,?,?,?)',(oid,u['id'],o['seller_id'],rating,str(d.get('comment') or '')[:500],now())); return ok()

    @app.get('/api/market/sellers/<int:uid>/rating')
    def market_rating(uid):
        r=q('SELECT ROUND(AVG(rating),2) rating,COUNT(*) count FROM nx_market_reviews WHERE seller_id=?',(uid,),True)
        return ok(rating=r['rating'] or 0,reviews=r['count'])

    @app.get('/api/infinity/health')
    def infinity_health():
        return ok(version='Infinity foundation 0.1',modules={'legacy_messaging':'preserved','bot_api':'1.0 core','market':'orders and internal seller wallet'},database='sqlite')
