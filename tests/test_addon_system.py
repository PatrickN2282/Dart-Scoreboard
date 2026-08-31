import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import addon_system


class AddonSystemTests(unittest.TestCase):
    def test_repository_manifests_are_discovered(self):
        manifests = addon_system.discover_addons('Addons')
        self.assertEqual({'cec', 'kiosk', 'screensaver'}, set(manifests))
        self.assertEqual('dart-screensaver.service', manifests['screensaver']['service'])

    def test_manifest_cannot_escape_addon_or_home(self):
        with tempfile.TemporaryDirectory() as root_name, tempfile.TemporaryDirectory() as home_name:
            root = Path(root_name)
            addon = root / 'Bad'
            addon.mkdir()
            (addon / 'payload').write_text('safe', encoding='utf-8')
            manifest = {
                'id': 'bad', 'name': 'Bad', 'version': '1', 'kind': 'systemd-user',
                'service': 'bad.service',
                'files': [{'source': 'payload', 'destination': '../outside', 'mode': '0644'}],
            }
            (addon / 'addon.json').write_text(json.dumps(manifest), encoding='utf-8')
            loaded = addon_system.discover_addons(root)['bad']
            with self.assertRaises(addon_system.AddonError):
                addon_system.install_addon(loaded, home_name)

    def test_install_uses_only_fixed_systemctl_arguments(self):
        with tempfile.TemporaryDirectory() as root_name, tempfile.TemporaryDirectory() as home_name:
            root = Path(root_name)
            addon = root / 'Good'
            addon.mkdir()
            (addon / 'run.sh').write_text('#!/bin/sh\n', encoding='utf-8')
            (addon / 'unit.service').write_text('[Service]\n', encoding='utf-8')
            manifest = {
                'id': 'good', 'name': 'Good', 'version': '1', 'kind': 'systemd-user',
                'service': 'good.service',
                'files': [
                    {'source': 'run.sh', 'destination': '.local/bin/good', 'mode': '0755'},
                    {'source': 'unit.service', 'destination': '.config/systemd/user/good.service', 'mode': '0644'},
                ],
            }
            (addon / 'addon.json').write_text(json.dumps(manifest), encoding='utf-8')
            loaded = addon_system.discover_addons(root)['good']
            completed = subprocess.CompletedProcess([], 0, '', '')
            with patch('addon_system._systemctl', return_value=completed) as systemctl:
                addon_system.install_addon(loaded, home_name)

            self.assertTrue((Path(home_name) / '.local/bin/good').is_file())
            self.assertEqual(
                [
                    unittest.mock.call(['daemon-reload']),
                    unittest.mock.call(['enable', '--now', 'good.service']),
                    unittest.mock.call(['restart', 'good.service']),
                ],
                systemctl.call_args_list,
            )

    def test_unknown_action_is_rejected_before_process_execution(self):
        manifest = {'service': 'safe.service'}
        with patch('addon_system._systemctl') as systemctl:
            with self.assertRaises(addon_system.AddonError):
                addon_system.manage_addon(manifest, 'start;reboot', tempfile.gettempdir())
        systemctl.assert_not_called()

    def test_screensaver_resume_does_not_kill_its_own_idle_watcher(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / 'Addons/Raspberry-Screensaver/dart_screensaver.sh').read_text(
            encoding='utf-8'
        )
        service = (root / 'Addons/Raspberry-Screensaver/dart-screensaver.service').read_text(
            encoding='utf-8'
        )

        self.assertIn("printf -v stop_command '%q --hide'", script)
        self.assertIn('CHROMIUM_PID_FILE=', script)
        self.assertNotIn("printf -v stop_command 'pkill -f", script)
        self.assertIn('Restart=always', service)


if __name__ == '__main__':
    unittest.main()
