"""REF-11: tests for the real AutoPickup option declarations and reset callback.

Runs without UnrealSDK, the game, or player settings files. Extracts only the
production AST nodes needed for the test so no mod import-time hooks are run.
"""
from __future__ import annotations

import ast
import copy
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'mod' / 'AutoPickup' / '__init__.py'

OPTION_NAMES = (
    'PickupFrequency', 'PickupRadiusPercent', 'PickupAmmo', 'PickupCurrency',
    'PickupTorgueTokens', 'PickupSeraphCrystals', 'PickupOtherConsumables',
    'PickupWeapons', 'PickupShields', 'PickupGrenadeMods', 'PickupClassMods',
    'PickupRelics', 'MinimumGearRarity', 'PickupSelfDroppedGear',
)
EXPECTED_DEFAULTS = {
    'PickupFrequency': 5,
    'PickupRadiusPercent': 100,
    'PickupAmmo': True, 'PickupCurrency': True,
    'PickupTorgueTokens': True, 'PickupSeraphCrystals': True,
    'PickupOtherConsumables': True,
    'PickupWeapons': False, 'PickupShields': False,
    'PickupGrenadeMods': False, 'PickupClassMods': False, 'PickupRelics': False,
    'MinimumGearRarity': 'Any', 'PickupSelfDroppedGear': False,
}


class Option:
    def __init__(self, *args, **kwargs):
        self.identifier = str(args[0]) if args else kwargs.get('identifier', '')
        self.value = args[1] if len(args) > 1 else kwargs.get('value')
        self.kwargs = kwargs


class Clock:
    def __init__(self, value=100.0): self.value = value
    def monotonic(self): return self.value


def load_source():
    return ast.parse(SOURCE.read_text(encoding='utf-8-sig'), filename=str(SOURCE))


def load_options(*, is_tps=False):
    tree = load_source()
    wanted = set(OPTION_NAMES) | {'ResetDefaults', '_OPTIONS'}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in wanted or name.startswith(('_DEFAULT_', '_MIN_', '_MAX_', '_OPT_')):
                selected.append(node)
    declared = {node.targets[0].id for node in selected}
    if not wanted.issubset(declared):
        raise AssertionError(f'Runtime option declarations missing: {wanted - declared}')
    # Constructor-only harness: the source declarations reference live option
    # callbacks, but option callbacks are deliberately not imported/executed.
    # Stubs preserve the declarative callback wiring without side effects.
    callback_names = ('_frequency_changed', '_radius_changed', '_category_changed',
                      '_rarity_changed', '_self_drop_changed')
    actual_callbacks = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    if not set(callback_names).issubset(actual_callbacks):
        raise AssertionError('Source option callback definitions missing')
    env = {'BoolOption': Option, 'SliderOption': Option, 'SpinnerOption': Option,
           'ButtonOption': Option, 'IS_TPS': is_tps, '_reset_pressed': lambda *_: None}
    env.update({name: (lambda *_: None) for name in callback_names})
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), env)
    return env


def reset_harness(*, is_tps=False):
    env = load_options(is_tps=is_tps)
    tree = load_source()
    callbacks = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_reset_pressed']
    if len(callbacks) != 1:
        raise AssertionError('Expected one production _reset_pressed callback')
    clock = Clock()
    actions = []
    enabled = SimpleNamespace(is_enabled=False, save_settings=Mock(side_effect=lambda: actions.append('save')))
    runtime = SimpleNamespace(sync_settings_from_options=Mock(side_effect=lambda: actions.append('sync')),
                              on_reset_defaults=Mock(side_effect=lambda: actions.append('notify')))
    env.update({'time': clock, '_LAST_RESET_PRESS_TIME': -math.inf,
                '_RESET_DEBOUNCE_SECONDS': 0.5,
                '_RUNTIME': runtime, '_MOD': enabled,
                '_refresh_open_options_page': Mock(return_value=True),
                '_log': Mock(), '_log_exception': Mock()})
    exec(compile(ast.Module(body=callbacks, type_ignores=[]), str(SOURCE), 'exec'), env)
    return SimpleNamespace(env=env, clock=clock, runtime=runtime, mod=enabled, actions=actions,
                           options={key: env[key] for key in OPTION_NAMES})


class AutoPickupResetContractTests(unittest.TestCase):
    def test_constructor_defaults_bl2(self):
        env = load_options()
        self.assertEqual({key: env[key].value for key in OPTION_NAMES}, EXPECTED_DEFAULTS)
        self.assertEqual([item for item in env['_OPTIONS'] if item is env['ResetDefaults']], [env['ResetDefaults']])
        self.assertEqual(len(env['_OPTIONS']), len(OPTION_NAMES)+1)

    def test_constructor_defaults_tps_match_and_labels_differ(self):
        bl2, tps = load_options(), load_options(is_tps=True)
        self.assertEqual({key: tps[key].value for key in OPTION_NAMES}, EXPECTED_DEFAULTS)
        self.assertNotEqual(bl2['PickupCurrency'].kwargs['display_name'], tps['PickupCurrency'].kwargs['display_name'])
        self.assertTrue(tps['PickupTorgueTokens'].kwargs['is_hidden'])
        self.assertTrue(tps['PickupSeraphCrystals'].kwargs['is_hidden'])

    def test_reset_matches_fresh_defaults_and_saves_once(self):
        h=reset_harness()
        for key,opt in h.options.items():
            opt.value = 99 if key in ('PickupFrequency','PickupRadiusPercent') else (
                'Legendary+' if key == 'MinimumGearRarity' else not EXPECTED_DEFAULTS[key])
        h.env['_reset_pressed'](None)
        self.assertEqual({k:v.value for k,v in h.options.items()}, EXPECTED_DEFAULTS)
        self.assertEqual(h.actions, ['sync', 'notify', 'save'])
        h.env['_refresh_open_options_page'].assert_called_once_with()
        self.assertFalse(h.mod.is_enabled)

    def test_reset_does_not_replace_enabled_preference(self):
        h=reset_harness()
        h.mod.is_enabled = True
        h.mod._session_enabled_preference = False
        h.env['_reset_pressed'](None)
        self.assertTrue(h.mod.is_enabled)
        self.assertFalse(h.mod._session_enabled_preference)
        self.assertEqual(h.mod.save_settings.call_count,1)

    def test_toggle_keybind_stays_unbound_and_outside_reset(self):
        tree = load_source()
        bindings = [node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'ToggleAutoPickup' for t in node.targets)]
        self.assertEqual(len(bindings), 1)
        call = bindings[0].value
        self.assertIsInstance(call, ast.Call)
        self.assertEqual(ast.literal_eval(call.args[0]), 'Toggle Auto Pickup')
        self.assertIsNone(ast.literal_eval(call.args[1]))
        reset_fn = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == '_reset_pressed')
        self.assertNotIn('ToggleAutoPickup', ast.unparse(reset_fn))

    def test_debounce_and_exact_boundary(self):
        h=reset_harness()
        h.env['_reset_pressed'](None)
        h.clock.value=100.499
        h.options['PickupAmmo'].value=False
        h.env['_reset_pressed'](None)
        self.assertFalse(h.options['PickupAmmo'].value)
        self.assertEqual(h.mod.save_settings.call_count,1)
        h.clock.value=100.5
        h.env['_reset_pressed'](None)
        self.assertTrue(h.options['PickupAmmo'].value)
        self.assertEqual(h.mod.save_settings.call_count,2)

    def test_disabled_runtime_does_not_prevent_reset_or_persistence(self):
        h=reset_harness()
        h.env['_RUNTIME'] = None
        h.options['PickupWeapons'].value=True
        h.env['_reset_pressed'](None)
        self.assertFalse(h.options['PickupWeapons'].value)
        self.assertEqual(h.actions,['save'])

    def test_absent_mod_and_legacy_menu_refresh_are_nonfatal(self):
        h=reset_harness()
        h.env['_MOD'] = None
        h.env['_refresh_open_options_page'] = Mock(return_value=False)
        h.options['PickupShields'].value=True
        h.env['_reset_pressed'](None)
        self.assertFalse(h.options['PickupShields'].value)
        self.assertEqual(h.actions,['sync','notify'])
        h.env['_refresh_open_options_page'].assert_called_once()

    def test_runtime_sync_rebases_frequency_radius_and_one_seed(self):
        h=reset_harness()
        tree=load_source()
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AutoPickupRuntime')
        names={'sync_settings_from_options','_apply_frequency','_apply_radius_percent','on_reset_defaults'}
        methods=[node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in methods},names)
        scope={**h.env,'time':h.clock}
        module=ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name='ResetOnlyRuntime',bases=[],keywords=[],body=methods,
            decorator_list=[])],type_ignores=[]))
        exec(compile(module,str(SOURCE),'exec'),scope)
        runtime=scope['ResetOnlyRuntime']()
        runtime._runtime_enabled=True
        runtime._options_dirty=False
        runtime._request_seed=Mock()
        runtime._next_service_time=-1
        runtime._apply_frequency(20)
        runtime._apply_radius_percent(500)
        h.env['_RUNTIME']=runtime
        h.env['_reset_pressed'](None)
        self.assertEqual(runtime._pickup_frequency_hz,5)
        self.assertAlmostEqual(runtime._service_interval,0.2)
        self.assertEqual(runtime._radius_percent,100)
        self.assertEqual(runtime._radius_multiplier,1.0)
        self.assertTrue(runtime._options_dirty)
        runtime._request_seed.assert_called_once_with('reset to defaults',delay=0.10)
        self.assertEqual(runtime._next_service_time,100.0)

    def test_save_failure_logs_without_undoing_defaults(self):
        h=reset_harness()
        h.options['PickupWeapons'].value=True
        h.mod.save_settings.side_effect=OSError('injected disk failure')
        h.env['_reset_pressed'](None)
        self.assertFalse(h.options['PickupWeapons'].value)
        h.env['_log_exception'].assert_called_once()
        h.env['_refresh_open_options_page'].assert_called_once()


if __name__ == '__main__': unittest.main()
