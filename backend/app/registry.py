"""Loads the YAML registries in data/: sources, schemes and refusal links."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.config import get_settings

ALLOWED_RENDER_MODES = {"auto", "http", "js"}


@dataclass(frozen=True)
class Source:
    id: str
    url: str
    scheme: str
    source_type: str
    render: str = "auto"


@dataclass(frozen=True)
class Scheme:
    id: str
    name: str
    source_id: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


def _data_dir(data_dir: Path | None) -> Path:
    return data_dir or get_settings().resolved_data_dir()


def _load_yaml(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_sources(data_dir: Path | None = None) -> list[Source]:
    raw = _load_yaml(_data_dir(data_dir) / "sources.yaml") or []
    sources = [Source(**item) for item in raw]
    ids = [s.id for s in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("sources.yaml: duplicate source ids")
    for s in sources:
        if s.render not in ALLOWED_RENDER_MODES:
            raise ValueError(f"sources.yaml: {s.id} has invalid render mode {s.render!r}")
    return sources


def load_schemes(data_dir: Path | None = None) -> dict[str, Scheme]:
    raw = _load_yaml(_data_dir(data_dir) / "schemes.yaml") or {}
    return {
        scheme_id: Scheme(
            id=scheme_id,
            name=item["name"],
            source_id=item["source_id"],
            aliases=tuple(a.lower() for a in item.get("aliases", [])),
        )
        for scheme_id, item in raw.items()
    }


def load_refusal_links(data_dir: Path | None = None) -> dict[str, dict]:
    return _load_yaml(_data_dir(data_dir) / "refusal_links.yaml") or {}
