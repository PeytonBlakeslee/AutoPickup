"""BUG-20: source-level capacity policy matrix, not game/SDK certification.

The production helper is loaded directly without the Unreal SDK.  Stubbed native
results simulate BL2/TPS capacity predicates; real engine behavior still needs
phase-05 acceptance, especially unusual gear and zero-slot inventories.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "mod" / "AutoPickup" / "capacity_guard.py"
spec = importlib.util.spec_from_file_location("ap_capacity_policy", SOURCE)
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)

GEAR = frozenset(("Weapon", "Shield", "Grenade Mod", "Class Mod", "Relic"))
PICKUP = object()
PAWN = NS()


def controller_with(*, native=True, slots=1):
    def has_room(_pickup):
        if isinstance(native, Exception):
            raise native
        return native

    def empty_slots():
        if isinstance(slots, Exception):
            raise slots
        return slots

    return NS(
        HasRoomInInventoryFor=has_room,
        GetPawnInventoryManager=lambda: NS(GetEmptyBackpackSlots=empty_slots),
    )


def result(category, *, native=True, slots=None, pawn=PAWN, pickup=PICKUP):
    controller = controller_with(native=native)
    return capacity.reject_for_capacity(category, GEAR, controller, pawn, pickup, slots)


class CapacityPolicyTests(unittest.TestCase):
    def test_all_gear_categories_three_by_three_capacity_matrix(self):
        # None means the *wrapped* native predicate was unavailable, not that
        # the bool-returning game method returned a Python None.
        for category in GEAR:
            for slots in (None, 0, 2):
                for native in (True, False, None):
                    with self.subTest(category=category, slots=slots, native=native):
                        with patch.object(capacity, "native_has_room", return_value=native):
                            blocked, reason = result(category, slots=slots)
                        expected_blocked = (native is False or (slots == 0 and native is not True))
                        expected_reason = (
                            "backpack" if slots == 0 and native is not True
                            else "native-inventory" if native is False else "backpack"
                        )
                        self.assertEqual((blocked, reason), (expected_blocked, expected_reason))

    def test_native_true_can_authorize_zero_slot_special_equipment(self):
        for category in GEAR:
            with self.subTest(category=category):
                self.assertEqual(result(category, native=True, slots=0), (False, "backpack"))

    def test_zero_slots_plus_unknown_native_result_remains_blocked(self):
        with patch.object(capacity, "native_has_room", return_value=None):
            self.assertEqual(result("Weapon", slots=0), (True, "backpack"))

    def test_unknown_slots_plus_unknown_native_result_delegates_to_stock(self):
        with patch.object(capacity, "native_has_room", return_value=None):
            self.assertEqual(result("Weapon", slots=None), (False, "backpack"))

    def test_native_false_blocks_gear_even_with_reported_free_slots(self):
        self.assertEqual(result("Weapon", native=False, slots=5), (True, "native-inventory"))

    def test_native_reflection_true_false_and_missing(self):
        self.assertIs(capacity.native_has_room(controller_with(native=True), PICKUP), True)
        self.assertIs(capacity.native_has_room(controller_with(native=False), PICKUP), False)
        self.assertIsNone(capacity.native_has_room(controller_with(native=RuntimeError("unavailable")), PICKUP))
        self.assertIsNone(capacity.native_has_room(NS(), PICKUP))

    def test_invalid_raw_none_returned_by_bool_api_fails_conservatively(self):
        # UnrealScript declares HasRoomInInventoryFor as bool. A Python None
        # returned by an abnormal wrapper is coerced to False, unlike a raised
        # reflection exception (which maps to unknown). Record that distinction
        # without relaxing pickup safety on an unverified SDK variant.
        self.assertIs(capacity.native_has_room(controller_with(native=None), PICKUP), False)

    def test_backpack_reflection_zero_unknown_positive_negative(self):
        for slots, expected in ((0, 0), (4, 4), (-3, 0), (RuntimeError("unavailable"), None)):
            with self.subTest(slots=slots):
                self.assertEqual(capacity.empty_backpack_slots(controller_with(slots=slots)), expected)
        self.assertIsNone(capacity.empty_backpack_slots(NS()))

    def test_ordinary_non_gear_respects_definite_native_rejection(self):
        for category in ("Ammo", "Other Consumable"):
            for slots in (None, 0, 3):
                with self.subTest(category=category, slots=slots):
                    self.assertEqual(result(category, native=False, slots=slots), (True, "native-inventory"))

    def test_unknown_native_non_gear_defers_if_resource_not_known_full(self):
        for category in ("Ammo", "Other Consumable"):
            with self.subTest(category=category):
                with patch.object(capacity, "native_has_room", return_value=None):
                    self.assertFalse(result(category, slots=0)[0])

    def test_currency_token_crystal_bypass_native_early_rejection(self):
        for category in ("Currency", "Torgue Token", "Seraph Crystal"):
            with self.subTest(category=category):
                # By design, no HasRoomInInventoryFor check is made here.
                with patch.object(capacity, "native_has_room", side_effect=AssertionError("should not call")):
                    self.assertEqual(result(category, native=False, slots=0), (False, "not-applicable"))

    def test_unknown_ammo_mapping_defers_to_native(self):
        self.assertEqual(result("Ammo", native=True, slots=0), (False, "ammo"))

    def test_verified_full_ammo_pool_is_rejected(self):
        resource = object()
        definition = NS(AmmoResource=resource)
        inventory = NS(DefinitionData=NS(ItemDefinition=definition))
        pickup = NS(GetPickupableInventory=lambda: inventory)
        pool = NS(Data=NS(GetCurrentValue=lambda: 10, GetMaxValue=lambda: 10))
        controller = NS(
            HasRoomInInventoryFor=lambda _: True,
            GetResourcePoolForResourceDefinition=lambda key, create: pool if key is resource and not create else None,
        )
        self.assertEqual(capacity.reject_for_capacity("Ammo", GEAR, controller, PAWN, pickup, None), (True, "ammo"))
        pool.Data.GetCurrentValue = lambda: 9
        self.assertEqual(capacity.reject_for_capacity("Ammo", GEAR, controller, PAWN, pickup, None), (False, "ammo"))

    def test_health_consumable_only_blocked_when_known_full(self):
        definition = NS(GetFullDefinitionName=lambda: "GD_BuffDrinks.A_Item.BuffDrink_Healing")
        pickup = NS(GetPickupableInventory=lambda: NS(DefinitionData=NS(ItemDefinition=definition)))
        pawn = NS(GetMaxHealth=lambda: 100, GetHealth=lambda: 100)
        self.assertEqual(result("Other Consumable", pickup=pickup, pawn=pawn), (True, "health"))
        pawn.GetHealth = lambda: 70
        self.assertEqual(result("Other Consumable", pickup=pickup, pawn=pawn), (False, "health"))
        pawn.GetHealth = lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
        self.assertEqual(result("Other Consumable", pickup=pickup, pawn=pawn), (False, "health"))

    def test_oxygen_consumable_only_blocked_when_known_full(self):
        definition = NS(GetFullDefinitionName=lambda: "GD_BuffDrinks.A_Item.BuffDrink_OxygenInstant")
        pickup = NS(GetPickupableInventory=lambda: NS(DefinitionData=NS(ItemDefinition=definition)))
        pool = NS(Data=NS(GetCurrentValue=lambda: 100, GetMaxValue=lambda: 100))
        pawn = NS(OxygenPool=pool)
        self.assertEqual(result("Other Consumable", pickup=pickup, pawn=pawn), (True, "oxygen"))
        pool.Data.GetCurrentValue = lambda: 50
        self.assertEqual(result("Other Consumable", pickup=pickup, pawn=pawn), (False, "oxygen"))

    def test_unknown_consumable_definition_falls_back_to_native(self):
        definition = NS(GetFullDefinitionName=lambda: "DLC.UnknownSpecialConsumable")
        pickup = NS(GetPickupableInventory=lambda: NS(DefinitionData=NS(ItemDefinition=definition)))
        self.assertEqual(result("Other Consumable", pickup=pickup), (False, "not-applicable"))


if __name__ == "__main__":
    unittest.main()
