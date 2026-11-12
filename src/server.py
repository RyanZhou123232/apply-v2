"""Minimal local list, not a full dashboard or application executor."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import os
from pathlib import Path
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser
import workflow

TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
PROGRESS = {'running':False,'error':None}

def work():
    try:
        workflow.refresh()
    except Exception as e:
        PROGRESS['error'] = str(e)[:300]
    finally:
        PROGRESS['running'] = False
        LOCK.release()

def screen_status():
    path = workflow.ROOT / 'runs' / 'screen-ui.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass
    def respond(self,code,body,kind='application/json; charset=utf-8'):
        raw = body if isinstance(body,bytes) else json.dumps(body,ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type',kind)
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def allowed(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'
    def do_GET(self):
        if not self.allowed(): return self.respond(403,{'error':'Forbidden host'})
        path = urllib.parse.urlsplit(self.path).path
        if path == '/health':
            return self.respond(200,{'service':'local-internship-workflow-v1'})
        if path == '/':
            html = (Path(__file__).parent / 'index.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN)
            return self.respond(200,html.encode(),'text/html; charset=utf-8')
        if path == '/api/jobs' and self.headers.get('X-Local-Token') == TOKEN:
            return self.respond(200,workflow.snapshot() | {'progress':PROGRESS, 'screen':screen_status()})
        return self.respond(404,{'error':'Not found'})
    def do_POST(self):
        origin = self.headers.get('Origin')
        expected = f'http://127.0.0.1:{self.server.server_port}'
        if not self.allowed() or origin != expected or self.headers.get('X-Local-Token') != TOKEN:
            return self.respond(403,{'error':'Forbidden'})
        if self.path != '/api/refresh': return self.respond(404,{'error':'Not found'})
        if not LOCK.acquire(blocking=False): return self.respond(409,{'error':'Already running'})
        PROGRESS.update(running=True,error=None)
        threading.Thread(target=work,daemon=True).start()
        return self.respond(202,{'running':True})

if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--no-browser',action='store_true')
    args.add_argument('--port', type=int, default=0)
    args = args.parse_args()
    server_state = workflow.ROOT / 'state' / 'local-server.json'
    if server_state.exists():
        try:
            old = json.loads(server_state.read_text())
            previous_url = f"http://127.0.0.1:{int(old['port'])}"
            with urllib.request.urlopen(previous_url+'/health',timeout=2) as r:
                if json.load(r).get('service')=='local-internship-workflow-v1':
                    if not args.no_browser: webbrowser.open(previous_url)
                    raise SystemExit(0)
        except (OSError,ValueError,KeyError): pass
    server = ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    server_state.parent.mkdir(parents=True,exist_ok=True)
    server_state.write_text(json.dumps({'port':server.server_port,'pid':os.getpid()}),encoding='utf-8')
    print('Local list: ' + url,flush=True)
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

