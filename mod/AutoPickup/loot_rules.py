"""Intrinsic pickup categories. Eligibility and ownership stay in the runtime."""
from __future__ import annotations

_CATEGORY_AMMO = 'Ammo'
_CATEGORY_CURRENCY = 'Currency'
_CATEGORY_TORGUE = 'Torgue Token'
_CATEGORY_SERAPH = 'Seraph Crystal'
_CATEGORY_OTHER = 'Other Consumable'
_CATEGORY_WEAPON = 'Weapon'
_CATEGORY_SHIELD = 'Shield'
_CATEGORY_GRENADE_MOD = 'Grenade Mod'
_CATEGORY_CLASS_MOD = 'Class Mod'
_CATEGORY_RELIC = 'Relic'
_GEAR_CATEGORIES = frozenset({_CATEGORY_WEAPON, _CATEGORY_SHIELD, _CATEGORY_GRENADE_MOD, _CATEGORY_CLASS_MOD, _CATEGORY_RELIC})
_GEAR_CLASS_CATEGORIES = {'weapontypedefinition': _CATEGORY_WEAPON, 'turretweapontypedefinition': _CATEGORY_WEAPON, 'vehicleweapontypedefinition': _CATEGORY_WEAPON, 'buzzaxeweapontypedefinition': _CATEGORY_WEAPON, 'shielddefinition': _CATEGORY_SHIELD, 'grenademoddefinition': _CATEGORY_GRENADE_MOD, 'classmoddefinition': _CATEGORY_CLASS_MOD, 'crossdlcclassmoddefinition': _CATEGORY_CLASS_MOD, 'artifactdefinition': _CATEGORY_RELIC}
_RARITY_THRESHOLDS = {'Any': 0, 'Green+': 2, 'Blue+': 3, 'Purple+': 4, 'Legendary+': 5}
_AMMO_PREFIX = 'gd_ammodrops.'
_CURRENCY_PREFIX = 'gd_currency.a_item.'
# TPS oxygen restores; shared path classification is harmless on BL2.
_TPS_OXYGEN_PREFIX = 'gd_buffdrinks.a_item.buffdrink_oxygeninstant'
TPS_CONSUMABLE_DEFINITIONS = frozenset({
    'gd_buffdrinks.a_item.buffdrink_healinginstant',
    'gd_buffdrinks.a_item.buffdrink_healingregen',
    'gd_buffdrinks.a_item.buffdrink_oxygeninstant',
    'gd_buffdrinks.a_item.buffdrink_toughness',
})
# Exact TPS Startup exports only. Other DLC or special resources stay excluded.
TPS_RESOURCE_DEFINITIONS = frozenset({
    'gd_ammodrops.pickups.ammodrop_assault_rifle_bullets',
    'gd_ammodrops.pickups.ammodrop_grenade_protean',
    'gd_ammodrops.pickups.ammodrop_laser_cells',
    'gd_ammodrops.pickups.ammodrop_patrol_smg_clip',
    'gd_ammodrops.pickups.ammodrop_repeater_pistol_clip',
    'gd_ammodrops.pickups.ammodrop_rocket_launcher',
    'gd_ammodrops.pickups.ammodrop_shotgun_shells',
    'gd_ammodrops.pickups.ammodrop_sniper_rifle_cartridges',
    'gd_currency.a_item.currency', 'gd_currency.a_item.currency_big',
    'gd_currency.a_item.moonstone', 'gd_currency.a_item.moonstonecluster',
})
_TORGUE_TOKEN_MARKER = 'torguetoken'
_TORGUE_TOKEN_PICKUP_MARKER = 'pickup_torguetoken'
_SERAPH_MARKER = 'seraphcrystal'
_SERAPH_PICKUP_MARKER = 'pickup_seraphcrystal'

UNCLASSIFIED = object()


def classify_path(identity):
    identity = identity.lower()
    if any(marker in identity for marker in ("echorecorder", "echo_recorder", "echolog", "echo_log", "audio_log", "audiolog")):
        return None
    if identity.startswith(_AMMO_PREFIX):
        return _CATEGORY_AMMO
    if identity.startswith(_TPS_OXYGEN_PREFIX):
        return _CATEGORY_OTHER
    if identity.startswith(_CURRENCY_PREFIX):
        return _CATEGORY_CURRENCY
    if _TORGUE_TOKEN_MARKER in identity and _TORGUE_TOKEN_PICKUP_MARKER in identity:
        return _CATEGORY_TORGUE
    if _SERAPH_MARKER in identity and _SERAPH_PICKUP_MARKER in identity:
        return _CATEGORY_SERAPH
    return UNCLASSIFIED


def classify_definition(identity, class_name, automatically_pickup=False):
    kind = classify_path(identity)
    if kind is not UNCLASSIFIED:
        return kind
    kind = _GEAR_CLASS_CATEGORIES.get(class_name.lower())
    return kind if kind is not None else (_CATEGORY_OTHER if automatically_pickup else None)


def tps_definition_allowed(identity, class_name, category):
    """Recognize TPS definitions; actor safety and native eligibility are separate."""
    identity = identity.lower()
    class_name = class_name.lower()
    if classify_path(identity) is None:
        return False
    if category in _GEAR_CATEGORIES:
        return _GEAR_CLASS_CATEGORIES.get(class_name) == category
    if class_name != 'usableitemdefinition':
        return False
    if category in (_CATEGORY_AMMO, _CATEGORY_CURRENCY):
        return identity in TPS_RESOURCE_DEFINITIONS and classify_path(identity) == category
    if category == _CATEGORY_OTHER:
        return identity in TPS_CONSUMABLE_DEFINITIONS
    return False
