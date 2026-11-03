import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deployment_manager', Path(__file__).resolve().parents[1] / 'deployment/manage.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


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
            path.write_text(json.dumps({'pid': 1234}))
            with patch.object(manager, 'PIDFILE', path), patch.object(manager.platform, 'system', return_value='Windows'), patch.object(manager, 'windows_process', return_value=None), patch.object(manager.os, 'kill') as kill:
                self.assertIsNone(manager.running())
                kill.assert_not_called()
