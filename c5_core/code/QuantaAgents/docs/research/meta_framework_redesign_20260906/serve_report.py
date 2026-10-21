from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'output' / 'reports' / 'meta_framework_redesign_20260906.html'
qa = json.loads((HERE/'artifact_qa.json').read_text(encoding='utf-8'))
allowed = {OUT.resolve()}
for row in qa['links']:
    if not row['url'].startswith('https://'):
        allowed.add((OUT.parent/row['url']).resolve())

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def send_head(self):
        if urlsplit(self.path).path == '/':
            self.send_response(302)
            self.send_header('Location','/output/reports/meta_framework_redesign_20260906.html')
            self.end_headers()
            return None
        candidate = Path(self.translate_path(unquote(urlsplit(self.path).path))).resolve()
        if candidate not in allowed:
            self.send_error(404)
            return None
        return super().send_head()

ThreadingHTTPServer(('192.0.2.10', 8772), Handler).serve_forever()
