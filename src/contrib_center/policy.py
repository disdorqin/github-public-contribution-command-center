"""Config loader for the contribution center.

Loads YAML configs from the config/ directory relative to the repo root
(or the path given by the CONTRIB_CENTER_CONFIG_DIR env var). All values
are pure-data — no I/O happens here besides reading the YAML files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.environ.get("CONTRIB_CENTER_CONFIG_DIR", "config"))


def _load(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


@dataclass
class Repository:
    name: str
    full_name: str
    purpose: str
    allow: dict[str, bool] = field(default_factory=dict)

    @property
    def is_profile(self) -> bool:
        return self.purpose.strip().lower() == "github profile readme"


@dataclass
class Policy:
    mode: str = "safe"
    public_repos: list[Repository] = field(default_factory=list)
    rules: dict[str, Any] = field(default_factory=dict)
    external_search: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    prompts: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, config_dir: Path | None = None) -> "Policy":
        if config_dir is not None:
            global CONFIG_DIR  # noqa: PLW0603
            CONFIG_DIR = config_dir
        repos_raw = _load("public_repos.yml")
        rules = _load("rules.yml")
        ext = _load("external_search.yml")
        profile = _load("profile.yml")
        prompts = _load("prompts.yml")
        repos: list[Repository] = []
        for r in repos_raw.get("repositories", []):
            repos.append(
                Repository(
                    name=r["name"],
                    full_name=r["full_name"],
                    purpose=r.get("purpose", ""),
                    allow=r.get("allow", {}),
                )
            )
        return cls(
            mode=rules.get("mode", "safe"),
            public_repos=repos,
            rules=rules,
            external_search=ext,
            profile=profile,
            prompts=prompts,
        )

    def allowlist(self) -> list[str]:
        return [r.full_name for r in self.public_repos]

    def profile_repo(self) -> Repository | None:
        for r in self.public_repos:
            if r.is_profile:
                return r
        return None
