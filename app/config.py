"""Configuration loading for the marketing engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("config file %s must contain a mapping" % path)
    return data


@dataclass(frozen=True)
class Policy:
    version: str
    raw: Dict[str, Any]

    @property
    def disclosure_classes(self) -> List[str]:
        return [str(c) for c in self.raw.get("disclosure_classes", [])]

    @property
    def publishable_classes(self) -> List[str]:
        return [str(c) for c in self.raw.get("publishable_classes", [])]

    @property
    def auto_publishable_classes(self) -> List[str]:
        return [str(c) for c in self.raw.get("auto_publishable_classes", [])]

    @property
    def default_approval_requirement(self) -> str:
        return str(self.raw.get("default_approval_requirement", "HUMAN_APPROVAL_REQUIRED"))

    @property
    def operating_mode(self) -> str:
        return str(self.raw.get("operating_mode", "MODE_1_APPROVAL_REQUIRED"))

    @property
    def auto_eligible_pillars(self) -> List[str]:
        return [str(p) for p in self.raw.get("auto_eligible_pillars", [])]

    def restricted(self, bucket: str) -> List[Dict[str, Any]]:
        return list((self.raw.get("restricted_categories", {}) or {}).get(bucket, []))

    @property
    def prohibited_phrases(self) -> List[str]:
        return [str(p) for p in self.raw.get("prohibited_phrases", [])]

    @property
    def evidence_required_categories(self) -> List[str]:
        return [
            str(c)
            for c in (self.raw.get("evidence", {}) or {}).get(
                "required_for_claim_categories", []
            )
        ]

    @property
    def accepted_evidence_kinds(self) -> List[str]:
        return [
            str(k) for k in (self.raw.get("evidence", {}) or {}).get("accepted_kinds", [])
        ]

    @property
    def duplicate_lookback_days(self) -> int:
        return int((self.raw.get("duplicate_prevention", {}) or {}).get("lookback_days", 90))


@dataclass(frozen=True)
class Pillars:
    version: str
    raw: Dict[str, Any]

    @property
    def all(self) -> List[Dict[str, Any]]:
        return list(self.raw.get("pillars", []))

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        for pillar in self.all:
            if pillar.get("key") == key:
                return dict(pillar)
        return None

    @property
    def keys(self) -> List[str]:
        return [str(p["key"]) for p in self.all]


@dataclass(frozen=True)
class Cadence:
    version: str
    raw: Dict[str, Any]

    @property
    def timezone(self) -> str:
        return str(self.raw.get("timezone", "America/New_York"))

    def plan_for(self, weekday_name: str) -> List[Dict[str, Any]]:
        plan = self.raw.get("weekly_plan", {}) or {}
        return list(plan.get(weekday_name.lower(), []) or [])

    @property
    def limits(self) -> Dict[str, Any]:
        return dict(self.raw.get("limits", {}))

    @property
    def slot_eligibility(self) -> Dict[str, Any]:
        return dict(self.raw.get("slot_eligibility", {}))

    def min_body_chars(self, platform: str) -> int:
        return int(self.slot_eligibility.get("min_body_chars", {}).get(platform, 0))


@dataclass(frozen=True)
class Platforms:
    version: str
    raw: Dict[str, Any]

    @property
    def publisher(self) -> Dict[str, Any]:
        return dict(self.raw.get("publisher", {}))

    def platform(self, name: str) -> Dict[str, Any]:
        return dict((self.raw.get("platforms", {}) or {}).get(name, {}))

    @property
    def names(self) -> List[str]:
        return list((self.raw.get("platforms", {}) or {}).keys())

    @property
    def style(self) -> Dict[str, Any]:
        return dict(self.raw.get("style", {}))


@dataclass(frozen=True)
class AiConfig:
    version: str
    raw: Dict[str, Any]

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", False))

    def task(self, name: str) -> Dict[str, Any]:
        return dict((self.raw.get("tasks", {}) or {}).get(name, {}))

    @property
    def budget(self) -> Dict[str, Any]:
        return dict(self.raw.get("budget", {}))

    @property
    def prompt_policy(self) -> Dict[str, Any]:
        return dict(self.raw.get("prompt_policy", {}))


@dataclass(frozen=True)
class AppConfig:
    policy: Policy
    pillars: Pillars
    cadence: Cadence
    platforms: Platforms
    ai: AiConfig
    config_dir: Path
    env: Dict[str, str] = field(default_factory=dict)

    @property
    def policy_version(self) -> str:
        return self.policy.version

    def env_value(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if key in self.env:
            return self.env[key]
        return os.environ.get(key, default)


def load_config(config_dir: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> AppConfig:
    directory = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    policy_raw = _load_yaml(directory / "policy.yaml")
    pillars_raw = _load_yaml(directory / "pillars.yaml")
    cadence_raw = _load_yaml(directory / "cadence.yaml")
    platforms_raw = _load_yaml(directory / "platforms.yaml")
    ai_raw = _load_yaml(directory / "ai.yaml")

    return AppConfig(
        policy=Policy(version=str(policy_raw["version"]), raw=policy_raw),
        pillars=Pillars(version=str(pillars_raw["version"]), raw=pillars_raw),
        cadence=Cadence(version=str(cadence_raw["version"]), raw=cadence_raw),
        platforms=Platforms(version=str(platforms_raw["version"]), raw=platforms_raw),
        ai=AiConfig(version=str(ai_raw["version"]), raw=ai_raw),
        config_dir=directory,
        env=dict(env or {}),
    )
