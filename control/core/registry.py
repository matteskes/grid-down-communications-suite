"""Registry loader for the Grid Down app catalog (Phase 0 stub).

Schema: docs/SPECIFICATION.md §6.1. One YAML per app in control/registry/apps/.
"""
from __future__ import annotations

from pathlib import Path

REQUIRED_KEYS = {"id", "name", "category", "install", "launch"}


class RegistryError(RuntimeError):
    pass


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RegistryError(
            "PyYAML is required to read the registry (python3 -m pip install pyyaml)"
        ) from exc
    return yaml


def load_registry(registry_dir: Path) -> list[dict]:
    """Load and validate every apps/*.yaml. Raises RegistryError on schema drift."""
    yaml = _yaml()
    apps: list[dict] = []
    for path in sorted(registry_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        missing = REQUIRED_KEYS - data.keys()
        if missing:
            raise RegistryError(f"{path.name}: missing keys {sorted(missing)}")
        if data["id"] != path.stem:
            raise RegistryError(f"{path.name}: id {data['id']!r} != filename stem")
        apps.append(data)
    ids = [a["id"] for a in apps]
    if len(ids) != len(set(ids)):
        raise RegistryError("duplicate app ids in registry")
    if not apps:
        raise RegistryError(f"no registry entries found in {registry_dir}")
    return apps


def load_bundles(path: Path) -> dict:
    yaml = _yaml()
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("bundles", {})
