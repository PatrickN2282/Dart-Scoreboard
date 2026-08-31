"""Small, allow-listed add-on manager for local Raspberry Pi integrations.

Only add-ons shipped in ``Addons/*/addon.json`` are considered.  Manifests may
install files below the current user's home directory and may control one
systemd user service.  No manifest value is ever executed through a shell.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ADDON_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@-]+\.service$")
ALLOWED_ACTIONS = frozenset({"install", "update", "start", "stop", "restart", "uninstall"})


class AddonError(RuntimeError):
    """A safe, user-displayable add-on management error."""


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AddonError(f"Ungültiges Add-on-Manifest {path.name}: {exc}") from exc

    required = {"id", "name", "version", "kind", "service", "files"}
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise AddonError(f"Add-on-Manifest {path} ist unvollständig.")
    if not ADDON_ID_RE.fullmatch(str(manifest["id"])):
        raise AddonError(f"Ungültige Add-on-ID in {path}.")
    if manifest["kind"] != "systemd-user":
        raise AddonError(f"Nicht unterstützter Add-on-Typ in {path}.")
    if not SERVICE_RE.fullmatch(str(manifest["service"])):
        raise AddonError(f"Ungültiger Service-Name in {path}.")
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        raise AddonError(f"Add-on {manifest['id']} enthält keine Dateien.")
    return manifest


def discover_addons(addons_dir: str | os.PathLike[str]) -> dict[str, dict[str, Any]]:
    """Return valid shipped service add-ons indexed by their stable ID."""
    root = Path(addons_dir).resolve()
    found: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return found
    for manifest_path in sorted(root.glob("*/addon.json")):
        manifest = _load_manifest(manifest_path)
        addon_id = manifest["id"]
        if addon_id in found:
            raise AddonError(f"Doppelte Add-on-ID: {addon_id}")
        manifest["_directory"] = str(manifest_path.parent.resolve())
        found[addon_id] = manifest
    return found


def _destination(home: Path, relative: str) -> Path:
    if not isinstance(relative, str) or relative.startswith(("/", "\\", "~")):
        raise AddonError("Add-on-Zielpfad muss relativ zum Benutzerverzeichnis sein.")
    destination = (home / relative).resolve()
    if destination != home and home not in destination.parents:
        raise AddonError("Add-on-Zielpfad verlässt das Benutzerverzeichnis.")
    return destination


def _validated_files(manifest: dict[str, Any], home: Path) -> list[tuple[Path, Path, int]]:
    source_root = Path(manifest["_directory"]).resolve()
    files: list[tuple[Path, Path, int]] = []
    for item in manifest["files"]:
        if not isinstance(item, dict) or not {"source", "destination", "mode"}.issubset(item):
            raise AddonError(f"Ungültiger Dateieintrag im Add-on {manifest['id']}.")
        source = (source_root / str(item["source"])).resolve()
        if source_root not in source.parents or not source.is_file():
            raise AddonError(f"Quelldatei fehlt oder liegt außerhalb des Add-ons: {item['source']}")
        destination = _destination(home, item["destination"])
        try:
            mode = int(str(item["mode"]), 8)
        except ValueError as exc:
            raise AddonError(f"Ungültiger Dateimodus für {item['source']}.") from exc
        if mode not in {0o600, 0o644, 0o700, 0o755}:
            raise AddonError(f"Nicht erlaubter Dateimodus für {item['source']}.")
        files.append((source, destination, mode))
    return files


def _missing_dependencies(manifest: dict[str, Any]) -> list[str]:
    missing = [
        command for command in manifest.get("requires_commands", [])
        if not isinstance(command, str) or not shutil.which(command)
    ]
    alternatives = manifest.get("requires_any_command", [])
    if alternatives and not any(isinstance(command, str) and shutil.which(command) for command in alternatives):
        missing.append(" oder ".join(str(command) for command in alternatives))
    return missing


def _systemctl(args: list[str], *, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("systemctl")
    if not executable:
        raise AddonError("systemctl ist auf diesem System nicht verfügbar.")
    try:
        result = subprocess.run(
            [executable, "--user", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AddonError(f"systemctl konnte nicht ausgeführt werden: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise AddonError(detail[-1][:300] if detail else "systemctl-Aktion fehlgeschlagen.")
    return result


def install_addon(manifest: dict[str, Any], home_dir: str | os.PathLike[str]) -> None:
    """Atomically copy allow-listed files, enable and restart the user service."""
    home = Path(home_dir).resolve()
    missing = _missing_dependencies(manifest)
    if missing:
        raise AddonError("Fehlende Abhängigkeiten: " + ", ".join(missing))
    files = _validated_files(manifest, home)
    for source, destination, mode in files:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            temporary.chmod(mode)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    _systemctl(["daemon-reload"])
    _systemctl(["enable", "--now", manifest["service"]])
    _systemctl(["restart", manifest["service"]])


def manage_addon(manifest: dict[str, Any], action: str, home_dir: str | os.PathLike[str]) -> None:
    """Execute one explicitly supported operation for a discovered add-on."""
    if action not in ALLOWED_ACTIONS:
        raise AddonError("Nicht erlaubte Add-on-Aktion.")
    if action in {"install", "update"}:
        install_addon(manifest, home_dir)
        return
    service = manifest["service"]
    if action in {"start", "stop", "restart"}:
        _systemctl([action, service])
        return

    # uninstall: exact destinations come from the validated bundled manifest.
    home = Path(home_dir).resolve()
    files = _validated_files(manifest, home)
    _systemctl(["disable", "--now", service], check=False)
    for _source, destination, _mode in files:
        destination.unlink(missing_ok=True)
    _systemctl(["daemon-reload"])


def addon_status(manifest: dict[str, Any], home_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Report persisted files and actual systemd state without trusting config flags."""
    home = Path(home_dir).resolve()
    try:
        files = _validated_files(manifest, home)
        installed = all(destination.is_file() for _source, destination, _mode in files)
    except AddonError as exc:
        return {
            "id": manifest.get("id", "unknown"), "name": manifest.get("name", "Add-on"),
            "available": False, "installed": False, "enabled": False, "active": False,
            "state": "fehlerhaft", "error": str(exc),
        }

    enabled = active = False
    error = None
    missing_dependencies = _missing_dependencies(manifest)
    configured = True
    if manifest.get("config"):
        try:
            configured = _destination(home, manifest["config"]).is_file()
        except AddonError as exc:
            configured = False
            error = str(exc)
    if installed:
        try:
            enabled = _systemctl(["is-enabled", manifest["service"]], check=False).returncode == 0
            active = _systemctl(["is-active", manifest["service"]], check=False).returncode == 0
        except AddonError as exc:
            error = str(exc)

    if installed and missing_dependencies:
        error = "Fehlende Abhängigkeiten: " + ", ".join(missing_dependencies)
    if error:
        state = "fehlerhaft"
    elif not installed:
        state = "nicht installiert"
    elif not configured:
        state = "nicht konfiguriert"
    elif active:
        state = "läuft"
    elif enabled:
        state = "gestoppt"
    else:
        state = "installiert"
    return {
        "id": manifest["id"], "name": manifest["name"], "version": manifest["version"],
        "description": manifest.get("description", ""), "service": manifest["service"],
        "available": True, "installed": installed, "enabled": enabled, "active": active,
        "configured": configured, "state": state, "error": error,
        "missing_dependencies": missing_dependencies,
    }


def all_addon_statuses(addons_dir: str | os.PathLike[str], home_dir: str | os.PathLike[str]) -> dict[str, dict[str, Any]]:
    return {key: addon_status(value, home_dir) for key, value in discover_addons(addons_dir).items()}
