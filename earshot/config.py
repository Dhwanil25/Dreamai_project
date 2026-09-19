"""Working project-relative configuration; independent of the launch directory."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from dotenv import load_dotenv


def find_project_root(start: Path | None = None) -> Path:
    """Find config.yaml above a module path; return its directory without writes.

    ``start`` defaults to this module's path, never the current working
    directory. Raise FileNotFoundError when no ancestor contains config.yaml.
    """
    origin = (start if start is not None else Path(__file__)).resolve()
    directory = origin if origin.is_dir() else origin.parent
    for candidate in (directory, *directory.parents):
        if (candidate / "config.yaml").is_file():
            return candidate
    raise FileNotFoundError(f"No config.yaml found above {origin}")


def _attributes(value: Any) -> Any:
    """Convert YAML mappings recursively to attribute-access objects."""
    if isinstance(value, dict):
        if not all(isinstance(key, str) and key.isidentifier() for key in value):
            raise ValueError("Configuration keys must be valid attribute names")
        return SimpleNamespace(**{key: _attributes(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_attributes(item) for item in value]
    return value


def load_config(project_root: Path | None = None) -> SimpleNamespace:
    """Load YAML and optional .env from a project root; return CONFIG attributes.

    Data keys ending in ``_dir`` or ``_path`` must be relative paths and become
    absolute Path objects anchored to the root. Other values retain their YAML
    types. Reject invalid mappings or paths escaping the project. The only
    side effect is loading .env values without overriding process variables.
    """
    root = find_project_root() if project_root is None else project_root.resolve()
    with (root / "config.yaml").open(encoding="utf-8") as handle:
        values = yaml.safe_load(handle)
    if not isinstance(values, dict) or not isinstance(values.get("data"), dict):
        raise ValueError("config.yaml must contain a data mapping")
    data = values["data"]
    for required in ("raw_dir", "processed_dir"):
        if required not in data:
            raise ValueError(f"Missing required data path: {required}")
    for key, value in data.items():
        if not isinstance(key, str):
            raise ValueError("Data configuration keys must be strings")
        if key.endswith(("_dir", "_path")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"data.{key} must be a nonempty relative path")
            relative = Path(value)
            resolved = (root / relative).resolve()
            if relative.is_absolute() or not resolved.is_relative_to(root):
                raise ValueError(f"data.{key} must stay relative to the project root")
            data[key] = resolved
    config = _attributes(values)
    load_dotenv(dotenv_path=root / ".env", override=False)
    return config


PROJECT_ROOT = find_project_root()
CONFIG = load_config(PROJECT_ROOT)
