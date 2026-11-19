from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .checks import CHECKS, Context
from .config import Settings
from .models import Finding

_BOTO_CONFIG = Config(retries={"max_attempts": 8, "mode": "adaptive"})


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def total_monthly(self) -> float:
        return sum(f.monthly_cost for f in self.findings)


def selected_checks(settings: Settings) -> list[str]:
    if not settings.checks:
        return list(CHECKS)
    unknown = set(settings.checks) - set(CHECKS)
    if unknown:
        raise ValueError(f"unknown check(s): {', '.join(sorted(unknown))}")
    return list(settings.checks)


def scan(
    settings: Settings, session: boto3.Session | None = None, now: datetime | None = None
) -> ScanResult:
    session = session or boto3.Session()
    now = now or datetime.now(UTC)
    names = selected_checks(settings)
    result = ScanResult(regions=list(settings.regions), generated_at=now)

    for region in settings.regions:
        ctx = Context(
            region=region,
            ec2=session.client("ec2", region_name=region, config=_BOTO_CONFIG),
            cloudwatch=session.client("cloudwatch", region_name=region, config=_BOTO_CONFIG),
            settings=settings,
            now=now,
        )
        for name in names:
            try:
                result.findings.extend(CHECKS[name](ctx))
            except (ClientError, BotoCoreError) as exc:
                # One denied API or throttled region must not hide the rest of the report.
                result.errors.append(f"{region}/{name}: {exc}")

    result.findings.sort(key=lambda f: f.monthly_cost, reverse=True)
    return result
