"""Settings, loaded from an optional TOML file (stdlib tomllib, no extra deps)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

# Approximate on-demand prices in USD, us-east-1, Linux. Good enough to rank waste;
# override in cost-guard.toml under [prices] if your region or contract differs.
DEFAULT_PRICES: dict[str, float] = {
    # EBS, per GB-month
    "ebs_gp3": 0.08,
    "ebs_gp2": 0.10,
    "ebs_io1": 0.125,
    "ebs_io2": 0.125,
    "ebs_st1": 0.045,
    "ebs_sc1": 0.015,
    "ebs_standard": 0.05,
    # Snapshots, per GB-month (standard tier, billed on full size as an upper bound)
    "snapshot": 0.05,
    # Public IPv4 address, per hour (charged whether attached or not since Feb 2024)
    "public_ipv4_hour": 0.005,
}

# On-demand hourly price for common instance types. Unknown types are still
# reported, with cost 0 and a note, so nothing is silently dropped.
DEFAULT_INSTANCE_PRICES: dict[str, float] = {
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "t3.large": 0.0832,
    "t3.xlarge": 0.1664,
    "m5.large": 0.096,
    "m5.xlarge": 0.192,
    "m6i.large": 0.096,
    "m6i.xlarge": 0.192,
    "m7i.large": 0.1008,
    "c5.large": 0.085,
    "c6i.large": 0.085,
    "r5.large": 0.126,
    "r6i.large": 0.126,
}

HOURS_PER_MONTH = 730


@dataclass
class Settings:
    regions: list[str] = field(default_factory=lambda: ["us-east-1"])
    keep_tag: str = "cost-guard:keep"
    idle_cpu_percent: float = 5.0
    idle_days: int = 14
    snapshot_age_days: int = 90
    stopped_days: int = 30
    checks: list[str] = field(default_factory=list)  # empty = all
    prices: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    instance_prices: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_INSTANCE_PRICES))

    def is_kept(self, tags: dict[str, str]) -> bool:
        return tags.get(self.keep_tag, "").lower() in {"true", "yes", "1"}


def load_settings(path: str | Path | None) -> Settings:
    settings = Settings()
    if path is None:
        return settings

    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    known = {f.name for f in fields(Settings)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown setting(s) in {path}: {', '.join(sorted(unknown))}")

    for key, value in raw.items():
        if key in ("prices", "instance_prices"):
            getattr(settings, key).update({k: float(v) for k, v in value.items()})
        else:
            setattr(settings, key, value)

    if settings.idle_days < 1 or settings.snapshot_age_days < 1 or settings.stopped_days < 1:
        raise ValueError("day thresholds must be >= 1")
    return settings
