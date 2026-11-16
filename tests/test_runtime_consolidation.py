"""Offline tests for source-only Auto Pickup module consolidation."""
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path

MOD_ROOT = Path(__file__).resolve().parents[1] / 'mod' / 'AutoPickup'

def load(module):
    spec = importlib.util.spec_from_file_location('test_ap_' + module, MOD_ROOT / (module + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

class MigrationAndAuthorityTests(unittest.TestCase):
    def test_numbered_filename_migration_preserves_old_bytes(self):
        m = load('settings_migration')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / '5AutoPickup.json'
            old.write_bytes(b'{"enabled":true,"options":{"Ammo":true}}')
            m.migrate_settings(root)
            self.assertEqual((root / 'AutoPickup.json').read_bytes(), old.read_bytes())
            self.assertTrue(old.is_file())
            self.assertEqual(list(root.glob('*.tmp')), [])
            m.migrate_settings(root)
            self.assertEqual((root / 'AutoPickup.json').read_bytes(), old.read_bytes())

    def test_existing_new_settings_not_overwritten(self):
        m = load('settings_migration')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '5AutoPickup.json').write_text('old')
            (root / 'AutoPickup.json').write_text('new')
            m.migrate_settings(root)
            self.assertEqual((root / 'AutoPickup.json').read_text(), 'new')

    def test_old_schema_migration_and_cleanup(self):
        m = load('settings_migration')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / 'settings.json'
            modern = root / 'AutoPickup.json'
            legacy.write_text(json.dumps({'AutoEnable':True, 'Options': {'Ammo':True, 'NotPresent': 55}}))
            errs=[]
            m.migrate_legacy_settings(modern, legacy, {'Ammo'}, lambda _m:None, lambda *a:errs.append(a))
            self.assertEqual(json.loads(modern.read_text()), {'enabled':True, 'options':{'Ammo':True}})
            self.assertEqual(errs, [])
            settings=json.loads(modern.read_text());settings['options'].update({'Mission Items':1,'Debug Logging':True,'Other mod setting':4})
            modern.write_text(json.dumps(settings))
            m.cleanup_obsolete_settings(modern, {'Mission Items', 'Debug Logging'}, lambda _m:None, lambda *a:errs.append(a))
            self.assertEqual(json.loads(modern.read_text())['options'], {'Ammo':True,'Other mod setting':4})
            self.assertFalse(errs)

    def test_client_and_authority_modes(self):
        g = load('capacity_guard')
        for value, name, outcome in [(3,'NM_Client','client'),(0,'NM_Standalone','authority'),
                                      (1,'NM_DedicatedServer','authority'),(2,'NM_ListenServer','authority'),
                                      (8,None,None),(None,None,None)]:
            mode = value if name is None else types.SimpleNamespace(name=name, __int__=lambda self:value)
            owner = types.SimpleNamespace(WorldInfo=types.SimpleNamespace(NetMode=mode))
            self.assertEqual(g.pickup_network_mode(owner), outcome)
        self.assertIsNone(g.pickup_network_mode(types.SimpleNamespace()))
        self.assertIsNone(g.pickup_network_mode(types.SimpleNamespace(WorldInfo=None)))

    def test_capacity_unknown_does_not_claim_full(self):
        g=load('capacity_guard')
        self.assertIsNone(g.native_has_room(types.SimpleNamespace(), types.SimpleNamespace()))
        self.assertIsNone(g.empty_backpack_slots(types.SimpleNamespace()))

if __name__=='__main__':unittest.main()
