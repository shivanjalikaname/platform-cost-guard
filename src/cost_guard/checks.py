"""Waste checks. Every call here is read-only (Describe*/GetMetricStatistics).

Each check takes a ``Context`` for one region and returns a list of findings.
Resources tagged with the keep tag (default ``cost-guard:keep=true``) are skipped.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .config import HOURS_PER_MONTH, Settings
from .models import Finding, display_name, tags_to_dict


@dataclass
class Context:
    region: str
    ec2: object
    cloudwatch: object
    settings: Settings
    now: datetime


def _paginate(client, method: str, key: str, **kwargs) -> Iterable[dict]:
    if client.can_paginate(method):
        for page in client.get_paginator(method).paginate(**kwargs):
            yield from page.get(key, [])
    else:
        yield from getattr(client, method)(**kwargs).get(key, [])


def _label(resource_id: str, tags: dict[str, str]) -> str:
    name = display_name(tags)
    return f"{resource_id} ({name})" if name else resource_id


def _ebs_price(ctx: Context, volume_type: str) -> float:
    return ctx.settings.prices.get(f"ebs_{volume_type}", ctx.settings.prices["ebs_gp3"])


# --------------------------------------------------------------------------- checks


def unattached_volumes(ctx: Context) -> list[Finding]:
    findings = []
    volumes = _paginate(
        ctx.ec2,
        "describe_volumes",
        "Volumes",
        Filters=[{"Name": "status", "Values": ["available"]}],
    )
    for vol in volumes:
        tags = tags_to_dict(vol.get("Tags"))
        if ctx.settings.is_kept(tags):
            continue
        size, vtype = vol["Size"], vol["VolumeType"]
        findings.append(
            Finding(
                check="unattached_volumes",
                region=ctx.region,
                resource_id=vol["VolumeId"],
                monthly_cost=size * _ebs_price(ctx, vtype),
                summary=f"{_label(vol['VolumeId'], tags)}: {size} GiB {vtype}, not attached",
                action="Snapshot if needed, then delete the volume",
                tags=tags,
            )
        )
    return findings


def gp2_volumes(ctx: Context) -> list[Finding]:
    """In-use gp2 volumes: gp3 is ~20% cheaper with equal or better baseline performance."""
    findings = []
    saving_per_gb = ctx.settings.prices["ebs_gp2"] - ctx.settings.prices["ebs_gp3"]
    volumes = _paginate(
        ctx.ec2,
        "describe_volumes",
        "Volumes",
        Filters=[
            {"Name": "volume-type", "Values": ["gp2"]},
            {"Name": "status", "Values": ["in-use"]},
        ],
    )
    for vol in volumes:
        tags = tags_to_dict(vol.get("Tags"))
        if ctx.settings.is_kept(tags):
            continue
        findings.append(
            Finding(
                check="gp2_volumes",
                region=ctx.region,
                resource_id=vol["VolumeId"],
                monthly_cost=vol["Size"] * saving_per_gb,
                summary=f"{_label(vol['VolumeId'], tags)}: {vol['Size']} GiB gp2 in use",
                action="Modify volume type to gp3 online (no downtime); saving shown",
                tags=tags,
            )
        )
    return findings


def unused_elastic_ips(ctx: Context) -> list[Finding]:
    findings = []
    monthly = ctx.settings.prices["public_ipv4_hour"] * HOURS_PER_MONTH
    for addr in ctx.ec2.describe_addresses().get("Addresses", []):
        if addr.get("AssociationId") or addr.get("InstanceId"):
            continue
        tags = tags_to_dict(addr.get("Tags"))
        if ctx.settings.is_kept(tags):
            continue
        rid = addr.get("AllocationId") or addr.get("PublicIp", "unknown")
        findings.append(
            Finding(
                check="unused_elastic_ips",
                region=ctx.region,
                resource_id=rid,
                monthly_cost=monthly,
                summary=f"{_label(rid, tags)}: Elastic IP {addr.get('PublicIp')} not associated",
                action="Release the address if nothing depends on it (DNS, allow-lists)",
                tags=tags,
            )
        )
    return findings


def old_snapshots(ctx: Context) -> list[Finding]:
    """Own snapshots older than N days that no own AMI depends on."""
    cutoff = ctx.now - timedelta(days=ctx.settings.snapshot_age_days)
    in_use: set[str] = set()
    for image in _paginate(ctx.ec2, "describe_images", "Images", Owners=["self"]):
        for mapping in image.get("BlockDeviceMappings", []):
            snap = mapping.get("Ebs", {}).get("SnapshotId")
            if snap:
                in_use.add(snap)

    findings = []
    for snap in _paginate(ctx.ec2, "describe_snapshots", "Snapshots", OwnerIds=["self"]):
        if snap["SnapshotId"] in in_use or snap["StartTime"] > cutoff:
            continue
        tags = tags_to_dict(snap.get("Tags"))
        if ctx.settings.is_kept(tags):
            continue
        age = (ctx.now - snap["StartTime"]).days
        size = snap.get("VolumeSize", 0)
        findings.append(
            Finding(
                check="old_snapshots",
                region=ctx.region,
                resource_id=snap["SnapshotId"],
                monthly_cost=size * ctx.settings.prices["snapshot"],
                summary=(
                    f"{_label(snap['SnapshotId'], tags)}: {size} GiB, {age} days old, "
                    "no AMI uses it"
                ),
                action="Delete, or move to the archive tier if it must be retained",
                tags=tags,
            )
        )
    return findings


def idle_instances(ctx: Context) -> list[Finding]:
    """Running instances whose daily average CPU never exceeded the threshold."""
    s = ctx.settings
    start = ctx.now - timedelta(days=s.idle_days)
    min_points = max(1, s.idle_days // 2)  # not enough data -> not judged
    findings = []

    reservations = _paginate(
        ctx.ec2,
        "describe_instances",
        "Reservations",
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}],
    )
    for reservation in reservations:
        for inst in reservation["Instances"]:
            tags = tags_to_dict(inst.get("Tags"))
            if s.is_kept(tags) or inst["LaunchTime"] > start:
                continue
            stats = ctx.cloudwatch.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": inst["InstanceId"]}],
                StartTime=start,
                EndTime=ctx.now,
                Period=86400,
                Statistics=["Average"],
            )
            points = [p["Average"] for p in stats.get("Datapoints", [])]
            if len(points) < min_points or max(points) >= s.idle_cpu_percent:
                continue
            itype = inst["InstanceType"]
            hourly = s.instance_prices.get(itype)
            price_note = "" if hourly is not None else " (price unknown, add to instance_prices)"
            findings.append(
                Finding(
                    check="idle_instances",
                    region=ctx.region,
                    resource_id=inst["InstanceId"],
                    monthly_cost=(hourly or 0.0) * HOURS_PER_MONTH,
                    summary=(
                        f"{_label(inst['InstanceId'], tags)}: {itype}, peak daily avg CPU "
                        f"{max(points):.1f}% over {s.idle_days}d{price_note}"
                    ),
                    action="Stop, downsize, or move to a smaller/burstable type",
                    tags=tags,
                )
            )
    return findings


_STOPPED_AT = re.compile(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def stopped_since(reason: str) -> datetime | None:
    """Parse the stop time from StateTransitionReason.

    Example: 'User initiated (2024-01-02 03:04:05 GMT)'.
    """
    match = _STOPPED_AT.search(reason or "")
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def stopped_instances(ctx: Context) -> list[Finding]:
    """Instances stopped for N+ days still pay for their EBS volumes."""
    cutoff = ctx.now - timedelta(days=ctx.settings.stopped_days)
    findings = []
    reservations = _paginate(
        ctx.ec2,
        "describe_instances",
        "Reservations",
        Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}],
    )
    for reservation in reservations:
        for inst in reservation["Instances"]:
            tags = tags_to_dict(inst.get("Tags"))
            since = stopped_since(inst.get("StateTransitionReason", ""))
            if ctx.settings.is_kept(tags) or since is None or since > cutoff:
                continue
            volumes = list(
                _paginate(
                    ctx.ec2,
                    "describe_volumes",
                    "Volumes",
                    Filters=[{"Name": "attachment.instance-id", "Values": [inst["InstanceId"]]}],
                )
            )
            cost = sum(v["Size"] * _ebs_price(ctx, v["VolumeType"]) for v in volumes)
            total_gb = sum(v["Size"] for v in volumes)
            findings.append(
                Finding(
                    check="stopped_instances",
                    region=ctx.region,
                    resource_id=inst["InstanceId"],
                    monthly_cost=cost,
                    summary=(
                        f"{_label(inst['InstanceId'], tags)}: "
                        f"stopped {(ctx.now - since).days} days, "
                        f"{len(volumes)} volume(s) / {total_gb} GiB still billed"
                    ),
                    action="Create an AMI and terminate, or tag as keep if it is a cold standby",
                    tags=tags,
                )
            )
    return findings


CHECKS: dict[str, Callable[[Context], list[Finding]]] = {
    "unattached_volumes": unattached_volumes,
    "gp2_volumes": gp2_volumes,
    "unused_elastic_ips": unused_elastic_ips,
    "old_snapshots": old_snapshots,
    "idle_instances": idle_instances,
    "stopped_instances": stopped_instances,
}
