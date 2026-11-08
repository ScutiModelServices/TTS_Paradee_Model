"""Settings changed on the admin page, kept in a JSON file beside the tokens so they survive a restart and every
replica sees them."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


def load(path: str | os.PathLike[str]) -> dict:
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def replace(path: str | os.PathLike[str], data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), "utf-8")
    os.replace(temp, path)


def save(path: str | os.PathLike[str], **values: object) -> None:
    replace(path, {**load(path), **values})


@dataclass(frozen=True)
class Setting:
    """One option the admin page can change. The value is the one saved on the admin page, else the environment
    variable `env`, else `default`. `restart`: read once at start, so a change applies after a restart."""

    key: str
    label: str
    kind: Literal["int", "float", "bool", "text"]
    default: Any
    env: str = ""
    minimum: float | None = None
    maximum: float | None = None
    restart: bool = False
    blank: bool = True  # text only: may the value be empty
    help: str = ""


def _coerce(spec: Setting, value: Any, from_env: bool = False) -> Any:
    """The value in the setting's type; raises ValueError. Environment values are strings, JSON values are typed."""
    if spec.kind == "bool":
        if from_env:
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if not isinstance(value, bool):
            raise ValueError(f"{spec.label} must be on or off")
        return value
    if spec.kind == "text":
        if not isinstance(value, str):
            raise ValueError(f"{spec.label} must be text")
        value = value.strip()
        if not value and not spec.blank:
            raise ValueError(f"{spec.label} cannot be empty")
        return value
    if isinstance(value, bool) or (isinstance(value, str) and not from_env):
        raise ValueError(f"{spec.label} must be a number")
    try:
        if spec.kind == "int" and isinstance(value, float) and not value.is_integer():
            raise ValueError
        number = int(value) if spec.kind == "int" else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{spec.label} must be a {'whole ' if spec.kind == 'int' else ''}number") from None
    if (spec.minimum is not None and number < spec.minimum) or (spec.maximum is not None and number > spec.maximum):
        raise ValueError(f"{spec.label} must be between {spec.minimum} and {spec.maximum}")
    return number


class Config:
    """The options in `schema`, read from the settings file beside the tokens. The file's modification time is checked
    on every read, so replicas sharing it see a saved change at once.

    shortcut: a change is missed when two saves land within the file system's timestamp resolution; upgrade to
    a version counter in the file if that matters."""

    def __init__(self, path: str | os.PathLike[str], schema: list[Setting]) -> None:
        self._path = Path(path)
        self._schema = {spec.key: spec for spec in schema}
        self._stamp: int | None = -1
        self._saved: dict = {}

    def _saved_values(self) -> dict:
        try:
            stamp = self._path.stat().st_mtime_ns
        except OSError:
            stamp = None
        if stamp != self._stamp:
            self._saved, self._stamp = load(self._path), stamp
        return self._saved

    def _resolve(self, key: str) -> tuple[Any, str]:
        spec = self._schema[key]
        saved = self._saved_values().get(key)
        if saved is not None:
            try:
                return _coerce(spec, saved), "saved"
            except ValueError:
                pass  # a hand-edited bad value is ignored
        raw = os.environ.get(spec.env, "").strip() if spec.env else ""
        if raw:
            try:
                return _coerce(spec, raw, from_env=True), "environment"
            except ValueError:
                pass
        return spec.default, "default"

    def get(self, key: str) -> Any:
        return self._resolve(key)[0]

    def describe(self) -> list[dict]:
        rows = []
        for spec in self._schema.values():
            value, source = self._resolve(spec.key)
            rows.append({"key": spec.key, "label": spec.label, "kind": spec.kind, "value": value, "default": spec.default,
                         "source": source, "restart": spec.restart, "help": spec.help,
                         "minimum": spec.minimum, "maximum": spec.maximum})
        return rows

    def set(self, values: dict[str, Any]) -> None:
        """Saves the values; None removes the saved one so the environment variable or default applies again.
        Raises ValueError and saves nothing when a key or value is invalid."""
        checked: dict[str, Any] = {}
        for key, value in values.items():
            spec = self._schema.get(key)
            if spec is None:
                raise ValueError(f"Unknown setting {key!r}")
            checked[key] = None if value is None else _coerce(spec, value)
        data = load(self._path)
        for key, value in checked.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        replace(self._path, data)
        self._stamp = -1


def load_env_file(path: str | os.PathLike[str]) -> list[str]:
    """Sets the variables of a .env file that the environment does not already have and returns their names.

    A real environment variable always wins. The values are never returned or printed."""
    from dotenv import dotenv_values

    loaded = []
    for key, value in dotenv_values(path).items():
        if value is not None and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def apply_models_dir() -> None:
    """Points the Hugging Face cache (HF_HOME) and Ollama (OLLAMA_MODELS) at SERVICE_MODELS_DIR.

    A real HF_HOME or OLLAMA_MODELS wins. Call before huggingface_hub is imported."""
    folder = os.environ.get("SERVICE_MODELS_DIR")
    if folder:
        os.environ.setdefault("HF_HOME", folder)
        os.environ.setdefault("OLLAMA_MODELS", os.path.join(folder, "ollama"))
