"""Atomic, idempotent legacy import and targeted settings cleanup."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile


def atomic_write_json(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=4)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def migrate_legacy_settings(settings_file, legacy_settings_file, known_keys, log, log_exception):
    """Copy legacy local settings into the modern settings format once."""
    if settings_file.exists() or not legacy_settings_file.exists():
        return

    try:
        with legacy_settings_file.open("r", encoding="utf-8") as file:
            old = json.load(file)
        if not isinstance(old, dict):
            return

        # A previous development build may already have modern-format data locally.
        if isinstance(old.get("options"), dict):
            modern = {
                "enabled": bool(old.get("enabled", False)),
                "options": dict(old["options"]),
            }
        else:
            old_options = old.get("Options", {})
            if not isinstance(old_options, dict):
                old_options = {}

            known = known_keys
            modern_options = {
                key: value for key, value in old_options.items() if key in known
            }
            modern = {
                "enabled": bool(old.get("AutoEnable", False)),
                "options": modern_options,
            }

        settings_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(settings_file, modern)
        log("migrated legacy settings to the modern menu format")
    except Exception as exc:
        log_exception("migrating legacy settings", exc)


def cleanup_obsolete_settings(settings_file, deprecated_keys, log, log_exception):
    """Remove retired beta option keys without changing remaining preferences."""
    if not settings_file.is_file():
        return
    try:
        with settings_file.open("r", encoding="utf-8") as file:
            saved = json.load(file)
        if not isinstance(saved, dict) or not isinstance(saved.get("options"), dict):
            return
        options = saved["options"]
        retired = deprecated_keys.intersection(options)
        if not retired:
            return
        for name in retired:
            options.pop(name, None)
        # Preserve all other values, including options not recognized by this build.
        # Atomic replacement avoids truncating the player's file during a crash.
        atomic_write_json(settings_file, saved)
        log("removed retired mission-item setting from saved options")
    except Exception as exc:
        # Settings cleanup must not stop the mod from loading.
        log_exception("cleaning obsolete settings", exc)


# One-time numbered-settings filename migration; distinct from JSON schema migration.
def migrate_settings(settings_dir):
    root = Path(settings_dir)
    old = root / "5AutoPickup.json"
    new = root / "AutoPickup.json"
    if new.exists() or not old.is_file():
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".AutoPickup.", suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            target.write(old.read_bytes())
            target.flush()
            os.fsync(target.fileno())
        if not new.exists():
            os.replace(temporary, new)
            temporary = None
    except OSError:
        pass  # Read-only settings must not block mod loading.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
