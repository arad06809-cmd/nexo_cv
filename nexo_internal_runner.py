"""Small, constrained Python runtime for NEXO Bot Studio.

This is an in-app execution backend, not a hardened multi-tenant security boundary.
It runs each callback in a short-lived child process with strict resource limits,
restricted imports/builtins, no writable filesystem access, and no network API.
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
import shutil

MAX_SOURCE_BYTES = 100_000
MAX_OUTPUT = 12_000
TIMEOUT_SECONDS = 2.5

_WRAPPER = r'''import ast, builtins, contextlib, datetime, io, json, math, random, re, sys
payload=json.loads(sys.stdin.read())
source=payload['source']
mode=payload.get('mode','message')
message=payload.get('message') or {}

ALLOWED_IMPORTS={'json':json,'math':math,'random':random,'re':re,'datetime':datetime}
SAFE_BUILTINS={k:getattr(builtins,k) for k in (
 'abs','all','any','bool','dict','enumerate','filter','float','int','isinstance',
 'iter','len','list','map','max','min','next','range','reversed','round','set',
 'slice','sorted','str','sum','tuple','zip','print','Exception','ValueError',
 'TypeError','KeyError','IndexError','ZeroDivisionError','RuntimeError'
)}
def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name not in ALLOWED_IMPORTS:
        raise ImportError('این کتابخانه در محیط اجرای NEXO مجاز نیست: '+name)
    return ALLOWED_IMPORTS[name]
SAFE_BUILTINS['__import__']=safe_import

class Guard(ast.NodeVisitor):
    def generic_visit(self,node):
        forbidden=(ast.ClassDef, ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom,
                   ast.Global, ast.Nonlocal, ast.With, ast.AsyncWith, ast.Delete, ast.Lambda)
        if isinstance(node,forbidden): raise ValueError('ساختار کد در محیط محدود NEXO مجاز نیست: '+type(node).__name__)
        if isinstance(node,ast.Name) and (node.id.startswith('__') or node.id in {
          'eval','exec','compile','open','input','globals','locals','vars','getattr',
          'setattr','delattr','breakpoint','help','memoryview','property','classmethod',
          'staticmethod','super','object','type','bytes','bytearray'
        }): raise ValueError('نام یا تابع غیرمجاز: '+node.id)
        if isinstance(node,ast.Attribute) and node.attr.startswith('_'):
            raise ValueError('دسترسی خصوصی در محیط اجرا مجاز نیست')
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            names=[a.name.split('.')[0] for a in node.names]
            if any(n not in ALLOWED_IMPORTS for n in names): raise ValueError('کتابخانه غیرمجاز: '+', '.join(names))
        super().generic_visit(node)

try:
    tree=ast.parse(source, filename='<nexo-bot>', mode='exec')
    Guard().visit(tree)
except BaseException as e:
    sys.stdout.write(json.dumps({'ok':False,'error':type(e).__name__+': '+str(e),'output':'','sent':[]},ensure_ascii=False))
    raise SystemExit(0)
class Bot:
    def __init__(self): self.sent=[]
    def send_message(self, chat_id, text):
        text=str(text)
        if len(self.sent)>=5: raise ValueError('حداکثر ۵ پیام در هر رویداد مجاز است')
        if len(text)>4000: raise ValueError('متن پیام بیشتر از ۴۰۰۰ نویسه است')
        self.sent.append({'chat_id':int(chat_id),'text':text})
    def log(self, value): print(value)
bot=Bot()
g={'__builtins__':SAFE_BUILTINS,'bot':bot,'message':message}
out=io.StringIO()
try:
    with contextlib.redirect_stdout(out):
        exec(compile(tree,'<nexo-bot>','exec'),g,g)
        if mode=='start':
            fn=g.get('on_start')
            if callable(fn): fn(bot)
        elif mode=='message':
            fn=g.get('on_message')
            if callable(fn): fn(message,bot)
            elif callable(g.get('main')): g['main']()
            else: print('کد اجرا شد؛ برای دریافت پیام‌ها تابع on_message(message, bot) تعریف کن.')
    result={'ok':True,'output':out.getvalue()[-10000:],'sent':bot.sent}
except BaseException as e:
    result={'ok':False,'error':type(e).__name__+': '+str(e),'output':out.getvalue()[-8000:]}
sys.stdout.write(json.dumps(result,ensure_ascii=False))
'''


def execute_python(source, mode='message', message=None):
    if not isinstance(source, str) or len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
        return {'ok': False, 'error': 'حجم کد نامعتبر است', 'output': '', 'sent': []}
    if source.count('\n') + (1 if source else 0) > 100:
        return {'ok': False, 'error': 'کد هر ربات حداکثر می‌تواند ۱۰۰ خط باشد', 'output': '', 'sent': []}
    payload=json.dumps({'source':source,'mode':mode,'message':message or {}},ensure_ascii=False)
    with tempfile.TemporaryDirectory(prefix='nexo-bot-') as work:
        try:
            command=[sys.executable,'-I','-S','-c',_WRAPPER]
            prlimit=shutil.which('prlimit') if os.name=='posix' else None
            if os.name=='posix' and not prlimit:
                return {'ok':False,'error':'ابزار محدودسازی منابع روی این میزبان در دسترس نیست؛ اجرای کد برای ایمنی غیرفعال شد.','output':'','sent':[]}
            if prlimit:
                command=[prlimit,'--cpu=2:2','--as=134217728:134217728','--fsize=0:0','--nofile=16:16','--nproc=1:1','--',*command]
            cp=subprocess.run(command,input=payload,
                text=True,capture_output=True,cwd=work,timeout=TIMEOUT_SECONDS,
                start_new_session=True,
                env={'PATH':os.environ.get('PATH',''),'PYTHONIOENCODING':'utf-8'})
            raw=cp.stdout[-MAX_OUTPUT:]
            try: result=json.loads(raw)
            except Exception:
                if cp.returncode and cp.returncode < 0:
                    reason='کد از محدودیت منابع موتور اجرا عبور کرد و متوقف شد.'
                else:
                    reason='موتور اجرا خروجی معتبر نداد: '+(cp.stderr or raw)[-2000:]
                result={'ok':False,'error':reason,'output':'','sent':[]}
            return result
        except subprocess.TimeoutExpired:
            return {'ok':False,'error':'زمان اجرای کد تمام شد (حداکثر ۲٫۵ ثانیه برای هر رویداد)','output':'','sent':[]}
        except Exception as exc:
            return {'ok':False,'error':'خطای موتور اجرا: '+str(exc)[:1000],'output':'','sent':[]}
