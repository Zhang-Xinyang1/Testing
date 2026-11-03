#!/usr/bin/env python3
"""HTTP server for the Testing scheduling demo (zero dependencies)."""
import argparse, json, os, sys, traceback, tempfile, threading, uuid, zipfile
from io import BytesIO
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

from server_core import ROOT, DEFAULT_CONFIG, load_config, public_config, build_payload, read_workbook
from data_sources import oracle_settings

sys.path.insert(0, ROOT)
STATIC_ROOT = os.path.join(ROOT, 'frontend')
CONFIG_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = 'TestingSchedulerDemo/0.2'

    def log_message(self, fmt, *args):
        sys.stderr.write('[%s] %s\n' % (datetime.now().strftime('%H:%M:%S'), fmt % args))

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == '/api/health':
                return self._json({'ok': True, 'time': datetime.now().isoformat()})
            if path == '/api/config':
                return self._json(public_config(self.server.config_path))
            if path == '/api/data':
                return self._json(build_payload(self.server.config_path))
            return self.serve_static(path)
        except Exception as e:
            traceback.print_exc()
            self._json({'ok': False, 'error': str(e)}, 500)

    def do_POST(self):
        endpoint = urlparse(self.path).path
        if endpoint not in ('/api/config', '/api/preview', '/api/uploads', '/api/oracle/config'):
            return self._json({'error': 'Unknown endpoint'}, 404)
        # Configuration writes are same-origin, even when deployed behind a proxy.
        from urllib.parse import urlsplit
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            return self._json({'error': 'Cross-origin write rejected'}, 403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if endpoint == '/api/uploads':
                return self.upload_excel(length)
            if not 0 < length <= 2_000_000:
                raise ValueError('配置大小无效')
            update = json.loads(self.rfile.read(length))
            if endpoint == '/api/oracle/config':
                settings = oracle_settings(update)
                with CONFIG_LOCK:
                    cfg = load_config(self.server.config_path)
                    cfg['oracle'] = settings
                    self.persist_config(cfg)
                return self._json({'ok': True, 'status': 'reserved', 'oracle': settings})
            if not isinstance(update, dict) or set(update) - {'dataSource', 'fieldMap', 'scheduling'}:
                raise ValueError('不支持的配置字段')
            with CONFIG_LOCK:
                cfg = load_config(self.server.config_path)
                cfg.update(update)
                payload = build_payload(self.server.config_path, config=cfg)
                if endpoint == '/api/preview':
                    return self._json({'ok': True, 'data': payload})
                if payload.get('planIssues'):
                    raise ValueError('；'.join(payload['planIssues'][:5]))
                self.persist_config(cfg)
            return self._json({'ok': True, 'data': payload, 'config': public_config(self.server.config_path)})
        except Exception as e:
            return self._json({'ok': False, 'error': str(e)}, 400)

    def persist_config(self, cfg):
        fd, temp = tempfile.mkstemp(dir=os.path.dirname(self.server.config_path), suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.server.config_path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def upload_excel(self, length):
        name = unquote(self.headers.get('X-Filename', ''))
        if not name.lower().endswith('.xlsx') or not 0 < length <= 20 * 1024 * 1024:
            raise ValueError('请选择不超过 20 MB 的 .xlsx 文件')
        content = self.rfile.read(length)
        if len(content) != length:
            raise ValueError('上传文件不完整')
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 3000 or sum(x.file_size for x in entries) > 100 * 1024 * 1024:
                raise ValueError('Excel 解压后过大')
            if 'xl/workbook.xml' not in archive.namelist():
                raise ValueError('不是有效的 XLSX 工作簿')
        directory = os.path.join(os.path.dirname(self.server.config_path), 'uploads')
        os.makedirs(directory, exist_ok=True)
        target = os.path.join(directory, uuid.uuid4().hex + '.xlsx')
        try:
            with open(target, 'xb') as f:
                f.write(content)
            sheets = read_workbook(target)
        except Exception:
            if os.path.exists(target):
                os.unlink(target)
            raise
        return self._json({'ok': True, 'path': target,
                           'name': os.path.basename(name.replace('\\', '/')),
                           'sheets': [sheet for sheet, _ in sheets]})

    def serve_static(self, path):
        if path in ('', '/'):
            path = '/index.html'
        if path in ('/config.json', '/config/config.json'):
            target = self.server.config_path
            root_abs = None
        else:
            rel = unquote(path.lstrip('/'))
            target = os.path.abspath(os.path.join(STATIC_ROOT, rel))
            root_abs = os.path.abspath(STATIC_ROOT)
            if not target.startswith(root_abs + os.sep) and target != root_abs:
                self.send_error(403)
                return
        if not os.path.isfile(target):
            self.send_error(404)
            return
        ext = os.path.splitext(target)[1].lower()
        mime = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }.get(ext, 'application/octet-stream')
        with open(target, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=DEFAULT_CONFIG)
    ap.add_argument('--host', default=None)
    ap.add_argument('--port', type=int, default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    host = args.host or cfg.get('server', {}).get('host', '0.0.0.0')
    port = args.port or cfg.get('server', {}).get('port', 8080)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.config_path = os.path.abspath(args.config)
    print('Testing scheduling demo')
    print('  config: %s' % httpd.config_path)
    print('  open:   http://127.0.0.1:%d' % port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
