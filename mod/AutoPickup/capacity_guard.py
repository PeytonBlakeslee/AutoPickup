"""Cheap, conservative pickup capacity checks for BL2 and TPS.

Full-resource checks return True when full, False when room is known,
and None when unknown. The native inventory predicate has the opposite
sense (True means room, False means no room). For optional gear, unknown
native capacity does not override a known zero-slot backpack: fail closed.
Never derive inventory limits from configured SDU levels or item name guesses.
"""

def _pool_full(pool):
    try:
        data = pool.Data
        current = float(data.GetCurrentValue())
        maximum = float(data.GetMaxValue())
        if maximum > 0:
            return current >= maximum - 0.001
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass
    return None


def native_has_room(controller, pickup):
    """Game's own non-mutating room check used by PickupPickupable.

    Reflected WillowPlayerController.HasRoomInInventoryFor may be unavailable
    in an SDK/game variant. Unknown normally defers to stock pickup validation,
    except that optional gear with a known zero-slot backpack is skipped.
    """
    try:
        return bool(controller.HasRoomInInventoryFor(pickup))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None


def empty_backpack_slots(controller):
    """Ask the game's inventory manager; do not count equipped gear as bag slots."""
    try:
        manager = controller.GetPawnInventoryManager()
        if manager is not None:
            return max(0, int(manager.GetEmptyBackpackSlots()))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass
    return None


def _pickup_definition_path(pickup):
    try:
        item = pickup.GetPickupableInventory()
        if item is None:
            return ""
        data = item.DefinitionData
        definition = data.ItemDefinition
        if definition is None:
            return ""
        return str(definition.GetFullDefinitionName()).lower()
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return ""


def _health_full(pawn):
    try:
        maximum = float(pawn.GetMaxHealth())
        if maximum > 0:
            return float(pawn.GetHealth()) >= maximum - 0.001
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass
    return None


def _oxygen_full(pawn):
    try:
        return _pool_full(pawn.OxygenPool)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None


def _ammo_full(controller, pickup):
    """Resolve a native ammo resource only if the pickup exposes its real mapping.

    Ammo subtype names (pistol/laser/shotgun) alone are not enough to assume
    which pool holds its quantity. If no usable mapping is exposed, fail open.
    """
    try:
        item = pickup.GetPickupableInventory()
        if item is None:
            return None
        definition = item.DefinitionData.ItemDefinition
        if definition is None:
            return None
        resource = None
        for owner in (definition, item):
            for key in ("AmmoResource", "ResourceDefinition", "Resource"):
                try:
                    resource = getattr(owner, key, None)
                except (AttributeError, RuntimeError):
                    resource = None
                if resource is not None:
                    break
            if resource is not None:
                break
        if resource is None:
            return None
        pool = controller.GetResourcePoolForResourceDefinition(resource, False)
        if pool is None:
            return None
        return _pool_full(pool)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None


def reject_for_capacity(category, gear_categories, controller, pawn, pickup,
                        backpack_slots):
    """Return (blocked, reason), preserving the conservative gear exception.

    For optional gear, zero known free slots plus an unknown native room check
    blocks the attempt. Other unknown capacity states defer to stock pickup.
    """
    if category in gear_categories:
        has_room = native_has_room(controller, pickup)
        if backpack_slots is not None and backpack_slots <= 0:
            # Stock may explicitly authorize special gear despite zero bag slots.
            # Without that positive evidence, do not attempt the pickup.
            if has_room is not True:
                return True, "backpack"
        if has_room is False:
            return True, "native-inventory"
        return False, "backpack"
    # Same early inventory predicate called by the game's own pickup flow.
    # Skip on definitive False, but never on unknown reflection/runtime state.
    if category not in ("Currency", "Torgue Token", "Seraph Crystal"):
        if native_has_room(controller, pickup) is False:
            return True, "native-inventory"
    if category == "Ammo":
        return _ammo_full(controller, pickup) is True, "ammo"
    if category == "Other Consumable":
        path = _pickup_definition_path(pickup)
        if "gd_buffdrinks.a_item.buffdrink_healing" in path:
            return _health_full(pawn) is True, "health"
        if "gd_buffdrinks.a_item.buffdrink_oxygeninstant" in path:
            return _oxygen_full(pawn) is True, "oxygen"
    return False, "not-applicable"


# Co-op authority gate: unknown network state cannot authorize native pickup writes.
def pickup_network_mode(controller):
    """Return 'client', 'authority', or None when world state is unavailable.

    BL2 uses UE3 NetMode: Standalone=0, DedicatedServer=1, ListenServer=2,
    Client=3. Check named enums first, then the numeric SDK representation.
    Unknown/unreadable modes must never fall through to local pickup writes.
    """
    try:
        mode = controller.WorldInfo.NetMode
        name = getattr(mode, "name", None)
        if name == "NM_Client":
            return "client"
        if name in ("NM_Standalone", "NM_DedicatedServer", "NM_ListenServer"):
            return "authority"
        value = int(mode)
    except (AttributeError, TypeError, ValueError, RuntimeError, OverflowError):
        return None
    if value == 3:
        return "client"
    if value in (0, 1, 2):
        return "authority"
    return None
