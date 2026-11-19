"""Offline service-loop regressions using production method ASTs.

These exercise AutoPickup's actual service logic with fake game/SDK boundaries,
not native-engine or multiplayer gameplay. No mod import is required.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'mod' / 'AutoPickup' / '__init__.py'
METHODS = {'_service_candidates', '_forget_candidate_if_current', '_forget_candidate'}


class Pickup:
    def __init__(self, address, x=0):
        self.address = address
        self.Location = NS(X=x, Y=0, Z=0)
        self.enabled = True
        self.deleted = False

    def Pickupable_IsEnabled(self):
        return self.enabled


class Controller:
    def __init__(self, mode='authority'):
        self.mode = mode
        self.Pawn = NS(Location=NS(X=0, Y=0, Z=0))
        self.authority_calls = []
        self.client_calls = []
        self.on_native_pickup = None

    def PickupPickupable(self, pickup, manual):
        self.authority_calls.append((pickup.address, manual))
        if self.on_native_pickup is not None:
            self.on_native_pickup(pickup)

    def ServerPickupSpecific(self, pickup):
        self.client_calls.append(pickup.address)
        if self.on_native_pickup is not None:
            self.on_native_pickup(pickup)


def harness(mode='authority', *, native_rejection=False, limit=8, is_tps=False):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'), filename=str(SOURCE))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == 'AutoPickupRuntime')
    selected = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                and n.name in METHODS]
    assert len(selected) == len(METHODS)
    stub = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name='Runtime', bases=[], keywords=[], body=selected, decorator_list=[]
    )], type_ignores=[]))
    errors = []
    guard = NS(
        empty_backpack_slots=lambda pc: 1,
        reject_for_capacity=lambda *a, **kw: (native_rejection, 'native-inventory'),
    )
    scope = {
        'capacity_guard': guard,
        'pickup_network_mode': lambda pc: pc.mode,
        'PickupSelfDroppedGear': NS(value=False),
        'IS_TPS': is_tps,
        '_log_exception': lambda context, exc: errors.append((context, str(exc))),
    }
    exec(compile(stub, str(SOURCE), 'exec'), scope)
    runtime = scope['Runtime']()
    runtime._candidates = {}
    runtime._GEAR_CATEGORIES = frozenset({'Weapon', 'Shield', 'Grenade Mod', 'Class Mod', 'Relic'})
    runtime._get_inventory_pawn = lambda pc: pc.Pawn
    runtime._get_effective_pickup_range = lambda pc: 300.0
    runtime._is_deleted = lambda pickup: pickup.deleted
    runtime._is_player_dropped_pickup = lambda pickup: False
    runtime._is_mission_director = lambda pickup: False
    runtime._is_mission_item = lambda pickup: False
    runtime._category_enabled = lambda category: True
    runtime._rarity_allows = lambda category, rarity: True
    runtime._object_address = lambda obj: obj.address
    runtime._client_window_started = 0.
    runtime._client_requests_sent = 0
    runtime._MAX_CLIENT_REQUESTS_PER_SECOND = limit
    runtime._FAILED_RETRY_SECONDS = 1.0
    runtime._auto_attempt_controller = 0
    runtime._auto_attempt_pickup = 0
    pc = Controller(mode)
    pc.address = 42

    def add(pickup, *, category='Ammo', retry_at=0):
        runtime._candidates[pickup.address] = (lambda: pickup, category, retry_at, 0)
    return runtime, pc, add, errors


class CandidateServiceTests(unittest.TestCase):
    def test_authority_uses_local_native_pickup_and_sets_retry(self):
        rt, pc, add, _ = harness()
        add(Pickup(100))
        rt._service_candidates(10., pc)
        self.assertEqual(pc.authority_calls, [(100, False)])
        self.assertEqual(pc.client_calls, [])
        self.assertEqual(rt._candidates[100][2], 11.)
        self.assertEqual((rt._auto_attempt_controller, rt._auto_attempt_pickup), (0, 0))

    def test_client_calls_server_only_and_enforces_one_second_budget(self):
        rt, pc, add, _ = harness('client')
        for address in range(100, 111):
            add(Pickup(address))
        rt._service_candidates(10., pc)
        self.assertEqual(pc.client_calls, list(range(100, 108)))
        self.assertEqual(rt._client_requests_sent, 8)
        self.assertEqual(pc.authority_calls, [])
        rt._service_candidates(10.5, pc)
        self.assertEqual(len(pc.client_calls), 8)
        rt._service_candidates(11.1, pc)
        self.assertEqual(pc.client_calls[8:11], [108, 109, 110])
        self.assertEqual(len(pc.client_calls), 16)
        self.assertEqual(rt._client_requests_sent, 8)
        rt._service_candidates(11.5, pc)
        self.assertEqual(len(pc.client_calls), 16)

    def test_unknown_network_mode_blocks_native_writes(self):
        rt, pc, add, _ = harness(None)
        add(Pickup(100))
        rt._service_candidates(10., pc)
        self.assertEqual(pc.client_calls, [])
        self.assertEqual(pc.authority_calls, [])
        self.assertIn(100, rt._candidates)

    def test_full_inventory_does_not_penalize_future_attempt(self):
        rt, pc, add, _ = harness(native_rejection=True)
        add(Pickup(100))
        rt._service_candidates(10., pc)
        self.assertEqual(pc.authority_calls, [])
        self.assertEqual(rt._candidates[100][2], 0)

    def test_disabled_native_pickup_waits_one_second(self):
        rt, pc, add, _ = harness()
        p = Pickup(100)
        p.enabled = False
        add(p)
        rt._service_candidates(10., pc)
        self.assertEqual(rt._candidates[100][2], 11.)
        p.enabled = True
        rt._service_candidates(10.5, pc)
        self.assertEqual(pc.authority_calls, [])
        rt._service_candidates(11., pc)
        self.assertEqual(pc.authority_calls, [(100, False)])

    def test_out_of_range_never_attempts_or_delays(self):
        rt, pc, add, _ = harness()
        add(Pickup(100, x=500.))
        rt._service_candidates(10., pc)
        self.assertEqual(pc.authority_calls, [])
        self.assertEqual(rt._candidates[100][2], 0)

    def test_deleted_candidate_pruned(self):
        rt, pc, add, _ = harness()
        pickup = Pickup(100)
        pickup.deleted = True
        add(pickup)
        rt._service_candidates(10., pc)
        self.assertNotIn(100, rt._candidates)

    def test_missing_pawn_prevents_all_attempts(self):
        rt, pc, add, _ = harness()
        add(Pickup(100))
        pc.Pawn = None
        rt._service_candidates(10., pc)
        self.assertEqual(pc.authority_calls, [])
        self.assertEqual(rt._candidates[100][2], 0)

    def test_mission_item_is_pruned_before_native_pickup(self):
        rt, pc, add, _ = harness()
        add(Pickup(100))
        rt._is_mission_item = lambda pickup: True
        rt._service_candidates(10., pc)
        self.assertEqual(pc.authority_calls, [])
        self.assertNotIn(100, rt._candidates)

    def test_disabled_category_is_pruned_before_native_pickup(self):
        rt, pc, add, _ = harness()
        add(Pickup(100))
        rt._category_enabled = lambda category: False
        rt._service_candidates(10., pc)
        self.assertEqual(pc.authority_calls, [])
        self.assertNotIn(100, rt._candidates)

    def test_tps_grinder_open_skips_all_pickups(self):
        rt, pc, add, _ = harness(is_tps=True)
        pc.bUsingGrinder = True
        add(Pickup(100))
        rt._service_candidates(10., pc)
        self.assertEqual(pc.authority_calls, [])
        self.assertEqual(pc.client_calls, [])
        self.assertIn(100, rt._candidates)

    def test_error_suppression_identifies_only_active_auto_attempt(self):
        rt, pc, add, _ = harness()
        add(Pickup(100))
        inside = []
        def observe(pickup):
            inside.append((rt._auto_attempt_controller, rt._auto_attempt_pickup))
        pc.on_native_pickup = observe
        rt._service_candidates(10., pc)
        self.assertEqual(inside, [(42, 100)])
        self.assertEqual((rt._auto_attempt_controller, rt._auto_attempt_pickup), (0, 0))

    def test_reentrant_registration_does_not_overwrite_new_candidate(self):
        rt, pc, add, _ = harness()
        original = Pickup(100)
        replacement = Pickup(100)
        add(original)
        def replace(_pickup):
            # Simulate an inventory event registering a new entry under the old key.
            rt._candidates[100] = (lambda: replacement, 'Weapon', 0, 0)
        pc.on_native_pickup = replace
        rt._service_candidates(10., pc)
        self.assertEqual(pc.authority_calls, [(100, False)])
        self.assertEqual(rt._candidates[100][1], 'Weapon')
        self.assertEqual(rt._candidates[100][2], 0)

    def test_native_exception_forgets_candidate_and_resets_error_suppression(self):
        rt, pc, add, errors = harness()
        add(Pickup(100))
        def fail(_pickup):
            raise RuntimeError('pickup API inaccessible')
        pc.on_native_pickup = fail
        rt._service_candidates(10., pc)
        self.assertEqual((rt._auto_attempt_controller, rt._auto_attempt_pickup), (0, 0))
        self.assertNotIn(100, rt._candidates)
        self.assertEqual(len(errors), 1)
        self.assertIn('pickup API inaccessible', errors[0][1])


if __name__ == '__main__':
    unittest.main()
