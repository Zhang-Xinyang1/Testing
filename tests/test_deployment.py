import http.server
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deployment_manager', Path(__file__).resolve().parents[1] / 'deployment/manage.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class DeploymentContractTests(unittest.TestCase):
    def test_complete_frontend_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in manager.REQUIRED_FRONTEND:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if name == 'index.html':
                    path.write_text('<div id="app"></div>' + ''.join(manager.FRONTEND_MARKERS), encoding='utf-8')
                else:
                    path.write_bytes(b'x')
            self.assertEqual(manager.frontend_problems(root), [])

    def test_stale_or_incomplete_frontend_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'index.html').write_text('<div id="app"></div>', encoding='utf-8')
            problems = manager.frontend_problems(root)
            self.assertTrue(any('id="btn-import"' in problem for problem in problems))
            self.assertTrue(any('assets/js/inputs.js' in problem for problem in problems))
            self.assertTrue(any('assets/js/maintenance.js' in problem for problem in problems))

    def test_data_contract_requires_frontend_fields(self):
        data = {'mps': [], 'eqp': [], 'recipes': [], 'lots': [], 'flows': {}, 'meta': {'month': '2026-09'}}
        self.assertEqual(manager.data_problems(data), [])
        self.assertTrue(manager.data_problems({'meta': {}}))

    def test_backend_requires_workbook_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in manager.REQUIRED_BACKEND:
                path = root / 'backend' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')
            self.assertEqual(manager.backend_problems(root), ['tools/build_data.py'])
            tools = root / 'tools' / 'build_data.py'
            tools.parent.mkdir(parents=True, exist_ok=True)
            tools.write_bytes(b'x')
            self.assertEqual(manager.backend_problems(root), [])


class HealthReportTests(unittest.TestCase):
    def serve(self, frontend=True, data=True):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/api/health':
                    body = b'{"ok": true}'
                elif self.path == '/':
                    body = b'<div id="app"></div>'
                    if frontend:
                        body += b''.join(marker.encode() for marker in manager.FRONTEND_MARKERS)
                elif self.path == '/api/data':
                    body = json.dumps({
                        'mps': [1] if data else [],
                        'lots': [1] if data else [],
                        'eqp': [1] if data else [],
                    }).encode()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html' if self.path == '/' else 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port

    def test_all_three_layers_must_be_ready(self):
        report = manager.health_report('127.0.0.1', self.serve(frontend=True, data=True))
        self.assertTrue(report['ok'], report)
        self.assertTrue(report['api'] and report['frontend'] and report['data'])

    def test_stale_frontend_blocks_startup(self):
        report = manager.health_report('127.0.0.1', self.serve(frontend=False, data=True))
        self.assertFalse(report['ok'])
        self.assertFalse(report['frontend'])
        self.assertIn('首页', report['error'])

    def test_empty_data_blocks_startup(self):
        report = manager.health_report('127.0.0.1', self.serve(frontend=True, data=False))
        self.assertFalse(report['ok'])
        self.assertFalse(report['data'])
        self.assertIn('数据接口', report['error'])


class BrowserPolicyTests(unittest.TestCase):
    def test_explicit_switch(self):
        with patch.dict(os.environ, {'XMC_OPEN_BROWSER': '0'}, clear=False):
            self.assertFalse(manager.should_open_browser())
        with patch.dict(os.environ, {'XMC_OPEN_BROWSER': '1'}, clear=False):
            self.assertTrue(manager.should_open_browser())

    def test_ssh_does_not_open_browser(self):
        with patch.dict(os.environ, {'SSH_CONNECTION': '10.0.0.1 1 10.0.0.2 22'}, clear=True), \
                patch.object(manager.platform, 'system', return_value='Darwin'):
            self.assertFalse(manager.should_open_browser())

    def test_desktop_and_headless_linux_differ(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(manager.platform, 'system', return_value='Darwin'):
            self.assertTrue(manager.should_open_browser())
        with patch.dict(os.environ, {}, clear=True), patch.object(manager.platform, 'system', return_value='Linux'):
            self.assertFalse(manager.should_open_browser())
        with patch.dict(os.environ, {'DISPLAY': ':0'}, clear=True), patch.object(manager.platform, 'system', return_value='Linux'):
            self.assertTrue(manager.should_open_browser())


class WindowsProcessTests(unittest.TestCase):
    def test_owned_process_requires_creation_time_and_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'process.json'
            path.write_text(json.dumps({'pid': 1234, 'config': 'C:\\Testing\\config.json', 'created': 'time1'}))
            info = {'CommandLine': 'python "' + str(manager.SERVER) + '" --config "C:\\Testing\\config.json"', 'CreationDate': 'time1'}
            with patch.object(manager, 'PIDFILE', path), patch.object(manager.platform, 'system', return_value='Windows'), patch.object(manager, 'windows_process', return_value=info):
                self.assertEqual(manager.running()['pid'], 1234)
                info['CreationDate'] = 'different'
                with self.assertRaises(RuntimeError):
                    manager.running()

    def test_missing_windows_process_does_not_use_os_kill(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'process.json'
            path.write_text(json.dumps({'pid': 1234, 'config': '/tmp/config.json'}))
            with patch.object(manager, 'PIDFILE', path), patch.object(manager.platform, 'system', return_value='Windows'), patch.object(manager, 'windows_process', return_value=None), patch.object(manager.os, 'kill') as kill:
                self.assertIsNone(manager.running())
                kill.assert_not_called()
