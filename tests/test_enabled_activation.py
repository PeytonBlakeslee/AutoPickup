"""Verify AutoPickup's enable transaction using production callback method ASTs.

mods_base.Mod enables keybinds/state before on_enable, then saves settings.
The stub models that contract; no game/runtime acceptance is implied.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import time
import unittest

SOURCE=Path(__file__).resolve().parents[1]/"mod"/"AutoPickup"/"__init__.py"


class SDKModStub:
    def __init__(self,on_enable,on_disable):
        self.on_enable=on_enable
        self.on_disable=on_disable
        self.enabling_locked=False
        self.is_enabled=False
        self.auto_enable=True
        self.saved=[]
        self.keybinds=[]
        self.hooks=[]
        self.commands=[]

    def save_settings(self):
        self.saved.append(self.is_enabled)

    def enable(self):
        if self.enabling_locked or self.is_enabled:return
        self.is_enabled=True
        for bind in self.keybinds:bind.enable()
        for hook in self.hooks:hook.enable()
        for command in self.commands:command.enable()
        if self.on_enable is not None:self.on_enable()
        if self.auto_enable:self.save_settings()

    def disable(self,dont_update_setting=False):
        if self.enabling_locked or not self.is_enabled:return
        self.is_enabled=False
        for bind in self.keybinds:bind.disable()
        for hook in self.hooks:hook.disable()
        for command in self.commands:command.disable()
        if self.on_disable is not None:self.on_disable()
        if self.auto_enable and not dont_update_setting:self.save_settings()


def load_production():
    tree=ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    selected=[]
    for cls in (n for n in tree.body if isinstance(n,ast.ClassDef)):
        if cls.name=="AutoPickupRuntime":
            methods=[n for n in cls.body if isinstance(n,ast.FunctionDef)
                     and n.name in ("enable","disable")]
            assert len(methods)==2
            selected.append(ast.ClassDef(name="AutoPickupRuntimeTest",bases=[],
                keywords=[],body=methods,decorator_list=[]))
        elif cls.name=="AutoPickupMod":
            methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="enable"]
            assert len(methods)==1
            selected.append(ast.ClassDef(name="AutoPickupModTest",
                bases=[ast.Name(id="Mod",ctx=ast.Load())],keywords=[],
                body=methods,decorator_list=[]))
    assert len(selected)==2
    events=[]
    scope={
        "Mod":SDKModStub,
        "time":time,
        "__version__":"test-version",
        "_log":lambda message:events.append(("log",message)),
        "_log_exception":lambda label,error:events.append(("error",label,type(error).__name__)),
        "_ensure_toggle_keybind_available":lambda:events.append(("keybind-restored",)),
        "MinimumGearRarity":SimpleNamespace(value="test"),
        "PickupSelfDroppedGear":SimpleNamespace(value=False),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected,type_ignores=[])),
                 str(SOURCE),"exec"),scope)
    return scope["AutoPickupRuntimeTest"],scope["AutoPickupModTest"],events


def harness(*, install_fails=False, cleanup_fails=False):
    Runtime,Mod,events=load_production()
    runtime=Runtime()
    runtime._traveling=False
    runtime._final_world_loaded=False
    runtime._travel_resume_after=0.
    runtime._options_dirty=False
    runtime._runtime_enabled=False
    runtime._seed_requested=False
    runtime._seed_reason=""
    runtime._seed_not_before=0.
    runtime._pickup_frequency_hz=10
    runtime._radius_percent=100
    runtime._service_interval=.1
    runtime._clear_runtime_state=lambda **kw:events.append(("clear",))
    runtime.sync_settings_from_options=lambda:events.append(("settings",))
    runtime._request_seed=lambda *a,**kw:events.append(("seed",))
    runtime._on_off=lambda value:"on" if value else "off"
    def install():
        events.append(("install",))
        if install_fails:raise RuntimeError("SDK hook ID collision")
    def remove():
        events.append(("remove",))
        if cleanup_fails:raise RuntimeError("cleanup failed")
    runtime._install_hooks=install
    runtime._remove_hooks=remove
    mod=Mod(on_enable=runtime.enable,on_disable=runtime.disable)
    return runtime,mod,events


class EnabledActivationTests(unittest.TestCase):
    def test_success_sets_runtime_and_sdk_state_and_saves_true(self):
        runtime,mod,events=harness()
        mod.enable()
        self.assertTrue(runtime._runtime_enabled)
        self.assertTrue(mod.is_enabled)
        self.assertEqual(mod.saved,[True])
        self.assertEqual(events.count(("install",)),1)

    def test_failed_startup_enable_rolls_back_mod_enabled_without_persisting(self):
        runtime,mod,events=harness(install_fails=True)
        # No _MOD global is needed because constructor/startup enables can
        # happen before build_mod returns its object.
        mod.enable()
        self.assertFalse(runtime._runtime_enabled)
        self.assertFalse(mod.is_enabled)
        self.assertEqual(mod.saved,[])
        self.assertEqual(events.count(("install",)),1)
        self.assertEqual(events.count(("remove",)),2)
        self.assertTrue(any(e[0]=="error" for e in events))

    def test_failure_preserves_previous_enabled_preference(self):
        runtime,mod,events=harness(install_fails=True)
        mod.saved=[True]
        mod.enable()
        self.assertEqual(mod.saved,[True])
        self.assertFalse(mod.is_enabled)

    def test_next_explicit_enable_after_recoverable_failure(self):
        runtime,mod,events=harness(install_fails=True)
        mod.enable()
        runtime._install_hooks=lambda:events.append(("install-recovered",))
        mod.enable()
        self.assertTrue(runtime._runtime_enabled)
        self.assertTrue(mod.is_enabled)
        self.assertEqual(mod.saved,[True])

    def test_sdk_disabled_when_cleanup_raises(self):
        runtime,mod,events=harness(install_fails=True,cleanup_fails=True)
        mod.enable()
        self.assertFalse(mod.is_enabled)
        self.assertFalse(runtime._runtime_enabled)
        self.assertEqual(mod.saved,[])
        self.assertTrue(any(e[0]=="error" for e in events))

    def test_second_successful_enable_is_noop(self):
        runtime,mod,events=harness()
        mod.enable();mod.enable()
        self.assertEqual(events.count(("install",)),1)
        self.assertEqual(mod.saved,[True])


if __name__=="__main__":
    unittest.main()
