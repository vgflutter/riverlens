#!/usr/bin/env python3
"""RiverLens local study server. Python 3.10+, standard library only."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from engine import analyze, cards
from history import parse_histories
from archive import Archive, UNITS
from decisions import cents
from study import Study
from screen import AnalysisExpired, analyze_screen, request_options

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
WORK = threading.BoundedSemaphore(2)
ARCHIVE = Archive(ROOT / 'private' / 'riverlens.sqlite3')
STUDY = Study(ARCHIVE, lambda: parse_histories((ROOT / 'examples' / 'demo.txt').read_text())['hands'])
API_LOCK = threading.Lock()
API_USAGE = {'attempts': 0, 'input_tokens': 0, 'output_tokens': 0}


def prompt(name):
    return (ROOT / 'prompts' / (name + '_system.txt')).read_text()


def call_limit():
    try:
        return max(0,min(1000,int(os.getenv('OPENAI_MAX_CALLS','20'))))
    except ValueError:
        return 20


def load_config():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                if key.strip() in ('OPENAI_API_KEY', 'OPENAI_MODEL', 'OPENAI_SCREEN_MODEL', 'OPENAI_MAX_CALLS'):
                    os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def config():
    return {'ai_ready': bool(os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_MODEL')),
            'screen_ai_ready': bool(os.getenv('OPENAI_API_KEY') and screen_model()),
            'screen_model': screen_model(),
            'model': os.getenv('OPENAI_MODEL', ''), 'version': '0.3.0',
            'api_usage': dict(API_USAGE), 'api_call_limit': call_limit(), 'default_style':'decisive_positive_ev'}


def screen_model():
    return os.getenv('OPENAI_SCREEN_MODEL', '').strip() or os.getenv('OPENAI_MODEL', '')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def response_text(instructions, content, schema=None, *, model=None, max_output_tokens=1600, timeout=50):
    model = model or os.getenv('OPENAI_MODEL', '')
    if not os.getenv('OPENAI_API_KEY') or not model:
        raise ValueError('Configura OPENAI_API_KEY e OPENAI_MODEL nel file .env e riavvia RiverLens.')
    payload = {'model': model, 'instructions': instructions,
               'input': [{'role': 'user', 'content': content}], 'store': False,
               'max_output_tokens': max_output_tokens}
    if schema:
        payload['text'] = {'format': {'type':'json_schema', 'name':'riverlens_decision', 'strict':True, 'schema':schema}}
    with API_LOCK:
        if API_USAGE['attempts'] >= call_limit():
            raise ValueError('Limite di chiamate API per questo avvio raggiunto. Nessuna nuova analisi AI disponibile.')
        API_USAGE['attempts'] += 1
    request = urllib.request.Request('https://api.openai.com/v1/responses',
        data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'],
                                                   'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as reply:
            result = json.load(reply)
    except urllib.error.HTTPError as error:
        raise ValueError(f'OpenAI ha risposto HTTP {error.code}. Verifica modello, chiave, credito e limiti API.') from None
    except (urllib.error.URLError, TimeoutError):
        raise ValueError('OpenAI non raggiungibile o timeout. Nessuna nuova analisi AI disponibile.') from None
    if result.get('status') != 'completed':
        raise ValueError('Risposta OpenAI incompleta. Riprova con un modello adatto a risposte brevi.')
    with API_LOCK:
        for field in ('input_tokens','output_tokens'):
            API_USAGE[field] += int(result.get('usage',{}).get(field,0))
    if any(part.get('type')=='refusal' for item in result.get('output',[]) if item.get('type')=='message' for part in item.get('content',[])):
        raise ValueError('Il modello non ha fornito una proposta AI.')
    text = '\n'.join(part.get('text', '') for item in result.get('output', [])
                     if item.get('type') == 'message' for part in item.get('content', [])
                     if part.get('type') == 'output_text')
    if not text:
        raise ValueError('OpenAI non ha restituito testo utilizzabile.')
    return text


def explain(data):
    if data.get('consent') is not True:
        raise ValueError('Conferma l’invio dei dati a OpenAI.')
    # Recompute locally: never trust client-provided calculated metrics.
    result = analyze(data.get('scenario', {}))
    text = response_text(prompt('explanation'),
        [{'type': 'input_text', 'text': json.dumps(result, ensure_ascii=False)}])
    return {'text': text, 'model': os.environ['OPENAI_MODEL']}


def vision(data):
    if data.get('consent') is not True or data.get('archived') is not True:
        raise ValueError('Conferma che è un’immagine archiviata e che vuoi inviarla a OpenAI.')
    url = data.get('image', '')
    if not isinstance(url, str) or not re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+', url):
        raise ValueError('Usa un’immagine PNG, JPEG o WebP.')
    try:
        raw = base64.b64decode(url.split(',', 1)[1], validate=True)
    except ValueError:
        raise ValueError('Immagine non valida.') from None
    if len(raw) > 4_000_000:
        raise ValueError('L’immagine deve pesare meno di 4 MB.')
    answer = response_text(prompt('vision'),
        [{'type': 'input_text', 'text': 'Trascrivi questo tavolo archiviato per una revisione manuale.'},
         {'type': 'input_image', 'image_url': url, 'detail': 'high'}])
    try:
        answer = re.sub(r'^```(?:json)?\s*|\s*```$', '', answer.strip())
        parsed = json.loads(answer)
        if not isinstance(parsed, dict):
            raise ValueError()
        for field in ('hero', 'board'):
            parsed[field] = cards(parsed.get(field, []))
        if len(parsed['hero']) not in (0, 2) or len(parsed['board']) not in (0, 3, 4, 5):
            raise ValueError()
        if len(set(parsed['hero'] + parsed['board'])) != len(parsed['hero'] + parsed['board']):
            raise ValueError()
        amounts = ('pot', 'call', 'stack', 'account_balance', 'big_blind',
                   'hero_bet', 'invested', 'min_raise_increment')
        for field in (*amounts, 'opponents'):
            val = parsed.get(field)
            if val is not None and (isinstance(val, bool) or not isinstance(val, (float, int)) or not 0 <= val <= 1e9):
                raise ValueError()
            if val is not None:
                cents(val, field)
        if parsed.get('opponents') is not None and (not 1 <= parsed['opponents'] <= 8 or parsed['opponents'] != int(parsed['opponents'])):
            raise ValueError()
        for field in ('unit', 'account_unit'):
            if parsed.get(field) is not None and parsed[field] not in UNITS:
                raise ValueError()
        if parsed.get('raise_reopened') is not None and not isinstance(parsed['raise_reopened'], bool):
            raise ValueError()
        if parsed.get('notes') is not None and not isinstance(parsed['notes'], str):
            raise ValueError()
        return {k: parsed.get(k) for k in ('hero', 'board', *amounts, 'opponents', 'unit',
                                           'account_unit', 'raise_reopened', 'notes')}
    except (ValueError, TypeError, KeyError):
        raise ValueError('La lettura AI non è coerente. Inserisci o correggi i dati manualmente.') from None


def recommend(data):
    def invoke(payload, schema):
        return json.loads(response_text(prompt('decision'), [{'type':'input_text','text':json.dumps(payload,ensure_ascii=False)}], schema))
    return STUDY.recommend(data, ai_call=invoke if config()['ai_ready'] else None,
                           model=os.getenv('OPENAI_MODEL',''), prompt=prompt('decision'))


def screen_analysis(data):
    mode, deadline_ms = request_options(data)
    model = screen_model()
    def invoke(instructions, content, schema):
        return response_text(instructions, content, schema, model=model,
                             max_output_tokens=400 if mode == 'fast' else 1600,
                             timeout=deadline_ms / 1000)
    return analyze_screen(data, invoke, model)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Do not log hand histories, screenshots, or model payloads.

    def send(self, status, data, mime='application/json; charset=utf-8'):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode()
        elif isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def host_ok(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, {'error': 'Host non autorizzato.'})
        routes = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/screen': ('screen.html', 'text/html; charset=utf-8'),
                  '/screen.js': ('screen.js', 'text/javascript; charset=utf-8'),
                  '/screen.css': ('screen.css', 'text/css; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if self.path == '/api/status':
            return self.send(200, config())
        if self.path == '/api/examples':
            return self.send(200, parse_histories((ROOT / 'examples' / 'demo.txt').read_text()))
        if self.path == '/api/archive':
            return self.send(200, {'hands': ARCHIVE.hands()})
        if self.path == '/api/session':
            return self.send(200, {'budget': ARCHIVE.budget()})
        if self.path in routes:
            file, mime = routes[self.path]
            return self.send(200, (ROOT / 'static' / file).read_text().replace('__TOKEN__', TOKEN), mime)
        return self.send(404, {'error': 'Non trovato.'})

    def do_POST(self):
        if not self.host_ok() or not secrets.compare_digest(self.headers.get('X-RiverLens-Token', ''), TOKEN):
            return self.send(403, {'error': 'Sessione non valida. Ricarica la pagina.'})
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
            return self.send(403, {'error': 'Origine non autorizzata.'})
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 6_000_000:
                return self.send(413, {'error': 'Richiesta troppo grande o vuota.'})
            if self.headers.get_content_type() != 'application/json':
                return self.send(415, {'error': 'Formato JSON richiesto.'})
            self.connection.settimeout(15)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('Il corpo della richiesta deve essere un oggetto JSON.')
            if self.path == '/api/import':
                parsed = parse_histories(data.get('text', ''))
                counts = ARCHIVE.import_hands(parsed['hands'])
                return self.send(200, {**parsed, **counts, 'hands': ARCHIVE.hands()})
            if self.path == '/api/session':
                return self.send(200, {'budget': ARCHIVE.save_budget(data)})
            methods = {'/api/analyze': lambda x: analyze(x), '/api/explain': explain, '/api/vision': vision,
                       '/api/recommend': recommend, '/api/screen/analyze': screen_analysis}
            if self.path not in methods:
                return self.send(404, {'error': 'Non trovato.'})
            if not WORK.acquire(blocking=False):
                return self.send(429, {'error': 'Analisi in corso. Attendi e riprova.'})
            try:
                self.send(200, methods[self.path](data))
            finally:
                WORK.release()
        except AnalysisExpired as error:
            self.send(408, {'error': str(error), 'code': 'deadline_exceeded'})
        except (ValueError, TypeError, TimeoutError, UnicodeError) as error:
            self.send(400, {'error': str(error)[:300]})
        except Exception:
            self.send(500, {'error': 'Errore interno. Verifica i dati e riprova.'})


def main():
    load_config()
    parser = argparse.ArgumentParser(description='RiverLens · screen analysis and poker study')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError as error:
        raise SystemExit(f'Impossibile avviare RiverLens: {error}. Prova --port 8766.') from None
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'RiverLens 0.3.0 · {url}\nAnalizza schermo: {url}/screen\nCtrl+C per terminare.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
