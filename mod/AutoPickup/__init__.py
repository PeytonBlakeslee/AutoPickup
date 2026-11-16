"""Lightweight event-indexed automatic pickup for Borderlands 2."""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from .settings_migration import migrate_legacy_settings, cleanup_obsolete_settings, migrate_settings
from . import loot_rules, capacity_guard
from .capacity_guard import pickup_network_mode

import unrealsdk
from mods_base import (
    SETTINGS_DIR,
    BoolOption,
    ButtonOption,
    EInputEvent,
    Game,
    GroupedOption,
    KeybindOption,
    KeybindType,
    Mod,
    SliderOption,
    SpinnerOption,
    build_mod,
    get_pc,
)
from unrealsdk import logging

__version__ = "0.3.5-beta.3"
__author__ = "Peyton"

_MOD_NAME = "Auto Pickup"
IS_TPS = Game.get_current() == Game.TPS
_SETTINGS_FILE = SETTINGS_DIR / "AutoPickup.json"
migrate_settings(SETTINGS_DIR)
_LEGACY_SETTINGS_FILE = Path(__file__).with_name("settings.json")

_DEFAULT_FREQUENCY_HZ = 5
_MIN_FREQUENCY_HZ = 1
_MAX_FREQUENCY_HZ = 20
_DEFAULT_RADIUS_PERCENT = 100
_MIN_RADIUS_PERCENT = 100
_MAX_RADIUS_PERCENT = 500
_DEFAULT_MIN_GEAR_RARITY = "Any"
_RESET_DEBOUNCE_SECONDS = 0.50
_MASTER_TOGGLE_DEBOUNCE_SECONDS = 0.35
_PLAYER_DROPPED_ITEM_LOCATION = 5
_DEPRECATED_OPTION_KEYS = frozenset({"Mission Items [RISKY]", "Mission Items", "Debug Logging"})

# Existing option identifiers intentionally match the legacy captions where possible.
# That keeps legacy settings migration simple and preserves user choices.
_OPT_FREQUENCY = "Frequency (Hz)"
_OPT_RADIUS = "Pickup Radius (%)"
_OPT_AMMO = "Ammo"
_OPT_CURRENCY = "Money / Eridium"
_OPT_TORGUE = "Torgue Tokens"
_OPT_SERAPH = "Seraph Crystals"
_OPT_OTHER = "Health / Other Consumables"
_OPT_WEAPONS = "Weapons"
_OPT_SHIELDS = "Shields"
_OPT_GRENADE_MODS = "Grenade Mods"
_OPT_CLASS_MODS = "Class Mods"
_OPT_RELICS = "Relics"
_OPT_MIN_RARITY = "Minimum Gear Rarity"
_OPT_SELF_DROPS = "Pick Up Self-Dropped Gear"


def _log(message: str) -> None:
    logging.info("[Auto Pickup] " + message)


def _log_exception(context: str, exc: BaseException) -> None:
    logging.error(
        "[Auto Pickup] ERROR {}: {}: {}\n{}".format(
            context,
            type(exc).__name__,
            exc,
            traceback.format_exc(),
        )
    )


def _migrate_legacy_settings() -> None:
    migrate_legacy_settings(
        _SETTINGS_FILE, _LEGACY_SETTINGS_FILE,
        {_OPT_FREQUENCY, _OPT_RADIUS, _OPT_AMMO, _OPT_CURRENCY, _OPT_TORGUE,
         _OPT_SERAPH, _OPT_OTHER, _OPT_WEAPONS},
        _log, _log_exception,
    )


def _cleanup_obsolete_settings() -> None:
    cleanup_obsolete_settings(_SETTINGS_FILE, _DEPRECATED_OPTION_KEYS, _log, _log_exception)


# Runtime is created before the option callbacks can ever be invoked by the UI.
_RUNTIME = None
_MOD = None
_LAST_RESET_PRESS_TIME = float("-inf")
_LAST_MASTER_TOGGLE_TIME = float("-inf")


def _toggle_auto_pickup() -> None:
    """Use the SDK mod's actual Enabled state for both keyboard and controller."""
    global _LAST_MASTER_TOGGLE_TIME

    if _MOD is None or _RUNTIME is None:
        return
    # Do not disable midway through an in-progress travel transaction.
    if _MOD.is_enabled and _RUNTIME._traveling:
        return

    now = time.monotonic()
    if now - _LAST_MASTER_TOGGLE_TIME < _MASTER_TOGGLE_DEBOUNCE_SECONDS:
        return
    _LAST_MASTER_TOGGLE_TIME = now

    try:
        # This same entry point is used by the keyboard and controller keybind.
        # Neither direction is allowed to rewrite the startup preference.
        _MOD.toggle_for_session()
    except Exception as exc:
        _log_exception("changing Enabled state from keybind", exc)
        return

    # The on_disable lifecycle already restores the standard keybind. Also
    # ensure it is available after a programmatic transition, for resilience.
    _ensure_toggle_keybind_available()
    _sync_open_enabled_row()
    state = "ON" if _MOD.is_enabled else "OFF"
    _log("master toggle: {} (SDK mod Enabled={}, category settings unchanged)".format(
        state, _MOD.is_enabled
    ))
    try:
        controller = get_pc(possibly_loading=True)
        if controller is not None and _RUNTIME._get_inventory_pawn(controller) is not None:
            controller.ClientMessage("Auto Pickup: {}".format(state))
    except Exception as exc:
        _log("master toggle HUD feedback unavailable: {}".format(type(exc).__name__))


def _sync_open_enabled_row() -> bool:
    """Refresh only *our* visible menu row after a shortcut changes the mod.

    The installed Willow2 menu builds a temporary ``Enabled`` BoolOption when
    it opens a mod's page. It is not bound to ``Mod.is_enabled`` after that,
    and the old menu version exposes ``latest_list`` but not the newer reload
    helper. A subsequent opening of the page always creates the correct row.
    """
    if _MOD is None:
        return False
    try:
        from willow2_mod_menu import options_menu
        from willow2_mod_menu.data_providers.mod_options import ModOptionsDataProvider
    except ImportError:
        return False

    try:
        stack = options_menu.data_provider_stack
        if not stack:
            return False
        page = stack[-1]
        if not isinstance(page, ModOptionsDataProvider) or page.mod is not _MOD:
            return False

        enabled_row = next(
            (
                option for option in page.options
                if isinstance(option, BoolOption) and option.identifier == "Enabled"
            ),
            None,
        )
        if enabled_row is None:
            return False
        is_enabled = bool(_MOD.is_enabled)
        if enabled_row.value != is_enabled:
            # The menu's callback will call enable/disable again; those are
            # idempotent when the SDK state is already correct.
            enabled_row.value = is_enabled

        get_list = getattr(options_menu, "latest_list", None)
        menu_list = get_list() if callable(get_list) else None
        if menu_list is None:
            return False
        menu_list.Refresh()
        return True
    except Exception as exc:
        _log("Enabled menu row sync unavailable: {}: {}".format(type(exc).__name__, exc))
        return False


def _ensure_toggle_keybind_available() -> None:
    """Keep the stock keybind active even while the SDK mod is disabled."""
    try:
        if not ToggleAutoPickup.is_enabled:
            ToggleAutoPickup.enable()
    except Exception as exc:
        _log_exception("keeping quick toggle keybind available", exc)


# A standard Mods Base keybind is deliberately the only input integration.
# Controller Binds (and future replacements which support KeybindType) discover
# this public bind and dispatch its callback; we do not intercept input devices,
# change native button mappings, or depend on a particular controller mod.
ToggleAutoPickup = KeybindType(
    "Toggle Auto Pickup",
    None,
    _toggle_auto_pickup,
    display_name="Toggle Auto Pickup",
    description="Turn Auto Pickup on/off, synchronized with the mod menu. Unbound by default.",
    event_filter=EInputEvent.IE_Pressed,
)


def _frequency_changed(_option, new_value) -> None:
    if _RUNTIME is not None:
        _RUNTIME.on_frequency_changed(new_value)


def _radius_changed(_option, new_value) -> None:
    if _RUNTIME is not None:
        _RUNTIME.on_radius_changed(new_value)


def _category_changed(_option, new_value) -> None:
    if _RUNTIME is not None:
        _RUNTIME.on_category_changed(bool(new_value))


def _rarity_changed(_option, _new_value) -> None:
    if _RUNTIME is not None:
        _RUNTIME.on_rarity_changed()


def _self_drop_changed(_option, new_value) -> None:
    if _RUNTIME is not None:
        _RUNTIME.on_self_drop_changed(bool(new_value))


def _refresh_open_options_page() -> bool:
    """Best-effort refresh of the currently open Willow2 mod-options page."""
    try:
        from willow2_mod_menu import options_menu as menu_options
    except Exception:
        return False

    try:
        reload_page = getattr(menu_options, "dangerous_reload_current_page", None)
        if callable(reload_page):
            reload_page()
            return True

        # Willow2 Mod Menu 3.6 predates the public reset-options work. Older
        # builds still keep a weak reference to the active scrolling list.
        latest_list = getattr(menu_options, "latest_list", None)
        if latest_list is not None and callable(latest_list):
            the_list = latest_list()
            if the_list is not None:
                the_list.Refresh()
                return True
    except Exception as exc:
        _log("options-page refresh failed: {}: {}".format(type(exc).__name__, exc))

    return False


def _reset_pressed(_option) -> None:
    global _LAST_RESET_PRESS_TIME

    now = time.monotonic()
    if now - _LAST_RESET_PRESS_TIME < _RESET_DEBOUNCE_SECONDS:
        return
    _LAST_RESET_PRESS_TIME = now

    # Mods Base 1.12 does not expose BaseOption.reset(), so assign the defaults
    # explicitly for compatibility with the user's current Willow2 Mod Manager.
    PickupFrequency.value = _DEFAULT_FREQUENCY_HZ
    PickupRadiusPercent.value = _DEFAULT_RADIUS_PERCENT
    PickupAmmo.value = True
    PickupCurrency.value = True
    PickupTorgueTokens.value = True
    PickupSeraphCrystals.value = True
    PickupOtherConsumables.value = True
    PickupWeapons.value = False
    PickupShields.value = False
    PickupGrenadeMods.value = False
    PickupClassMods.value = False
    PickupRelics.value = False
    MinimumGearRarity.value = _DEFAULT_MIN_GEAR_RARITY
    PickupSelfDroppedGear.value = False

    if _RUNTIME is not None:
        _RUNTIME.sync_settings_from_options()
        _RUNTIME.on_reset_defaults()

    if _MOD is not None:
        try:
            _MOD.save_settings()
        except Exception as exc:
            _log_exception("saving reset settings", exc)

    refreshed = _refresh_open_options_page()
    if refreshed:
        _log("settings reset to defaults; options page refreshed")
    else:
        _log("settings reset to defaults; options page refresh unavailable")




ResetDefaults = ButtonOption(
    "Reset to Defaults",
    display_name="Reset to Defaults",
    description="Restore all Auto Pickup options to their defaults.",
    on_press=_reset_pressed,
)

PickupFrequency = SliderOption(
    _OPT_FREQUENCY,
    _DEFAULT_FREQUENCY_HZ,
    _MIN_FREQUENCY_HZ,
    _MAX_FREQUENCY_HZ,
    1,
    display_name="Pickup Frequency (Hz)",
    description="How often nearby drops are checked. Default: 5 times per second.",
    on_change_while_enabled=_frequency_changed,
)

PickupRadiusPercent = SliderOption(
    _OPT_RADIUS,
    _DEFAULT_RADIUS_PERCENT,
    _MIN_RADIUS_PERCENT,
    _MAX_RADIUS_PERCENT,
    25,
    display_name="Pickup Radius (%)",
    description="Pickup reach relative to the game's usual distance. 100% is normal range.",
    on_change_while_enabled=_radius_changed,
)

PickupAmmo = BoolOption(
    _OPT_AMMO,
    True,
    display_name="Ammo",
    description="Experimental: collect eligible ordinary TPS ammo; avoid scripted sequences." if IS_TPS else "Automatically collect supported ammo drops.",
    on_change_while_enabled=_category_changed,
)

PickupCurrency = BoolOption(
    _OPT_CURRENCY,
    True,
    display_name="Money & Moonstones" if IS_TPS else "Money & Eridium",
    description="Experimental: collect eligible ordinary TPS cash and Moonstones; avoid scripted sequences." if IS_TPS else "Automatically collect money and Eridium.",
    on_change_while_enabled=_category_changed,
)

PickupTorgueTokens = BoolOption(
    _OPT_TORGUE,
    True,
    display_name="Torgue Tokens",
    is_hidden=IS_TPS,
    description="Automatically collect Torgue Tokens.",
    on_change_while_enabled=_category_changed,
)

PickupSeraphCrystals = BoolOption(
    _OPT_SERAPH,
    True,
    display_name="Seraph Crystals",
    is_hidden=IS_TPS,
    description="Automatically collect Seraph Crystals.",
    on_change_while_enabled=_category_changed,
)

PickupOtherConsumables = BoolOption(
    _OPT_OTHER,
    True,
    display_name="Health & Consumables",
    description="Experimental TPS health, oxygen and ordinary consumables; avoid scripted rewards." if IS_TPS else "Health and other items BL2 already marks for automatic pickup.",
    on_change_while_enabled=_category_changed,
)

PickupWeapons = BoolOption(
    _OPT_WEAPONS,
    False,
    display_name="Weapons",
    description="Experimental TPS equipment pickup; avoid scripted rewards. Off by default." if IS_TPS else "Automatically collect dropped weapons. Off by default.",
    on_change_while_enabled=_category_changed,
)

PickupShields = BoolOption(
    _OPT_SHIELDS,
    False,
    display_name="Shields",
    description="Experimental TPS equipment pickup; avoid scripted rewards. Off by default." if IS_TPS else "Automatically collect dropped shields. Off by default.",
    on_change_while_enabled=_category_changed,
)

PickupGrenadeMods = BoolOption(
    _OPT_GRENADE_MODS,
    False,
    display_name="Grenade Mods",
    description="Experimental TPS equipment pickup; avoid scripted rewards. Off by default." if IS_TPS else "Automatically collect dropped grenade mods. Off by default.",
    on_change_while_enabled=_category_changed,
)

PickupClassMods = BoolOption(
    _OPT_CLASS_MODS,
    False,
    display_name="Class Mods",
    description="Experimental TPS equipment pickup; avoid scripted rewards. Off by default." if IS_TPS else "Automatically collect dropped class mods. Off by default.",
    on_change_while_enabled=_category_changed,
)

PickupRelics = BoolOption(
    _OPT_RELICS,
    False,
    display_name="Oz Kits" if IS_TPS else "Relics",
    description="Experimental TPS equipment pickup; avoid scripted rewards. Off by default." if IS_TPS else "Automatically collect dropped relics. Off by default.",
    on_change_while_enabled=_category_changed,
)

MinimumGearRarity = SpinnerOption(
    _OPT_MIN_RARITY,
    _DEFAULT_MIN_GEAR_RARITY,
    ["Any", "Green+", "Blue+", "Purple+", "Legendary+"],
    display_name="Minimum Gear Rarity",
    description="Applies to weapons, shields, grenade mods, class mods, and Oz Kits." if IS_TPS else "Applies to weapons, shields, grenade mods, class mods, and relics.",
    on_change_while_enabled=_rarity_changed,
)

PickupSelfDroppedGear = BoolOption(
    _OPT_SELF_DROPS,
    False,
    display_name="Pick Up Self-Dropped Gear",
    description="Also collect gear you drop from your inventory. Off by default.",
    on_change_while_enabled=_self_drop_changed,
)

_OPTIONS = [
    # The mod menu renders Enabled separately above the option list, making this
    # the first user option directly beneath Enabled.
    ResetDefaults,
    PickupFrequency,
    PickupRadiusPercent,
    PickupAmmo,
    PickupCurrency,
    PickupTorgueTokens,
    PickupSeraphCrystals,
    PickupOtherConsumables,
    PickupWeapons,
    PickupShields,
    PickupGrenadeMods,
    PickupClassMods,
    PickupRelics,
    MinimumGearRarity,
    PickupSelfDroppedGear,
]


class AutoPickupRuntime:
    _HOOK_INVENTORY = "AutoPickup.InventoryAssociated"
    _HOOK_TRAVEL = "AutoPickup.PreClientTravel"
    _HOOK_LOADED = "AutoPickup.NotifyLoadedWorld"
    _HOOK_TICK = "AutoPickup.PlayerTick"
    _HOOK_PICKUP_MESSAGE = "AutoPickup.AutoFailedMessage"
    _HOOK_FAILED_PICKUP = "AutoPickup.AutoFailedPickup"

    _FN_INVENTORY = "WillowGame.WillowPickup:InventoryAssociated"
    _FN_TRAVEL = "WillowGame.WillowPlayerController:PreClientTravel"
    _FN_LOADED = "Engine.PlayerController:NotifyLoadedWorld"
    _FN_TICK = "WillowGame.WillowPlayerController:PlayerTick"
    _FN_PICKUP_MESSAGE = "WillowGame.WillowPlayerController:ClientDisplayPickupFailedMessage"
    _FN_FAILED_PICKUP = "WillowGame.WillowPickup:FailedPickup"

    _FAILED_RETRY_SECONDS = 1.0
    _MAX_CLIENT_REQUESTS_PER_SECOND = 8
    _POST_LOAD_STABILIZE_SECONDS = 3.0
    _POST_RESUME_SEED_DELAY_SECONDS = 0.25

    _CATEGORY_AMMO = loot_rules._CATEGORY_AMMO
    _CATEGORY_CURRENCY = loot_rules._CATEGORY_CURRENCY
    _CATEGORY_TORGUE = loot_rules._CATEGORY_TORGUE
    _CATEGORY_SERAPH = loot_rules._CATEGORY_SERAPH
    _CATEGORY_OTHER = loot_rules._CATEGORY_OTHER
    _CATEGORY_WEAPON = loot_rules._CATEGORY_WEAPON
    _CATEGORY_SHIELD = loot_rules._CATEGORY_SHIELD
    _CATEGORY_GRENADE_MOD = loot_rules._CATEGORY_GRENADE_MOD
    _CATEGORY_CLASS_MOD = loot_rules._CATEGORY_CLASS_MOD
    _CATEGORY_RELIC = loot_rules._CATEGORY_RELIC

    _GEAR_CATEGORIES = loot_rules._GEAR_CATEGORIES

    _GEAR_CLASS_CATEGORIES = loot_rules._GEAR_CLASS_CATEGORIES

    _RARITY_THRESHOLDS = loot_rules._RARITY_THRESHOLDS

    # These paths are inventory-definition namespaces, not display-name matches.
    _AMMO_PREFIX = loot_rules._AMMO_PREFIX
    _CURRENCY_PREFIX = loot_rules._CURRENCY_PREFIX
    _TORGUE_TOKEN_MARKER = loot_rules._TORGUE_TOKEN_MARKER
    _TORGUE_TOKEN_PICKUP_MARKER = loot_rules._TORGUE_TOKEN_PICKUP_MARKER
    _SERAPH_MARKER = loot_rules._SERAPH_MARKER
    _SERAPH_PICKUP_MARKER = loot_rules._SERAPH_PICKUP_MARKER

    _CACHE_MISS = object()

    def __init__(self) -> None:
        self._candidates = {}
        self._definition_cache = {}
        self._auto_attempt_controller = 0
        self._auto_attempt_pickup = 0
        self._pickup_frequency_hz = _DEFAULT_FREQUENCY_HZ
        self._service_interval = 1.0 / _DEFAULT_FREQUENCY_HZ
        self._radius_percent = _DEFAULT_RADIUS_PERCENT
        self._radius_multiplier = _DEFAULT_RADIUS_PERCENT / 100.0
        self._next_service_time = 0.0
        self._client_window_started = 0.0
        self._client_requests_sent = 0
        self._runtime_enabled = False
        self._hooks_installed = False
        self._installed_hooks = []
        # A False SDK return can still mean a successful append. Such hooks
        # become owned only when our actual registered callback executes.
        self._provisional_hooks = {}
        # A post-registration exception cannot prove which callback owns the ID.
        # Ambiguous IDs are never removed automatically.
        self._uncertain_hooks = []
        self._traveling = False
        self._final_world_loaded = False
        self._travel_resume_after = 0.0
        self._options_dirty = False
        self._seed_requested = False
        self._seed_reason = ''
        self._seed_not_before = 0.0
        self._seed_epoch = 0
        self._seed_retry_count = 0
        self._seed_retry_world = None

    # ------------------------------------------------------------------
    # Lifecycle / option callbacks
    # ------------------------------------------------------------------

    def enable(self) -> None:
        self._traveling = False
        self._final_world_loaded = False
        self._travel_resume_after = 0.0
        self._options_dirty = False
        self._clear_runtime_state(clear_definition_cache=True)
        self.sync_settings_from_options()

        _log(
            "enabling v{} at {} Hz, radius={}%; installing hooks".format(
                __version__, self._pickup_frequency_hz, self._radius_percent
            )
        )

        try:
            self._install_hooks()
        except Exception as exc:
            self._runtime_enabled = False
            self._remove_hooks()
            # mods_base marks the Mod enabled *before* invoking on_enable.
            # Propagate so AutoPickupMod.enable can run the normal SDK disable
            # lifecycle without saving a false-positive Enabled preference.
            raise RuntimeError("AutoPickup hook installation failed") from exc

        self._runtime_enabled = True
        now = time.monotonic()
        self._next_service_time = now + self._service_interval
        self._request_seed("enable", now=now, delay=0.25)

        _log(
            "v{} enabled: {} Hz; radius={}%; event-indexed; weak-candidates; "
            "lifecycle-seed=on; gear-rarity={}; self-drops={}".format(
                __version__,
                self._pickup_frequency_hz,
                self._radius_percent,
                MinimumGearRarity.value,
                self._on_off(PickupSelfDroppedGear.value),
            )
        )

    def disable(self) -> None:
        self._runtime_enabled = False
        self._traveling = False
        self._final_world_loaded = False
        self._travel_resume_after = 0.0
        self._options_dirty = False
        self._seed_requested = False
        self._seed_reason = ''
        self._seed_not_before = 0.0
        self._remove_hooks()
        self._clear_runtime_state(clear_definition_cache=True)
        _ensure_toggle_keybind_available()
        _log('disabled')

    def toggle_master_pickup(self) -> None:
        """Compatibility entry point; the SDK's Enabled state is authoritative."""
        _toggle_auto_pickup()

    def sync_settings_from_options(self) -> None:
        self._apply_frequency(PickupFrequency.value)
        self._apply_radius_percent(PickupRadiusPercent.value)

    def on_frequency_changed(self, new_value) -> None:
        self._apply_frequency(new_value)
        self._next_service_time = time.monotonic()

    def on_radius_changed(self, new_value) -> None:
        self._apply_radius_percent(new_value)
        self._next_service_time = time.monotonic()

    def on_category_changed(self, enabling: bool) -> None:
        if not self._runtime_enabled:
            return
        # ValueOption callbacks run before the actual option value is changed.
        # Reconcile on the next PlayerTick, after the new value is visible.
        self._options_dirty = True
        if enabling:
            self._request_seed("category enabled", delay=0.10)
        self._next_service_time = time.monotonic()

    def on_self_drop_changed(self, enabling: bool) -> None:
        if not self._runtime_enabled:
            return
        # Enabling requires a one-time seed because self-dropped pickups may have
        # been deliberately skipped while this option was Off. Disabling is
        # reconciled on the next tick and prunes any such live candidates.
        self._options_dirty = True
        if enabling:
            self._request_seed("self-dropped pickup enabled", delay=0.10)
        self._next_service_time = time.monotonic()

    def on_rarity_changed(self) -> None:
        if not self._runtime_enabled:
            return
        self._options_dirty = True
        # Always reseed after a rarity change. This is needed when lowering the
        # threshold because previously excluded gear was never retained.
        self._request_seed("gear rarity changed", delay=0.10)
        self._next_service_time = time.monotonic()

    def on_reset_defaults(self) -> None:
        if not self._runtime_enabled:
            return
        self._options_dirty = True
        self._request_seed("reset to defaults", delay=0.10)
        self._next_service_time = time.monotonic()


    def _apply_frequency(self, raw_value) -> int:
        try:
            hz = int(raw_value)
        except (TypeError, ValueError, OverflowError):
            hz = _DEFAULT_FREQUENCY_HZ
        hz = max(_MIN_FREQUENCY_HZ, min(_MAX_FREQUENCY_HZ, hz))
        self._pickup_frequency_hz = hz
        self._service_interval = 1.0 / float(hz)
        return hz

    def _apply_radius_percent(self, raw_value) -> int:
        try:
            value = int(raw_value)
        except (TypeError, ValueError, OverflowError):
            value = _DEFAULT_RADIUS_PERCENT
        value = max(_MIN_RADIUS_PERCENT, min(_MAX_RADIUS_PERCENT, value))
        self._radius_percent = value
        self._radius_multiplier = float(value) / 100.0
        return value

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _install_hooks(self) -> None:
        if self._hooks_installed:
            return
        if self._installed_hooks or self._uncertain_hooks:
            raise RuntimeError('AutoPickup hook cleanup is unresolved; restart SDK if ownership is ambiguous')

        hooks = unrealsdk.hooks
        hook_specs = (
            (
                self._FN_INVENTORY,
                hooks.Type.POST,
                self._HOOK_INVENTORY,
                self._on_inventory_associated,
            ),
            (
                self._FN_TRAVEL,
                hooks.Type.PRE,
                self._HOOK_TRAVEL,
                self._on_pre_client_travel,
            ),
            (
                self._FN_LOADED,
                hooks.Type.PRE,
                self._HOOK_LOADED,
                self._on_notify_loaded_world,
            ),
            (
                self._FN_TICK,
                hooks.Type.PRE,
                self._HOOK_TICK,
                self._on_player_tick,
            ),
            (
                "WillowGame.WillowPlayerController:ServerTeleportPlayerToStation",
                hooks.Type.PRE,
                "AutoPickup.FastTravelRequest",
                self._on_fast_travel_request,
            ),
            (
                "WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie",
                hooks.Type.POST_UNCONDITIONAL,
                "AutoPickup.LoadingComplete",
                self._on_loading_complete,
            ),
            (
                self._FN_PICKUP_MESSAGE,
                hooks.Type.PRE,
                self._HOOK_PICKUP_MESSAGE,
                self._on_auto_failed_message,
            ),
            (
                self._FN_FAILED_PICKUP,
                hooks.Type.PRE,
                self._HOOK_FAILED_PICKUP,
                self._on_auto_failed_pickup,
            ),
        )

        for function_name, hook_type, hook_id, callback in hook_specs:
            entry = (function_name, hook_type, hook_id)
            if entry in self._provisional_hooks:
                # A callback from an earlier successful-but-unconfirmed append
                # may be waiting for its first invocation. Do not overwrite or
                # remove the ID merely because it is still present on reenable.
                if hooks.has_hook(*entry):
                    continue
                self._provisional_hooks.pop(entry, None)
            if hooks.has_hook(*entry):
                raise RuntimeError('AutoPickup hook ID already registered: {}'.format(hook_id))

            def witnessed(obj, args, ret, func, *, _entry=entry, _callback=callback):
                if not self._hooks_installed:
                    return None
                if _entry in self._provisional_hooks:
                    # This invocation proves that the SDK installed *our*
                    # callback; ID presence alone could be a foreign race.
                    self._provisional_hooks.pop(_entry, None)
                    self._installed_hooks.append(_entry)
                return _callback(obj, args, ret, func)

            try:
                added = hooks.add_hook(function_name, hook_type, hook_id, witnessed)
            except Exception:
                # A hook may have been installed before the exception. Only an
                # absent ID proves that a clean retry cannot remove foreign code.
                try:
                    still_present = hooks.has_hook(*entry)
                except Exception:
                    still_present = True  # Unable to verify: fail closed.
                if still_present:
                    self._uncertain_hooks.append(entry)
                raise
            if added is True:
                self._installed_hooks.append(entry)
                continue
            # UnrealSDK 3.2.0 returns False even after a successful append on
            # an existing function/stage list. Verify existence, but require
            # our own callback to execute before taking removal ownership.
            try:
                present = hooks.has_hook(*entry)
            except Exception:
                self._uncertain_hooks.append(entry)
                raise
            if not present:
                raise RuntimeError('AutoPickup hook registration rejected: {}'.format(hook_id))
            self._provisional_hooks[entry] = witnessed

        self._hooks_installed = True

    def _remove_hooks(self) -> None:
        hooks = unrealsdk.hooks
        pending = []
        for entry in reversed(self._installed_hooks):
            function_name, hook_type, hook_id = entry
            try:
                removed = hooks.remove_hook(*entry)
                exists = hooks.has_hook(*entry)
            except Exception as exc:
                _log_exception('removing hook {}'.format(hook_id), exc)
                # A thrown remove can be a transient failure. Retain the
                # ledger only while the ID is still present, for later cleanup.
                pending.append(entry)
                continue
            if exists:
                if removed is True:
                    # A replacement hook now uses this ID; do not remove it.
                    self._uncertain_hooks.append(entry)
                    _log('hook ID still present after removal: {}'.format(hook_id))
                else:
                    pending.append(entry)
                    _log('hook removal unconfirmed: {}'.format(hook_id))
        self._installed_hooks = list(reversed(pending))
        self._hooks_installed = False

    def _on_auto_failed_message(self, controller, _args, _ret, _func):
        if self._runtime_enabled and self._auto_attempt_controller and (self._object_address(controller) == self._auto_attempt_controller):
            return unrealsdk.hooks.Block
        return None

    def _on_auto_failed_pickup(self, pickup, _args, _ret, _func):
        if self._runtime_enabled and self._auto_attempt_pickup and (self._object_address(pickup) == self._auto_attempt_pickup):
            return unrealsdk.hooks.Block
        return None

    def _on_inventory_associated(self, pickup, _args, _ret, _func) -> None:
        return self._on_inventory_associated_impl(pickup, _args, _ret, _func)

    def _on_inventory_associated_impl(self, pickup, _args, _ret, _func) -> None:
        if not self._runtime_enabled or self._traveling:
            return
        try:
            self._register_pickup(pickup)
        except Exception as exc:
            _log_exception("registering pickup", exc)

    def _on_pre_client_travel(self, controller, _args, _ret, _func) -> None:
        if (not self._runtime_enabled or self._traveling
                or not self._local_travel_controller(controller)):
            # Ignore remote controllers and duplicate transition notifications.
            return

        self._clear_runtime_state(clear_definition_cache=True)
        self._seed_requested = False
        self._seed_reason = ""
        self._seed_not_before = 0.0
        self._traveling = True
        self._final_world_loaded = False
        self._travel_resume_after = 0.0
        self._next_service_time = float("inf")


    def _on_notify_loaded_world(self, controller, args, _ret, _func) -> None:
        if (not self._runtime_enabled or not self._traveling
                or not self._local_travel_controller(controller)):
            return

        try:
            if not bool(args.bFinalDest):
                return
        except Exception as exc:
            _log_exception("reading NotifyLoadedWorld.bFinalDest", exc)
            return

        self._mark_final_world_loaded()

    def _local_travel_controller(self, controller) -> bool:
        try:
            address = self._object_address(controller)
            return bool(address and address == self._object_address(get_pc(possibly_loading=True)))
        except Exception:
            return False

    def _on_fast_travel_request(self, controller, args, ret, func) -> None:
        # Native station travel can replace map streaming without PreClientTravel.
        self._on_pre_client_travel(controller, args, ret, func)

    def _on_loading_complete(self, controller, _args, _ret, _func) -> None:
        if not self._runtime_enabled or not self._local_travel_controller(controller):
            return
        if not self._traveling:
            # Also reconcile streaming arrivals that had no departure callback.
            self._clear_runtime_state(clear_definition_cache=True)
            self._request_seed("loading complete", delay=0.25)
            return
        self._mark_final_world_loaded()

    def _mark_final_world_loaded(self) -> None:
        if self._final_world_loaded:
            return

        self._clear_candidates()
        self._final_world_loaded = True
        self._travel_resume_after = time.monotonic() + self._POST_LOAD_STABILIZE_SECONDS


    def _on_player_tick(self, controller, _args, _ret, _func) -> None:
        return self._on_player_tick_impl(controller, _args, _ret, _func)

    def _on_player_tick_impl(self, controller, _args, _ret, _func) -> None:
        if not self._runtime_enabled or not self._local_travel_controller(controller):
            return

        now = time.monotonic()

        if self._traveling:
            if not self._final_world_loaded or now < self._travel_resume_after:
                return

            try:
                if not bool(controller.HasClientLoadedCurrentWorld()):
                    return
            except Exception:
                return

            if self._get_inventory_pawn(controller) is None:
                return

            self._clear_candidates()
            self._traveling = False
            self._final_world_loaded = False
            self._travel_resume_after = 0.0
            self._next_service_time = now + self._service_interval
            self._request_seed(
                "map arrival",
                now=now,
                delay=self._POST_RESUME_SEED_DELAY_SECONDS,
            )
            return

        if self._options_dirty:
            self._options_dirty = False
            self._prune_disabled_or_filtered_candidates()

        if self._seed_requested and now >= self._seed_not_before:
            # Perform this only when the world/controller is demonstrably usable.
            if self._get_inventory_pawn(controller) is not None:
                self._run_seed_if_requested(controller, now)

        if now < self._next_service_time:
            return

        self._next_service_time = now + self._service_interval
        if not self._candidates:
            return

        try:
            self._service_candidates(now, controller)
        except Exception as exc:
            _log_exception("servicing candidates", exc)

    # ------------------------------------------------------------------
    # One-time lifecycle seed
    # ------------------------------------------------------------------

    def _request_seed(self, reason: str, *, now: float | None = None, delay: float = 0.0) -> None:
        if not self._runtime_enabled:
            return
        if now is None:
            now = time.monotonic()
        requested_at = now + max(0.0, float(delay))
        if not self._seed_requested or requested_at < self._seed_not_before:
            self._seed_not_before = requested_at
        self._seed_requested = True
        self._seed_reason = reason
        # A new explicit request supersedes any retries of an older seed.
        self._seed_retry_count = 0
        self._seed_retry_world = None

    def _run_seed_if_requested(self, controller, now: float) -> None:
        if not self._seed_requested or self._traveling or now < self._seed_not_before:
            return
        try:
            world = controller.WorldInfo
        except Exception:
            world = None
        world_address = self._object_address(world)
        # Do not retry a scan without an identifiable world. Its next valid
        # lifecycle event will request a fresh seed instead.
        if not world_address:
            self._seed_requested = False
            self._seed_reason = ''
            self._seed_not_before = 0.0
            return
        if self._seed_retry_world is not None:
            try:
                previous = self._seed_retry_world()
            except Exception:
                previous = None
            if previous is None or self._object_address(previous) != world_address:
                # The old world is gone. Never carry its retry into a new one.
                self._seed_requested = False
                self._seed_reason = ''
                self._seed_not_before = 0.0
                self._seed_retry_count = 0
                self._seed_retry_world = None
                return
        reason = self._seed_reason or 'lifecycle'
        epoch = self._seed_epoch
        self._seed_requested = False
        self._seed_reason = ''
        self._seed_not_before = 0.0
        try:
            registered, inspected = self._seed_current_world(controller)
            self._seed_retry_count = 0
            self._seed_retry_world = None
            _log('seeded {} eligible pickup(s) from {} current-world pickup(s); reason={}'.format(
                registered, inspected, reason))
        except Exception as exc:
            _log_exception('one-time current-world seed', exc)
            if (not self._runtime_enabled or self._traveling or self._seed_epoch != epoch
                    or self._seed_requested or self._seed_retry_count >= 2):
                return
            # Confirm the world remained the same across a failed enumeration.
            try:
                if self._object_address(controller.WorldInfo) != world_address:
                    return
                if self._seed_retry_world is None:
                    self._seed_retry_world = unrealsdk.unreal.WeakPointer(world)
                if self._seed_retry_world() is None:
                    return
            except Exception:
                return
            self._seed_retry_count += 1
            self._seed_requested = True
            self._seed_reason = reason
            self._seed_not_before = now + (0.5 if self._seed_retry_count == 1 else 1.0)

    def _seed_current_world(self, controller) -> tuple[int, int]:
        """
        One-time synchronization scan of the current loaded world only.

        find_all is deliberately not used as a service mechanism. Every object is
        filtered by WorldInfo before any pickup-native function is called, preventing
        CDOs/archetypes/objects from other worlds from reaching classification.
        """
        try:
            world = controller.WorldInfo
        except Exception:
            return (0, 0)

        world_address = self._object_address(world)
        if not world_address:
            return (0, 0)

        registered = 0
        inspected = 0

        actor_failures = 0
        first_actor_error = None
        for pickup in unrealsdk.find_all("WillowPickup", exact=True):
            if self._traveling or not self._runtime_enabled:
                break
            if pickup is None:
                continue

            # Passive identity checks must precede all pickup-native calls.
            # One actor's reflected getter must not hide later valid pickups.
            try:
                if self._is_deleted(pickup):
                    continue
                pickup_world = pickup.WorldInfo
                if self._object_address(pickup_world) != world_address:
                    continue
                inspected += 1
                if self._register_pickup(pickup):
                    registered += 1
            except Exception as exc:
                actor_failures += 1
                if first_actor_error is None:
                    first_actor_error = exc
        if first_actor_error is not None:
            _log_exception('current-world seed skipped {} failing pickup(s)'.format(
                actor_failures), first_actor_error)

        return (registered, inspected)

    # ------------------------------------------------------------------
    # Candidate registration / classification
    # ------------------------------------------------------------------

    def _register_pickup(self, pickup) -> bool:
        return self._register_pickup_impl(pickup)

    def _register_pickup_impl(self, pickup) -> bool:
        if pickup is None or self._is_deleted(pickup):
            return False
        address = self._object_address(pickup)
        if not address:
            return False
        if not bool(PickupSelfDroppedGear.value) and self._is_player_dropped_pickup(pickup):
            self._forget_candidate(address)
            return False
        if self._is_mission_director(pickup):
            self._forget_candidate(address)
            return False
        definition = pickup.GetPickupableInventoryDefinition()
        if definition is None:
            return False
        identity = self._definition_identity(definition)
        if not identity:
            return False
        if self._is_mission_item(pickup) or self._is_mission_definition(definition):
            self._forget_candidate(address)
            return False
        cached = self._definition_cache.get(identity, self._CACHE_MISS)
        if cached is self._CACHE_MISS:
            category = self._classify_definition(definition, identity)
            self._definition_cache[identity] = category
        else:
            category = cached
        if IS_TPS and not loot_rules.tps_definition_allowed(identity, self._uobject_class_name(definition), category):
            self._forget_candidate(address)
            return False
        if category is None or not self._category_enabled(category):
            self._forget_candidate(address)
            return False
        rarity_level = self._pickup_rarity_level(pickup)
        if not self._rarity_allows(category, rarity_level):
            self._forget_candidate(address)
            return False
        try:
            pointer = unrealsdk.unreal.WeakPointer(pickup)
        except Exception as exc:
            _log_exception('creating pickup weak pointer', exc)
            return False
        previous = self._candidates.get(address)
        retry_at = 0.0
        if previous is not None and previous[1] == category:
            try:
                old_pickup = previous[0]()
                if old_pickup is not None and (not self._is_deleted(old_pickup)):
                    retry_at = previous[2]
            except Exception:
                pass
        self._candidates[address] = (pointer, category, retry_at, rarity_level)
        return True

    def _classify_definition(self, definition, identity: str):
        """Read reflected metadata only after cheaper intrinsic checks fail."""
        kind = loot_rules.classify_path(identity)
        if kind is not loot_rules.UNCLASSIFIED:
            return kind
        kind = self._GEAR_CLASS_CATEGORIES.get(self._uobject_class_name(definition))
        if kind is not None:
            return kind
        return self._CATEGORY_OTHER if self._definition_is_automatically_picked_up(definition) else None

    @staticmethod
    def _is_player_dropped_pickup(pickup) -> bool:
        """Return True for gear intentionally thrown from a player's backpack."""
        try:
            inventory = pickup.GetPickupableInventory()
            if inventory is None:
                return False
            return int(inventory.ItemLocation) == _PLAYER_DROPPED_ITEM_LOCATION
        except Exception:
            return False


    @staticmethod
    def _definition_identity(definition) -> str:
        try:
            value = definition._path_name()
            if value:
                return str(value).lower()
        except Exception:
            pass
        try:
            return str(definition).lower()
        except Exception:
            return ""

    @classmethod
    def _is_mission_definition(cls, definition) -> bool:
        return cls._uobject_class_name(definition) == "missionitemdefinition"

    @staticmethod
    def _uobject_class_name(obj) -> str:
        try:
            name = obj.Class.Name
            if name:
                return str(name).lower()
        except Exception:
            pass
        return ""

    @staticmethod
    def _definition_is_automatically_picked_up(definition) -> bool:
        try:
            return bool(definition.bAutomaticallyPickup)
        except Exception:
            return False

    @staticmethod
    def _pickup_rarity_level(pickup) -> int:
        try:
            return max(0, int(pickup.InventoryRarityLevel))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return 0

    @staticmethod
    def _is_mission_item(pickup) -> bool:
        try:
            return bool(pickup.bIsMissionItem)
        except Exception:
            return True

    @staticmethod
    def _is_mission_director(pickup) -> bool:
        try:
            return bool(pickup.bIsMissionDirector)
        except Exception:
            return True

    @staticmethod
    def _is_deleted(pickup) -> bool:
        try:
            return bool(pickup.bDeleteMe)
        except Exception:
            return True

    @staticmethod
    def _object_address(obj) -> int:
        try:
            return int(obj._get_address())
        except Exception:
            return 0

    def _prune_disabled_or_filtered_candidates(self) -> None:
        for address, entry in tuple(self._candidates.items()):
            try:
                pointer, category, _retry_at, rarity_level = entry
            except Exception:
                self._forget_candidate(address)
                continue
            if not self._category_enabled(category) or not self._rarity_allows(category, rarity_level):
                self._forget_candidate(address)
                continue
            if not bool(PickupSelfDroppedGear.value):
                pickup = pointer()
                if pickup is None or self._is_player_dropped_pickup(pickup):
                    self._forget_candidate(address)

    # ------------------------------------------------------------------
    # Bounded service loop
    # ------------------------------------------------------------------

    def _service_candidates(self, now: float, controller) -> None:
        pawn = self._get_inventory_pawn(controller)
        if pawn is None:
            return
        if IS_TPS:
            try:
                # TPS GrinderGFxMovie sets this while its storage is open.
                if controller.bUsingGrinder:
                    return
            except (AttributeError, RuntimeError, TypeError):
                return
        network_mode = pickup_network_mode(controller)
        if network_mode is None:
            return
        is_client = network_mode == 'client'
        if is_client:
            if now - self._client_window_started >= 1.0:
                self._client_window_started = now
                self._client_requests_sent = 0
            if self._client_requests_sent >= self._MAX_CLIENT_REQUESTS_PER_SECOND:
                return
        pickup_range = self._get_effective_pickup_range(controller)
        if pickup_range <= 0.0:
            return
        try:
            origin = pawn.Location
            origin_x = float(origin.X)
            origin_y = float(origin.Y)
            origin_z = float(origin.Z)
        except Exception:
            return
        range_sq = pickup_range * pickup_range
        backpack_slots = capacity_guard.empty_backpack_slots(controller)
        exclude_self_drops = not bool(PickupSelfDroppedGear.value)
        entries = tuple(self._candidates.items())
        for address, entry in entries:
            if is_client and self._client_requests_sent >= self._MAX_CLIENT_REQUESTS_PER_SECOND:
                break
            current = self._candidates.get(address)
            if current is not entry:
                continue
            pointer, category, retry_at, rarity_level = entry
            if now < retry_at:
                continue
            pickup = pointer()
            if pickup is None:
                self._forget_candidate_if_current(address, entry)
                continue
            try:
                if self._is_deleted(pickup):
                    self._forget_candidate_if_current(address, entry)
                    continue
                location = pickup.Location
                dx = float(location.X) - origin_x
                dy = float(location.Y) - origin_y
                dz = float(location.Z) - origin_z
                if dx * dx + dy * dy + dz * dz > range_sq:
                    continue
                if exclude_self_drops and self._is_player_dropped_pickup(pickup):
                    self._forget_candidate_if_current(address, entry)
                    continue
                if self._is_mission_director(pickup) or self._is_mission_item(pickup):
                    self._forget_candidate_if_current(address, entry)
                    continue
                if not self._category_enabled(category) or not self._rarity_allows(category, rarity_level):
                    self._forget_candidate_if_current(address, entry)
                    continue
                if not pickup.Pickupable_IsEnabled():
                    if self._candidates.get(address) is entry:
                        self._candidates[address] = (pointer, category, now + self._FAILED_RETRY_SECONDS, rarity_level)
                    continue
                if IS_TPS:
                    try:
                        definition = pickup.GetPickupableInventoryDefinition()
                        identity = self._definition_identity(definition) if definition is not None else ''
                        if (definition is None or self._is_mission_definition(definition)
                                or not loot_rules.tps_definition_allowed(identity, self._uobject_class_name(definition), category)
                                or pickup.bIsPickupAttachedToSomething or pickup.bCostsToPickUp
                                or pickup.Owner is not None or pickup.Base is not None or pickup.Instigator is not None):
                            continue
                        if category in self._GEAR_CATEGORIES:
                            if not pickup.bPickupAtRest:
                                continue
                        elif (not bool(definition.bAutomaticallyPickup)
                              or not bool(definition.ShouldPlayerAutomaticallyPickup(controller))):
                            continue
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        continue
                blocked, _ = capacity_guard.reject_for_capacity(category, self._GEAR_CATEGORIES, controller, pawn, pickup, backpack_slots=backpack_slots)
                if blocked:
                    continue
                self._auto_attempt_controller = self._object_address(controller)
                self._auto_attempt_pickup = address
                try:
                    if is_client:
                        self._client_requests_sent += 1
                        controller.ServerPickupSpecific(pickup)
                    else:
                        controller.PickupPickupable(pickup, False)
                finally:
                    self._auto_attempt_controller = 0
                    self._auto_attempt_pickup = 0
                pickup_after = pointer()
                if pickup_after is None or self._is_deleted(pickup_after):
                    self._forget_candidate_if_current(address, entry)
                    continue
                if self._candidates.get(address) is entry:
                    self._candidates[address] = (pointer, category, now + self._FAILED_RETRY_SECONDS, rarity_level)
            except Exception as exc:
                self._forget_candidate_if_current(address, entry)
                _log_exception('servicing one pickup', exc)

    def _category_enabled(self, category: str) -> bool:
        if category == self._CATEGORY_AMMO:
            return bool(PickupAmmo.value)
        if category == self._CATEGORY_CURRENCY:
            return bool(PickupCurrency.value)
        if category == self._CATEGORY_TORGUE:
            return not IS_TPS and bool(PickupTorgueTokens.value)
        if category == self._CATEGORY_SERAPH:
            return not IS_TPS and bool(PickupSeraphCrystals.value)
        if category == self._CATEGORY_OTHER:
            return bool(PickupOtherConsumables.value)
        if category == self._CATEGORY_WEAPON:
            return bool(PickupWeapons.value)
        if category == self._CATEGORY_SHIELD:
            return bool(PickupShields.value)
        if category == self._CATEGORY_GRENADE_MOD:
            return bool(PickupGrenadeMods.value)
        if category == self._CATEGORY_CLASS_MOD:
            return bool(PickupClassMods.value)
        if category == self._CATEGORY_RELIC:
            return bool(PickupRelics.value)
        return False

    def _rarity_allows(self, category: str, rarity_level: int) -> bool:
        if category not in self._GEAR_CATEGORIES:
            return True
        threshold = self._RARITY_THRESHOLDS.get(MinimumGearRarity.value, 0)
        return int(rarity_level) >= int(threshold)

    @staticmethod
    def _get_inventory_pawn(controller):
        try:
            pawn = controller.GetInventoryPawn()
            if pawn is not None:
                return pawn
        except Exception:
            pass
        try:
            return controller.Pawn
        except Exception:
            return None

    def _get_effective_pickup_range(self, controller) -> float:
        native_range = self._get_native_pickup_range(controller)
        if native_range <= 0.0:
            return 0.0
        return native_range * self._radius_multiplier

    @staticmethod
    def _get_native_pickup_range(controller) -> float:
        try:
            globals_obj = controller.GetWillowGlobals()
            if globals_obj is None:
                return 0.0
            globals_definition = globals_obj.GetGlobalsDefinition()
            if globals_definition is None:
                return 0.0
            return max(0.0, float(globals_definition.PlayerInteractionDistance))
        except Exception:
            return 0.0

    def _forget_candidate_if_current(self, address: int, entry) -> None:
        # Native queries/calls may reenter registration at the same address.
        # A service snapshot must never remove a newer candidate or its diagnostics.
        if self._candidates.get(address) is entry:
            self._forget_candidate(address)

    def _forget_candidate(self, address: int) -> None:
        self._candidates.pop(address, None)

    def _clear_candidates(self) -> None:
        self._candidates.clear()

    def _clear_runtime_state(self, clear_definition_cache: bool) -> None:
        self._seed_epoch += 1
        self._seed_retry_count = 0
        self._seed_retry_world = None
        self._clear_candidates()
        if clear_definition_cache:
            self._definition_cache.clear()
        self._next_service_time = 0.0
        self._client_window_started = 0.0
        self._client_requests_sent = 0
        self._auto_attempt_controller = 0
        self._auto_attempt_pickup = 0

    @staticmethod
    def _on_off(value) -> str:
        return "on" if bool(value) else "off"


class AutoPickupMod(Mod):
    """Show one keyboard/controller-compatible keybind without a nested header.

    Willow2 already supplies a Keybinds heading on the Key Bindings screen.
    Base Mod.iter_display_options() wraps all bindings in another `Keybinds`
    GroupedOption, leaving an extra copy of the action in the installed menu.
    Present the stock KeybindOption once at top level. This does not alter the
    actual KeybindType object, keyboard input dispatch, or controller bind IDs.
    """

    def toggle_for_session(self) -> None:
        """Change runtime Enabled only; preserve the SDK menu's saved preference."""
        if getattr(self, "_session_enabled_preference", None) is None:
            # A previous failed auto-enable may leave the runtime disabled even
            # while the persisted menu preference is On. Trust the saved flag
            # when one exists, not the live state in that case.
            preference = bool(self.is_enabled)
            if self.settings_file is not None:
                try:
                    saved = json.loads(self.settings_file.read_text(encoding="utf-8"))
                    if isinstance(saved, dict) and type(saved.get("enabled")) is bool:
                        preference = saved["enabled"]
                except (OSError, ValueError):
                    pass
            self._session_enabled_preference = preference

        self._quick_toggle_in_progress = True
        try:
            if self.is_enabled:
                # Native Mods Base has a dont_update_setting switch for Off.
                self.disable(dont_update_setting=True)
            else:
                # Native Mods Base 1.12 has NO symmetric argument for On.
                # save_settings() below suppresses the implicit write.
                self.enable()
        finally:
            self._quick_toggle_in_progress = False

    def disable(self, dont_update_setting: bool = False) -> None:
        """Only deliberate menu disables replace the saved startup preference."""
        if (self.is_enabled and not dont_update_setting
                and not getattr(self, "_quick_toggle_in_progress", False)):
            # A Willow2 Enabled-row refresh may invoke disable() after we
            # already switched Off. A no-op is not an intentional menu edit.
            self._session_enabled_preference = None
        super().disable(dont_update_setting=dont_update_setting)

    def save_settings(self) -> None:
        """Save category/keybind edits without persisting a session-only toggle.

        Mods Base writes 'enabled' on EVERY settings save, not just on enable.
        Keep the user's persistent menu choice when another option is saved
        while the live runtime state differs. Do not mutate option values or
        leave the SDK Enabled state changed after serialization.
        """
        if getattr(self, "_quick_toggle_in_progress", False):
            return
        preference = getattr(self, "_session_enabled_preference", None)
        if preference is None:
            super().save_settings()
            return
        runtime_enabled = self.is_enabled
        try:
            self.is_enabled = preference
            super().save_settings()
        finally:
            self.is_enabled = runtime_enabled

    def enable(self) -> None:
        """Undo SDK Enabled state if the runtime cannot install its hooks.

        mods_base sets is_enabled before invoking on_enable. A callback error
        must not leave the menu claiming that AutoPickup is running.
        """
        if (not self.is_enabled
                and not getattr(self, "_quick_toggle_in_progress", False)):
            # A real menu-originated enable replaces the saved preference.
            # Ignore a duplicate Enabled-row refresh when already active.
            self._session_enabled_preference = None
        try:
            super().enable()
        except Exception as exc:
            if self.is_enabled:
                try:
                    self.disable(dont_update_setting=True)
                except Exception as cleanup_exc:
                    _log_exception("cleaning up failed activation", cleanup_exc)
            _log_exception("activating AutoPickup", exc)

    def iter_display_options(self):
        """Group visible options without changing persisted settings or keybinds."""
        yield GroupedOption("Pickup Behavior", [ResetDefaults, PickupFrequency, PickupRadiusPercent])
        yield GroupedOption("Equipment", [
            MinimumGearRarity, PickupWeapons, PickupShields, PickupGrenadeMods,
            PickupClassMods, PickupRelics, PickupSelfDroppedGear,
        ])
        yield GroupedOption("Consumables & Currency", [
            PickupAmmo, PickupCurrency, PickupTorgueTokens, PickupSeraphCrystals,
            PickupOtherConsumables,
        ])
        for bind in self.keybinds:
            if not bind.is_hidden:
                yield KeybindOption.from_keybind(bind)


_migrate_legacy_settings()
_cleanup_obsolete_settings()
_RUNTIME = AutoPickupRuntime()

_MOD = build_mod(
    cls=AutoPickupMod,
    name=_MOD_NAME,
    author=__author__,
    description=(
        "Collects eligible world drops automatically, with optional equipment pickup. "
        "Does not open containers or collect protected mission items."
    ),
    version=__version__,
    supported_games=Game.BL2 | Game.TPS,
    settings_file=_SETTINGS_FILE,
    options=_OPTIONS,
    keybinds=[ToggleAutoPickup],
    auto_enable=True,
    on_enable=_RUNTIME.enable,
    on_disable=_RUNTIME.disable,
)

# The mod might load with a persisted Disabled state. The quick-toggle keybind
# must still be callable in that case; enabling the mod normally also registers
# it, so the helper is idempotent under the SDK's keybind implementation.
_ensure_toggle_keybind_available()
