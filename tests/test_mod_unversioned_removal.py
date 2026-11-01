"""Exercise Apply against real files, without a Tk display or network."""
import copy
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from equpdater import app


class TestUnversionedRemoval(unittest.TestCase):
    def run_apply(self, managed=True, enabled=False, partial=False):
        with tempfile.TemporaryDirectory() as client:
            mod = copy.deepcopy(next(m for m in app.MODS_REGISTRY
                                     if m['id'] == 'VanillaFixes'))
            files = mod['installed_files']
            for rel in files[:1] if partial else files:
                path = os.path.join(client, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'wb') as f:
                    f.write(b'original adopted file')
            cfg = {'mods': {mod['id']: {
                'managed': managed, 'adopted': managed,
                'enabled': True, 'installed_version': None,
                'installed_files': files,
            }}}

            def update(fn):
                fn(cfg)
                return cfg

            worker = SimpleNamespace(
                _mod_pending_state={mod['id']: {'enabled': enabled}},
                after=lambda *args: None)
            with mock.patch.object(app, 'MODS_REGISTRY', [mod]), \
                    mock.patch.object(app, 'load_config', side_effect=lambda: copy.deepcopy(cfg)), \
                    mock.patch.object(app, 'update_config', side_effect=update), \
                    mock.patch.object(app, '_fetch_release_cached', return_value=None) as lookup, \
                    mock.patch.object(app, 'install_mod') as install, \
                    mock.patch.object(app, 'log'), \
                    mock.patch.object(app, 'reconcile_dlls_txt', return_value=[]):
                app.EqUpdaterApp._apply_mods_worker(worker, client)
            remaining = [rel for rel in files
                         if os.path.exists(os.path.join(client, rel))]
            return remaining, cfg['mods'][mod['id']], lookup.call_count, install.call_count

    def test_adopted_mod_uninstalls_without_a_version_or_network(self):
        remaining, state, lookups, installs = self.run_apply()
        self.assertEqual(remaining, [])
        self.assertFalse(state['enabled'])
        self.assertFalse(state['managed'])
        self.assertEqual((lookups, installs), (0, 0))

    def test_partially_missing_adopted_mod_is_also_removed(self):
        self.assertEqual(self.run_apply(partial=True)[0], [])

    def test_unmanaged_files_are_preserved(self):
        self.assertTrue(self.run_apply(managed=False)[0])

    def test_enabled_unversioned_mod_is_not_reinstalled_on_lookup_failure(self):
        remaining, state, lookups, installs = self.run_apply(enabled=True)
        self.assertTrue(remaining)
        self.assertTrue(state['enabled'])
        self.assertEqual(installs, 0)


if __name__ == '__main__':
    unittest.main()
