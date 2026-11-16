"""Exercise the production AutoPickup one-time seed methods with SDK boundaries.

These are offline failure-injection tests, not in-game/engine validation.
"""
from __future__ import annotations

import ast
from pathlib import Path
import time
from types import SimpleNamespace as NS
import unittest
import weakref

SOURCE=Path(__file__).resolve().parents[1]/"mod"/"AutoPickup"/"__init__.py"
METHODS={"_request_seed","_run_seed_if_requested","_seed_current_world",
         "_clear_runtime_state"}


class World:
    def __init__(self,address):
        self.address=address

    def _get_address(self):
        return self.address


class Pickup:
    def __init__(self,world,number):
        self.WorldInfo=world
        self.number=number


def setup():
    tree=ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef)
             and n.name=="AutoPickupRuntime")
    selected=[n for n in cls.body if isinstance(n,ast.FunctionDef)
              and n.name in METHODS]
    assert len(selected)==len(METHODS), "Production seed methods changed"
    testcls=ast.ClassDef(name="SeedRuntime",bases=[],keywords=[],
                         body=selected,decorator_list=[])
    errors=[]
    logs=[]
    api=NS(find_all=lambda *args,**kwargs:(),unreal=NS(WeakPointer=weakref.ref))
    env={"time":time,"unrealsdk":api,
         "_log":lambda msg:logs.append(msg),
         "_log_exception":lambda label,exc:errors.append((label,str(exc)))}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[testcls],
                                type_ignores=[])),str(SOURCE),"exec"),env)
    runtime=env["SeedRuntime"]()
    runtime._runtime_enabled=True
    runtime._traveling=False
    runtime._seed_requested=False
    runtime._seed_reason=""
    runtime._seed_not_before=0.
    runtime._seed_retry_count=0
    runtime._seed_retry_world=None
    runtime._seed_epoch=0
    runtime._candidates={}
    runtime._definition_cache={}
    runtime._next_service_time=0.
    runtime._client_window_started=0.
    runtime._client_requests_sent=0
    runtime._auto_attempt_controller=0
    runtime._auto_attempt_pickup=0
    runtime._clear_candidates=lambda:runtime._candidates.clear()
    runtime._object_address=lambda obj:obj._get_address() if obj is not None else 0
    runtime._is_deleted=lambda pickup:False
    controller=NS(WorldInfo=World(101))
    return runtime,controller,api,errors,logs


class SeedRecoveryTests(unittest.TestCase):
    def test_bad_middle_actor_does_not_hide_later_good_actor(self):
        r,pc,api,errors,_=setup()
        actors=[Pickup(pc.WorldInfo,i) for i in ("A","B","C")]
        api.find_all=lambda *a,**k:iter(actors)
        called=[]
        def register(pickup):
            called.append(pickup.number)
            if pickup.number=="B":raise RuntimeError("bad reflected getter")
            r._candidates[pickup.number]=pickup
            return True
        r._register_pickup=register
        r._request_seed("initial",now=1.)
        r._run_seed_if_requested(pc,1.)
        self.assertEqual(called,["A","B","C"])
        self.assertEqual(set(r._candidates),{"A","C"})
        self.assertFalse(r._seed_requested)
        self.assertEqual(r._seed_retry_count,0)
        self.assertEqual(len(errors),1)
        self.assertIn("1 failing pickup",errors[0][0])

    def test_unreadable_actor_world_is_skipped_without_aborting_scan(self):
        r,pc,api,errors,_=setup()
        bad=Pickup(pc.WorldInfo,"bad")
        class Broken:
            @property
            def WorldInfo(self):raise RuntimeError("WorldInfo getter failed")
        api.find_all=lambda *a,**k:iter([Pickup(pc.WorldInfo,"A"),Broken(),
                                          Pickup(World(202),"foreign"),
                                          Pickup(pc.WorldInfo,"C")])
        registered=[]
        r._register_pickup=lambda p:registered.append(p.number) or True
        self.assertEqual(r._seed_current_world(pc),(2,2))
        self.assertEqual(registered,["A","C"])
        self.assertEqual(len(errors),1)

    def test_global_enumeration_error_retries_only_same_world(self):
        r,pc,api,errors,_=setup()
        attempts=[]
        def find_all(*a,**k):
            attempts.append(True)
            if len(attempts)==1:raise RuntimeError("global enumeration failed")
            return [Pickup(pc.WorldInfo,"A")]
        api.find_all=find_all
        visited=[]
        r._register_pickup=lambda p:visited.append(p.number) or True
        r._request_seed("enable",now=10.)
        r._run_seed_if_requested(pc,10.)
        self.assertTrue(r._seed_requested)
        self.assertEqual(r._seed_retry_count,1)
        self.assertAlmostEqual(r._seed_not_before,10.5)
        r._run_seed_if_requested(pc,10.3)
        self.assertEqual(len(attempts),1)
        r._run_seed_if_requested(pc,10.5)
        self.assertEqual(len(attempts),2)
        self.assertEqual(visited,["A"])
        self.assertFalse(r._seed_requested)
        self.assertEqual(r._seed_retry_count,0)

    def test_permanent_global_failure_stops_after_three_attempts(self):
        r,pc,api,errors,_=setup()
        attempts=[]
        def fail(*args,**kwargs):
            attempts.append(1)
            raise RuntimeError("permanent global failure")
        api.find_all=fail
        r._request_seed("enable",now=10.)
        for now in (10.,10.5,11.5,20.,30.):
            r._run_seed_if_requested(pc,now)
        self.assertEqual(len(attempts),3)
        self.assertFalse(r._seed_requested)
        self.assertEqual(len(errors),3)

    def test_pending_retry_cannot_scan_replacement_world(self):
        r,pc,api,errors,_=setup()
        calls=[]
        def fail(*args,**kwargs):
            calls.append(1)
            raise RuntimeError("retry later")
        api.find_all=fail
        r._request_seed("enable",now=1.)
        r._run_seed_if_requested(pc,1.)
        self.assertTrue(r._seed_requested)
        pc.WorldInfo=World(202)
        r._run_seed_if_requested(pc,1.5)
        self.assertFalse(r._seed_requested)
        self.assertEqual(len(calls),1)
        # An explicit later lifecycle seed can independently use the new world.
        api.find_all=lambda *a,**kw:[Pickup(pc.WorldInfo,"new")]
        visited=[]
        r._register_pickup=lambda p:visited.append(p.number) or True
        r._request_seed("new world arrival",now=2.)
        r._run_seed_if_requested(pc,2.)
        self.assertEqual(visited,["new"])

    def test_travel_invalidates_outstanding_retry(self):
        r,pc,api,errors,_=setup()
        calls=[]
        def fail(*args,**kwargs):
            calls.append(1)
            raise RuntimeError("temporary")
        api.find_all=fail
        r._candidates["old"]="old"
        r._request_seed("enable",now=1.)
        r._run_seed_if_requested(pc,1.)
        self.assertTrue(r._seed_requested)
        r._traveling=True
        r._clear_runtime_state(clear_definition_cache=True)
        r._seed_requested=False
        r._run_seed_if_requested(pc,5.)
        self.assertEqual(len(calls),1)
        self.assertEqual(r._candidates,{})
        self.assertIsNone(r._seed_retry_world)
        self.assertEqual(r._seed_retry_count,0)

    def test_new_seed_request_supersedes_pending_retry_budget(self):
        r,pc,api,errors,_=setup()
        api.find_all=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError("transient"))
        r._request_seed("initial",now=1.)
        r._run_seed_if_requested(pc,1.)
        self.assertEqual(r._seed_retry_count,1)
        r._request_seed("changed rarity",now=1.1)
        self.assertEqual(r._seed_retry_count,0)
        self.assertIsNone(r._seed_retry_world)
        self.assertEqual(r._seed_reason,"changed rarity")


if __name__=="__main__":
    unittest.main()
