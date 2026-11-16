"""Focused hook registration ownership regressions against production method ASTs.

The full AutoPickup module requires UnrealSDK/mods_base. Extract only the two
unchanged production method bodies and execute them against a fake hook API.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mod" / "AutoPickup" / "__init__.py"
CALLBACKS = (
    "_on_inventory_associated", "_on_pre_client_travel",
    "_on_notify_loaded_world", "_on_player_tick",
    "_on_fast_travel_request", "_on_loading_complete",
    "_on_auto_failed_message", "_on_auto_failed_pickup",
)
FUNCTIONS = (
    "_FN_INVENTORY", "_FN_TRAVEL", "_FN_LOADED",
    "_FN_TICK", "_FN_PICKUP_MESSAGE", "_FN_FAILED_PICKUP",
)


class Hooks:
    Type = SimpleNamespace(PRE=1, POST=2, POST_UNCONDITIONAL=3)

    def __init__(self):
        self.registered = {}
        self.add_calls = []
        self.remove_calls = []
        self.add_fault = None
        self.remove_fault = None

    def has_hook(self, *key):
        return tuple(key) in self.registered

    def add_hook(self, path, hook_type, identifier, callback):
        key = (path, hook_type, identifier)
        self.add_calls.append(key)
        if self.add_fault is not None:
            outcome = self.add_fault(key, len(self.add_calls))
            if outcome == "reject":
                return False
            if outcome == "false-with-hook":
                self.registered[key] = callback
                return False
            if outcome == "foreign-race":
                self.registered[key] = object()
                return False
            if outcome == "raise-before-add":
                raise RuntimeError("add failed before registration")
            if outcome == "add-then-raise":
                self.registered[key] = callback
                raise RuntimeError("add raised after registering")
        if key in self.registered:
            return False
        self.registered[key] = callback
        return True

    def remove_hook(self, *key):
        key = tuple(key)
        self.remove_calls.append(key)
        if self.remove_fault is not None:
            outcome = self.remove_fault(key)
            if outcome == "raise":
                raise RuntimeError("transient removal failure")
            if outcome == "false-with-hook":
                return False
            if outcome == "foreign-replaced":
                self.registered[key] = object()
                return True
        return self.registered.pop(key, None) is not None


def load_methods(hooks):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"), filename=str(SOURCE))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and any(isinstance(item, ast.FunctionDef) and item.name == "_install_hooks"
                       for item in node.body))
    selected = [node for node in cls.body
                if isinstance(node, ast.FunctionDef)
                and node.name in ("_install_hooks", "_remove_hooks")]
    assert len(selected) == 2, "AutoPickup hook methods changed"
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    log = []
    scope = {"unrealsdk": SimpleNamespace(hooks=hooks),
             "_log_exception": lambda *args: log.append(("error", args)),
             "_log": lambda message: log.append(("note", message))}
    exec(compile(module, str(SOURCE), "exec"), scope)
    return scope, log


def harness(hooks):
    ctx = SimpleNamespace(_hooks_installed=False, _installed_hooks=[],
                          _provisional_hooks={}, _uncertain_hooks=[])
    for i, field in enumerate(FUNCTIONS):
        setattr(ctx, field, f"Engine.Function{i}")
    for name in ("_HOOK_INVENTORY", "_HOOK_TRAVEL", "_HOOK_LOADED",
                 "_HOOK_TICK", "_HOOK_PICKUP_MESSAGE", "_HOOK_FAILED_PICKUP"):
        setattr(ctx, name, name)
    for cb in CALLBACKS:
        setattr(ctx, cb, lambda *args: None)
    scope, log = load_methods(hooks)
    return ctx, scope, log


class HookOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.hooks = Hooks()
        self.ctx, self.scope, self.log = harness(self.hooks)

    def install(self):
        self.scope["_install_hooks"](self.ctx)

    def remove(self):
        self.scope["_remove_hooks"](self.ctx)

    def test_normal_enable_disable_reenable(self):
        self.install()
        self.assertTrue(self.ctx._hooks_installed)
        self.assertEqual(len(self.ctx._installed_hooks), 8)
        self.assertEqual(len(self.hooks.registered), 8)
        self.remove()
        self.assertFalse(self.ctx._hooks_installed)
        self.assertFalse(self.ctx._installed_hooks)
        self.assertFalse(self.hooks.registered)
        self.install()
        self.assertEqual(len(self.hooks.registered), 8)

    def test_foreign_duplicate_not_claimed_or_removed(self):
        original = ("Engine.Function0", self.hooks.Type.POST, "_HOOK_INVENTORY")
        foreign = object()
        self.hooks.registered[original] = foreign
        with self.assertRaisesRegex(RuntimeError, "already registered"):
            self.install()
        self.remove()
        self.assertIs(self.hooks.registered[original], foreign)
        self.assertFalse(self.ctx._installed_hooks)

    def test_add_false_is_not_ledgered(self):
        self.hooks.add_fault = lambda key, count: "reject" if count == 1 else None
        with self.assertRaisesRegex(RuntimeError, "registration rejected"):
            self.install()
        self.assertFalse(self.ctx._installed_hooks)
        self.assertFalse(self.hooks.registered)

    def test_false_after_append_requires_callback_witness_for_ownership(self):
        self.hooks.add_fault = (
            lambda key, count: "false-with-hook" if count == 1 else None
        )
        self.install()
        entry = self.hooks.add_calls[0]
        self.assertIn(entry, self.ctx._provisional_hooks)
        self.assertNotIn(entry, self.ctx._installed_hooks)
        self.assertEqual(len(self.ctx._installed_hooks), 7)
        self.hooks.registered[entry](None, None, None, None)
        self.assertNotIn(entry, self.ctx._provisional_hooks)
        self.assertIn(entry, self.ctx._installed_hooks)
        self.remove()
        self.assertFalse(self.hooks.registered)

    def test_false_foreign_race_does_not_claim_or_remove_foreign(self):
        self.hooks.add_fault = (
            lambda key, count: "foreign-race" if count == 1 else None
        )
        self.install()
        entry = self.hooks.add_calls[0]
        foreign = self.hooks.registered[entry]
        self.assertIn(entry, self.ctx._provisional_hooks)
        self.assertNotIn(entry, self.ctx._installed_hooks)
        self.remove()
        self.assertIs(self.hooks.registered[entry], foreign)
        self.assertNotIn(entry, self.hooks.remove_calls)
        self.install()  # Reuse the pending ID without adopting it.
        self.assertIn(entry, self.ctx._provisional_hooks)
        self.remove()
        self.assertIs(self.hooks.registered[entry], foreign)

    def test_provisional_late_callback_after_disable_stays_inert(self):
        self.hooks.add_fault = (
            lambda key, count: "false-with-hook" if count == 1 else None
        )
        self.install()
        entry = self.hooks.add_calls[0]
        self.remove()
        self.assertIn(entry, self.ctx._provisional_hooks)
        self.hooks.registered[entry](None, None, None, None)
        self.assertIn(entry, self.ctx._provisional_hooks)
        self.assertNotIn(entry, self.ctx._installed_hooks)
        self.install()
        self.hooks.registered[entry](None, None, None, None)
        self.assertNotIn(entry, self.ctx._provisional_hooks)
        self.assertIn(entry, self.ctx._installed_hooks)
        self.remove()
        self.assertFalse(self.hooks.registered)

    def test_add_raises_before_registration_allows_clean_retry(self):
        self.hooks.add_fault = (
            lambda key, count: "raise-before-add" if count == 1 else None
        )
        with self.assertRaisesRegex(RuntimeError, "before registration"):
            self.install()
        self.assertFalse(self.ctx._installed_hooks)
        self.assertFalse(self.ctx._uncertain_hooks)
        self.assertFalse(self.hooks.registered)
        self.hooks.add_fault = None
        self.install()
        self.assertEqual(len(self.hooks.registered), 8)

    def test_partial_install_failure_removes_only_confirmed_hooks(self):
        self.hooks.add_fault = lambda key, count: "reject" if count == 2 else None
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertEqual(len(self.ctx._installed_hooks), 1)
        self.remove()
        self.assertFalse(self.hooks.registered)
        self.assertFalse(self.ctx._installed_hooks)

    def test_add_then_throw_is_quarantined_and_not_removed(self):
        self.hooks.add_fault = lambda key, count: "add-then-raise" if count == 1 else None
        with self.assertRaisesRegex(RuntimeError, "after registering"):
            self.install()
        self.assertEqual(len(self.ctx._uncertain_hooks), 1)
        self.remove()
        self.assertEqual(len(self.hooks.registered), 1)
        self.assertFalse(self.hooks.remove_calls)
        with self.assertRaisesRegex(RuntimeError, "cleanup is unresolved"):
            self.install()

    def test_remove_exception_keeps_ledger_for_retry(self):
        self.install()
        first = self.ctx._installed_hooks[0]
        self.hooks.remove_fault = lambda key: "raise" if key == first else None
        self.remove()
        self.assertEqual(self.ctx._installed_hooks, [first])
        self.assertTrue(self.hooks.has_hook(*first))
        self.hooks.remove_fault = None
        self.remove()
        self.assertFalse(self.ctx._installed_hooks)
        self.assertFalse(self.hooks.registered)

    def test_successful_remove_followed_by_foreign_id_is_quarantined(self):
        self.install()
        first = self.ctx._installed_hooks[0]
        self.hooks.remove_fault = lambda key: "foreign-replaced" if key == first else None
        self.remove()
        self.assertIn(first, self.ctx._uncertain_hooks)
        self.assertTrue(self.hooks.has_hook(*first))
        attempts = self.hooks.remove_calls.count(first)
        self.remove()
        self.assertEqual(self.hooks.remove_calls.count(first), attempts)

    def test_missing_hook_clears_record(self):
        self.install()
        first = self.ctx._installed_hooks[0]
        self.hooks.registered.pop(first)
        self.remove()
        self.assertFalse(self.ctx._installed_hooks)
        self.assertFalse(self.ctx._uncertain_hooks)

    def test_false_remove_with_live_id_retains_cleanup_record(self):
        self.install()
        first = self.ctx._installed_hooks[0]
        self.hooks.remove_fault = lambda key: "false-with-hook" if key == first else None
        self.remove()
        self.assertEqual(self.ctx._installed_hooks, [first])
        with self.assertRaisesRegex(RuntimeError, "cleanup is unresolved"):
            self.install()


if __name__ == "__main__":
    unittest.main()
