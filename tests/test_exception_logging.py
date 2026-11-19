"""Source-derived error reporting regressions, without importing UnrealSDK.

The live runtime is loaded from its AST to exercise the exact production helper.
"""
from __future__ import annotations

import ast
from pathlib import Path
import traceback
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'mod' / 'AutoPickup' / '__init__.py'


def load_error_helper():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'), filename=str(SOURCE))
    func = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == '_log_exception'
    )
    emitted = []
    scope = {'logging': SimpleNamespace(error=emitted.append), 'traceback': traceback}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[])),
                 str(SOURCE), 'exec'), scope)
    return scope['_log_exception'], emitted


class ErrorLoggingTests(unittest.TestCase):
    def test_captured_error_outside_active_except_retains_traceback(self):
        helper, errors = load_error_helper()
        try:
            raise RuntimeError('broken pickup getter')
        except RuntimeError as exc:
            captured = exc
        helper('seeding world', captured)
        self.assertEqual(len(errors), 1)
        self.assertIn('RuntimeError: broken pickup getter', errors[0])
        self.assertIn('test_captured_error_outside_active_except', errors[0])
        self.assertNotIn('NoneType: None', errors[0])

    def test_error_while_handling_exception_keeps_traceback(self):
        helper, errors = load_error_helper()
        try:
            raise ValueError('bad native resource')
        except ValueError as exc:
            helper('checking pickup', exc)
        self.assertEqual(len(errors), 1)
        self.assertIn('ValueError: bad native resource', errors[0])
        self.assertIn('test_error_while_handling_exception', errors[0])

    def test_seed_actor_error_reported_after_loop_keeps_original_traceback(self):
        helper, errors = load_error_helper()
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        cls = next(
            n for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == 'AutoPickupRuntime'
        )
        method = next(
            n for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name == '_seed_current_world'
        )
        cls_stub = ast.ClassDef(
            name='SeedRuntime', bases=[], keywords=[],
            body=[method], decorator_list=[]
        )
        current_world = SimpleNamespace(_get_address=lambda: 101)
        good_a = SimpleNamespace(WorldInfo=current_world, name='A')
        bad = SimpleNamespace(WorldInfo=current_world, name='B')
        good_c = SimpleNamespace(WorldInfo=current_world, name='C')
        visited = []
        scope = {
            'unrealsdk': SimpleNamespace(find_all=lambda *args, **kw: (good_a, bad, good_c)),
            '_log_exception': helper,
        }
        exec(compile(
            ast.fix_missing_locations(ast.Module(body=[cls_stub], type_ignores=[])),
            str(SOURCE), 'exec'
        ), scope)
        runtime = scope['SeedRuntime']()
        runtime._traveling = False
        runtime._runtime_enabled = True
        runtime._object_address = (
            lambda thing: thing._get_address() if thing is not None else 0
        )
        runtime._is_deleted = lambda thing: False

        def register(thing):
            if thing.name == 'B':
                raise RuntimeError('B reflected getter failed')
            visited.append(thing.name)
            return True
        runtime._register_pickup = register
        counts = runtime._seed_current_world(SimpleNamespace(WorldInfo=current_world))
        self.assertEqual(counts, (2, 3))
        self.assertEqual(visited, ['A', 'C'])
        self.assertEqual(len(errors), 1)
        self.assertIn('RuntimeError: B reflected getter failed', errors[0])
        self.assertIn('register', errors[0])
        self.assertNotIn('NoneType: None', errors[0])


if __name__ == '__main__':
    unittest.main()
