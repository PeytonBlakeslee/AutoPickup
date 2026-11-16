# Auto Pickup: User Guide

[Back to the repository overview](../README.md)

**Contents:** [Install, update and rollback](#install-update-and-rollback) · [Settings and defaults](#settings-and-defaults) · [Quick toggle](#quick-toggle) · [Compatibility and safety](#compatibility-and-safety) · [Troubleshooting](#troubleshooting)

## Install, update and rollback

1. Confirm PythonSDK and Willow2 Mod Manager load normally in the Windows BL2 game.
2. Exit the game and back up `sdk_mods/AutoPickup/` and SDK-managed settings.
3. Remove any old numbered Auto Pickup mod directory. Keep only one `sdk_mods/AutoPickup/`.
4. Extract a matching **Player ZIP** so `sdk_mods/AutoPickup/__init__.py` exists. Do not install `mod/AutoPickup/` nested inside another folder or mix branches.
5. Launch BL2 and check previous settings, Enabled state and the quick-toggle assignment. To roll back, exit the game and restore the matching folder and settings backups.

The mod migrates recognized older settings into `AutoPickup.json` without deliberately overwriting existing modern preferences. Back up settings before upgrading.

## Settings and defaults

Options appear in this order:

- **Pickup Behavior:** Reset to Defaults, Pickup Frequency (default **5 Hz**, range 1–20), and Pickup Radius (default **100%**, range 100–500%). Reset does not clear the keybind.
- **Equipment:** Minimum Gear Rarity (default **Any**), weapons, shields, grenade mods, class mods, and relics/Oz Kits. All equipment categories and **Pick Up Self-Dropped Gear** default to **Off**. Rarity applies only to gear.
- **Consumables & Currency:** Ammo, money/Eridium, DLC currencies, and supported health/consumables. Available options vary by game and default to **On**.

The mod does not open containers or collect identified protected mission items. Full inventory or capacity checks can leave eligible items on the ground. Failed pickups retry at a bounded rate.

## Quick toggle

**Toggle Auto Pickup** is a standard rebindable SDK keybind, **unbound by default**.

1. Assign a keyboard key in the SDK Mods keybind menu, or use a compatible controller-binding mod to assign a controller shortcut.
2. Press the shortcut to change Auto Pickup's live Enabled state.
3. Use the normal SDK Mods **Enabled** option if you want to change the saved startup preference.

The shortcut switches the current session only, without changing category preferences or the game's regular pickup controls. Some controller-binding mods may not dispatch shortcuts to disabled mods.

## Compatibility and safety

The joining-client path uses bounded stock server pickup requests, with no direct client inventory grant on failure. It still requires an actual host/client test, including a vanilla host; do not describe it as fully co-op-certified.

TPS is present in the source as **experimental**. Ordinary recognized resources and selected oxygen/health pickups can be considered; equipment including Oz Kits is opt-in. Unknown/scripted items, attached/owned/instigated or priced drops, and uncertain special definitions are excluded. The Grinder UI pauses eligibility, but the mod cannot prove that every ownerless reward came from a scripted machine. Avoid using automatic pickup during Grinder, chest or mission-reward sequences. TPS eligibility and provenance handling are not fully tested.

Windows BL2 through Proton/Wine is not verified. Native Linux BL2 is not supported by this installer.

## Troubleshooting

**Missing mod:** the path must be `sdk_mods/AutoPickup/__init__.py`, with no duplicate numbered folder. Confirm PythonSDK/Willow2 are present.

**Pickup skipped:** inspect category, rarity, radius, inventory capacity and whether the item is scripted or still owned. Compare once with the mod disabled. Client pickup requests require host approval and may fail without bypassing authority.

**Settings lost:** exit BL2 and restore the settings backup; do not mix old and new settings files or manually rename legacy identifiers.

[Report an issue](https://github.com/PeytonBlakeslee/AutoPickup/issues) with game/SDK/mod versions, options, role, OS, reproduction steps and a relevant short SDK log excerpt. Redact names, paths, account data and save information.
