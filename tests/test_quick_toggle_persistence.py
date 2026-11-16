"""BUG-19: production AutoPickup session toggle vs Mods Base 1.12 persistence.

The real AutoPickupMod/toggle ASTs run against a Mods Base lifecycle model
matching v1.12's Mod.enable/disable/save_settings interfaces. The tests are
offline and cannot certify Willow2 menu implementations in BL2/TPS.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "mod" / "AutoPickup" / "__init__.py"


class ModsBase112:
    """Key enable/disable/save behavior mirrored from bl-sdk/mods_base v1.12."""
    def __init__(self, settings_file, on_enable=None, on_disable=None):
        self.settings_file = Path(settings_file)
        self.on_enable = on_enable
        self.on_disable = on_disable
        self.enabling_locked = False
        self.is_enabled = False
        self.auto_enable = True
        self.keybinds = []
        self.hooks = []
        self.commands = []
        self.values = {"Ammo": True, "Weapons": False}
        self.saved = 0
        self.fail_saving = False

    def enable(self):
        if self.enabling_locked or self.is_enabled:
            return
        self.is_enabled = True
        for bind in self.keybinds: bind.enable()
        for hook in self.hooks: hook.enable()
        for command in self.commands: command.enable()
        if self.on_enable is not None: self.on_enable()
        if self.auto_enable: self.save_settings()

    def disable(self, dont_update_setting=False):
        if self.enabling_locked or not self.is_enabled:
            return
        self.is_enabled = False
        for bind in self.keybinds: bind.disable()
        for hook in self.hooks: hook.disable()
        for command in self.commands: command.disable()
        if self.on_disable is not None: self.on_disable()
        if self.auto_enable and not dont_update_setting: self.save_settings()

    def save_settings(self):
        if self.fail_saving:
            raise OSError("fake disk full")
        snapshot = {"enabled": self.is_enabled, "options": dict(self.values)}
        self.settings_file.write_text(json.dumps(snapshot), encoding="utf-8")
        self.saved += 1


class Clock:
    def __init__(self):
        self.now = 100

    def monotonic(self):
        return float(self.now)

    def advance(self, seconds=1):
        self.now += seconds


def production():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "AutoPickupMod")
    names = {"enable", "disable", "toggle_for_session", "save_settings"}
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef)
               and node.name in names]
    assert len(methods) == len(names), "AutoPickupMod API changed"
    derived = ast.ClassDef(name="AutoPickupModTest",
                           bases=[ast.Name(id="Mod", ctx=ast.Load())],
                           keywords=[], body=methods, decorator_list=[])
    toggle = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_toggle_auto_pickup")
    fake_scope = {"Mod": ModsBase112, "json": json}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[derived, toggle],
                   type_ignores=[])), str(SOURCE), "exec"), fake_scope)
    return fake_scope


class SessionToggleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "AutoPickup.json"
        self.events = []
        self.clock = Clock()
        self.scope = production()
        self.scope.update({
            "_LAST_MASTER_TOGGLE_TIME": float("-inf"),
            "_MASTER_TOGGLE_DEBOUNCE_SECONDS": .35,
            "time": NS(monotonic=self.clock.monotonic),
            "_ensure_toggle_keybind_available": lambda: self.events.append("keybind"),
            "_sync_open_enabled_row": lambda: self.events.append("menu-row"),
            "_log": lambda message: self.events.append(("log",message)),
            "_log_exception": lambda label, exc: self.events.append(
                ("error",label,type(exc).__name__)),
            "get_pc": lambda **kwargs: None,
        })
        self.fail_activate = False
        def on_enable():
            self.events.append("enable-hook")
            if self.fail_activate:
                raise RuntimeError("hook registration refused")
        def on_disable():
            self.events.append("disable-hook")
        self.mod = self.scope["AutoPickupModTest"](
            self.path, on_enable=on_enable, on_disable=on_disable)
        self.scope["_MOD"] = self.mod
        self.scope["_RUNTIME"] = NS(
            _traveling=False,
            _get_inventory_pawn=lambda pc: None)
        self.toggle = self.scope["_toggle_auto_pickup"]

    def settings(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def persist_off(self):
        self.mod.save_settings()
        self.assertEqual(self.settings()["enabled"], False)

    def persist_on(self):
        self.mod.enable()
        self.assertEqual(self.settings()["enabled"], True)

    def quick(self):
        self.clock.advance(1)
        self.toggle()

    def test_persist_off_quick_on_off_restart_stays_disabled(self):
        self.persist_off()
        before = self.path.read_bytes()
        self.quick()
        self.assertTrue(self.mod.is_enabled)
        self.assertEqual(self.path.read_bytes(),before)
        self.quick()
        self.assertFalse(self.mod.is_enabled)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.mod.saved, 1)
        self.assertTrue(self.mod.auto_enable)
        self.assertEqual(self.events.count("menu-row"),2)
        self.assertEqual(self.settings()["enabled"],False)

    def test_persist_on_quick_off_on_restart_stays_enabled(self):
        self.persist_on()
        before=self.path.read_bytes()
        self.quick()
        self.assertFalse(self.mod.is_enabled)
        self.assertEqual(self.path.read_bytes(),before)
        self.quick()
        self.assertTrue(self.mod.is_enabled)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.mod.saved,1)
        self.assertEqual(self.settings()["enabled"],True)

    def test_enabled_row_noop_enable_does_not_clear_session_preference(self):
        self.persist_off()
        self.quick()
        self.assertTrue(self.mod.is_enabled)
        # The Willow2 menu Enabled-row setter triggers enable() after state
        # was already changed programmatically by a controller/keyboard bind.
        self.mod.enable()
        self.assertIs(self.mod._session_enabled_preference, False)
        self.mod.values["Weapons"]=True
        self.mod.save_settings()
        self.assertTrue(self.mod.is_enabled)
        self.assertEqual(self.settings()["enabled"],False)
        self.assertTrue(self.settings()["options"]["Weapons"])

    def test_enabled_row_noop_disable_does_not_clear_session_preference(self):
        self.persist_on()
        self.quick()
        self.assertFalse(self.mod.is_enabled)
        self.mod.disable()
        self.assertIs(self.mod._session_enabled_preference, True)
        self.mod.values["Ammo"]=False
        self.mod.save_settings()
        self.assertFalse(self.mod.is_enabled)
        self.assertEqual(self.settings()["enabled"],True)
        self.assertFalse(self.settings()["options"]["Ammo"])

    def test_saving_category_changes_after_quick_on_preserves_off(self):
        self.persist_off()
        self.quick()
        self.assertTrue(self.mod.is_enabled)
        self.mod.values["Ammo"]=False
        self.mod.values["Weapons"]=True
        self.mod.save_settings()
        self.assertEqual(self.settings(),{
            "enabled":False,"options":{"Ammo":False,"Weapons":True}})
        self.assertTrue(self.mod.is_enabled)
        self.assertTrue(self.mod.auto_enable)

    def test_saving_category_changes_after_quick_off_preserves_on(self):
        self.persist_on()
        self.quick()
        self.assertFalse(self.mod.is_enabled)
        self.mod.values["Ammo"]=False
        self.mod.save_settings()
        self.assertEqual(self.settings()["enabled"],True)
        self.assertFalse(self.settings()["options"]["Ammo"])
        self.assertFalse(self.mod.is_enabled)

    def test_manual_menu_disable_enable_persists_new_preference(self):
        self.persist_off()
        self.quick()
        self.mod.disable()
        self.assertFalse(self.mod.is_enabled)
        self.assertFalse(self.settings()["enabled"])
        self.assertIsNone(self.mod._session_enabled_preference)
        self.mod.enable()
        self.assertTrue(self.settings()["enabled"])
        self.assertIsNone(self.mod._session_enabled_preference)
        self.quick()
        self.assertFalse(self.mod.is_enabled)
        self.assertTrue(self.settings()["enabled"])

    def test_manual_menu_disabled_after_persisted_on_changes_preference(self):
        self.persist_on()
        self.quick()
        self.assertFalse(self.mod.is_enabled)
        self.quick()
        self.assertTrue(self.mod.is_enabled)
        self.mod.disable()
        self.assertEqual(self.settings()["enabled"],False)
        self.assertIsNone(self.mod._session_enabled_preference)
        self.quick()
        self.assertTrue(self.mod.is_enabled)
        self.assertEqual(self.settings()["enabled"],False)

    def test_controller_and_keyboard_same_callback_effect(self):
        self.persist_off()
        self.quick()  # One supported keyboard/controller KeybindType callback.
        self.assertTrue(self.mod.is_enabled)
        self.quick()  # ControllerManager invokes this same public action.
        self.assertFalse(self.mod.is_enabled)
        self.assertEqual(self.mod.saved,1)
        self.assertEqual(self.events.count("enable-hook"),1)
        self.assertEqual(self.events.count("disable-hook"),1)

    def test_travel_prevents_quick_disable(self):
        self.persist_on()
        self.scope["_RUNTIME"]._traveling=True
        self.quick()
        self.assertTrue(self.mod.is_enabled)
        self.assertEqual(self.mod.saved,1)
        self.assertTrue(self.settings()["enabled"])

    def test_debounce_keeps_session_and_saved_state_unchanged(self):
        self.persist_off()
        self.quick()
        self.clock.advance(.1)
        self.toggle()
        self.assertTrue(self.mod.is_enabled)
        self.assertEqual(self.mod.saved,1)
        self.assertEqual(self.events.count("enable-hook"),1)

    def test_failed_quick_enable_keeps_enabled_preference_off(self):
        self.persist_off()
        self.fail_activate=True
        self.quick()
        # Production AutoPickupMod.enable handles runtime hook failure by
        # calling disable(dont_update_setting=True).
        self.assertFalse(self.mod.is_enabled)
        self.assertEqual(self.settings()["enabled"],False)
        self.assertTrue(any(e[0]=="error" for e in self.events if isinstance(e,tuple)))
        self.fail_activate=False
        self.quick()
        self.assertTrue(self.mod.is_enabled)
        self.assertEqual(self.settings()["enabled"],False)

    def test_saved_on_with_failed_startup_keeps_saved_on_after_quick(self):
        self.path.write_text(json.dumps({
            "enabled":True,"options":{"Ammo":True,"Weapons":False}}),
            encoding="utf-8")
        self.assertFalse(self.mod.is_enabled)  # Simulates failed startup.
        self.quick()
        self.assertTrue(self.mod.is_enabled)
        self.mod.values["Weapons"]=True
        self.mod.save_settings()
        self.assertEqual(self.settings()["enabled"],True)
        self.assertTrue(self.settings()["options"]["Weapons"])

    def test_save_error_restores_live_enabled_field(self):
        self.persist_off()
        self.quick()
        self.mod.fail_saving=True
        with self.assertRaises(OSError):
            self.mod.save_settings()
        self.assertTrue(self.mod.is_enabled)
        self.assertFalse(self.mod._session_enabled_preference)

    def test_missing_settings_file_uses_live_start_state(self):
        self.assertFalse(self.path.exists())
        self.quick()
        self.assertTrue(self.mod.is_enabled)
        self.assertFalse(self.path.exists())
        self.mod.values["Ammo"]=False
        self.mod.save_settings()
        self.assertEqual(self.settings()["enabled"],False)

    def test_normal_sdk_enable_disable_still_persist_without_quick_toggle(self):
        self.mod.enable()
        self.assertEqual(self.settings()["enabled"],True)
        self.mod.disable()
        self.assertEqual(self.settings()["enabled"],False)
        self.assertIsNone(getattr(self.mod,"_session_enabled_preference",None))


if __name__=="__main__":
    unittest.main()
