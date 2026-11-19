# Auto Pickup 1.0.0

Automatically collects nearby eligible ammo, currency, and consumables in Borderlands 2. Equipment pickup is optional and **Off by default**. Stock inventory checks and identified protected mission-item exclusions remain in place.

## Install or update

1. Exit the game. Back up `sdk_mods/AutoPickup/` and saved SDK settings such as `AutoPickup.json`.
2. Extract the matching **Player ZIP** into `Borderlands 2/sdk_mods/`, replacing the old folder. Remove duplicate/numbered installations; do not use GitHub's source archive.
3. Confirm `sdk_mods/AutoPickup/__init__.py`, restart the game, and review options in the SDK Mods menu.

Restore your folder and settings backup to roll back.

## Settings and quick toggle

- **Pickup Behavior:** Frequency 5 Hz and radius 100% by default.
- **Equipment:** Gear categories and self-dropped gear Off by default; minimum rarity Any.
- **Consumables & Currency:** Supported resource categories On by default.
- **Toggle Auto Pickup:** Optional keyboard/controller keybind, unbound by default. Changes live Enabled state for this session; the SDK Mods Enabled option controls the saved startup preference.

The game's normal pickup remains available. Capacity limits and excluded items can prevent collection.

## Compatibility and safety

Windows BL2 single-player is the primary Player target. Co-op joining clients and TPS are experimental and not fully verified; Proton/Wine is unverified; native Linux BL2 is unsupported by this package.

**TPS:** Avoid Grinder, chest, and scripted reward sequences. Scripted origin cannot always be identified once a pickup is ownerless.

For more detail and troubleshooting, see the [Auto Pickup user guide](https://github.com/PeytonBlakeslee/AutoPickup/blob/main/docs/USER_GUIDE.md).
