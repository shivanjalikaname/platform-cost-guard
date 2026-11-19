from datetime import UTC, datetime, timedelta

import pytest
from conftest import REGION, make_ctx

from cost_guard import checks
from cost_guard.checks import Context, stopped_since
from cost_guard.config import Settings

KEEP = [{"Key": "cost-guard:keep", "Value": "true"}]


def _volume(ec2, size=100, vtype="gp3", tags=None):
    kwargs = {"AvailabilityZone": f"{REGION}a", "Size": size, "VolumeType": vtype}
    if tags:
        kwargs["TagSpecifications"] = [{"ResourceType": "volume", "Tags": tags}]
    return ec2.create_volume(**kwargs)["VolumeId"]


def test_unattached_volume_priced_by_type(aws, ec2):
    gp3 = _volume(ec2, 100, "gp3", [{"Key": "Name", "Value": "old-data"}])
    st1 = _volume(ec2, 500, "st1")

    found = {f.resource_id: f for f in checks.unattached_volumes(make_ctx(aws))}

    assert found[gp3].monthly_cost == pytest.approx(8.0)
    assert "old-data" in found[gp3].summary
    assert found[st1].monthly_cost == pytest.approx(22.5)


def test_keep_tag_is_respected(aws, ec2):
    kept = _volume(ec2, 50, tags=KEEP)
    ids = {f.resource_id for f in checks.unattached_volumes(make_ctx(aws))}
    assert kept not in ids


def test_attached_volume_is_not_unattached(aws, ec2, ami):
    inst = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1, InstanceType="t3.micro")[
        "Instances"
    ][0]
    vol = _volume(ec2, 20)
    ec2.attach_volume(VolumeId=vol, InstanceId=inst["InstanceId"], Device="/dev/sdf")

    ids = {f.resource_id for f in checks.unattached_volumes(make_ctx(aws))}
    assert vol not in ids


def test_in_use_gp2_reports_gp3_saving(aws, ec2, ami):
    inst = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1, InstanceType="t3.micro")[
        "Instances"
    ][0]
    vol = _volume(ec2, 200, "gp2")
    ec2.attach_volume(VolumeId=vol, InstanceId=inst["InstanceId"], Device="/dev/sdf")

    found = {f.resource_id: f for f in checks.gp2_volumes(make_ctx(aws))}
    assert found[vol].monthly_cost == pytest.approx(200 * 0.02)


def test_unassociated_elastic_ip(aws, ec2, ami):
    free = ec2.allocate_address(Domain="vpc")["AllocationId"]
    used = ec2.allocate_address(Domain="vpc")["AllocationId"]
    inst = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1)["Instances"][0]
    ec2.associate_address(AllocationId=used, InstanceId=inst["InstanceId"])

    found = {f.resource_id: f for f in checks.unused_elastic_ips(make_ctx(aws))}
    assert free in found and used not in found
    assert found[free].monthly_cost == pytest.approx(3.65)


def test_old_snapshot_reported_unless_an_ami_uses_it(aws, ec2, ami):
    vol = _volume(ec2, 40)
    orphan = ec2.create_snapshot(VolumeId=vol)["SnapshotId"]

    inst = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1)["Instances"][0]
    image_id = ec2.create_image(InstanceId=inst["InstanceId"], Name="golden")["ImageId"]
    image = ec2.describe_images(ImageIds=[image_id])["Images"][0]
    backing = {m["Ebs"]["SnapshotId"] for m in image["BlockDeviceMappings"] if "Ebs" in m}

    found = {f.resource_id: f for f in checks.old_snapshots(make_ctx(aws, days_ahead=120))}

    assert orphan in found
    assert found[orphan].monthly_cost == pytest.approx(40 * 0.05)
    assert not backing & set(found)


def test_new_snapshot_is_not_old(aws, ec2):
    snap = ec2.create_snapshot(VolumeId=_volume(ec2, 10))["SnapshotId"]
    ids = {f.resource_id for f in checks.old_snapshots(make_ctx(aws))}
    assert snap not in ids


def test_stopped_instance_counts_its_volumes(aws, ec2, ami):
    inst = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1)["Instances"][0]
    iid = inst["InstanceId"]
    ec2.stop_instances(InstanceIds=[iid])

    assert checks.stopped_instances(make_ctx(aws)) == []  # just stopped
    found = {f.resource_id: f for f in checks.stopped_instances(make_ctx(aws, days_ahead=45))}
    assert iid in found
    assert "stopped 45 days" in found[iid].summary or "stopped 44 days" in found[iid].summary


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("User initiated (2024-01-02 03:04:05 GMT)", datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)),
        ("Server.SpotInstanceTermination", None),
        ("", None),
    ],
)
def test_stopped_since_parsing(reason, expected):
    assert stopped_since(reason) == expected


# ---- idle_instances uses small fakes: moto does not aggregate CloudWatch history well.


class FakeEC2:
    def __init__(self, instances):
        self._instances = instances

    def can_paginate(self, _):
        return False

    def describe_instances(self, **_):
        return {"Reservations": [{"Instances": self._instances}]}


class FakeCloudWatch:
    def __init__(self, series):
        self.series = series

    def get_metric_statistics(self, Dimensions, **_):  # noqa: N803 (boto signature)
        iid = Dimensions[0]["Value"]
        return {"Datapoints": [{"Average": v} for v in self.series.get(iid, [])]}


def _idle_ctx(instances, series, **settings):
    now = datetime(2026, 6, 1, tzinfo=UTC)
    return Context(REGION, FakeEC2(instances), FakeCloudWatch(series), Settings(**settings), now)


def _inst(iid, itype="m5.large", age_days=60, tags=None):
    return {
        "InstanceId": iid,
        "InstanceType": itype,
        "Tags": tags or [],
        "LaunchTime": datetime(2026, 6, 1, tzinfo=UTC) - timedelta(days=age_days),
    }


def test_idle_instance_found_and_priced():
    ctx = _idle_ctx(
        [_inst("i-idle"), _inst("i-busy")], {"i-idle": [1.2] * 14, "i-busy": [1.0] * 13 + [40.0]}
    )
    found = {f.resource_id: f for f in checks.idle_instances(ctx)}
    assert set(found) == {"i-idle"}
    assert found["i-idle"].monthly_cost == pytest.approx(0.096 * 730)


def test_idle_needs_enough_data_and_age():
    ctx = _idle_ctx(
        [_inst("i-sparse"), _inst("i-new", age_days=3), _inst("i-kept", tags=KEEP)],
        {"i-sparse": [0.5] * 3, "i-new": [0.5] * 14, "i-kept": [0.5] * 14},
    )
    assert checks.idle_instances(ctx) == []


def test_unknown_instance_type_is_still_reported():
    ctx = _idle_ctx([_inst("i-x", itype="z9.mega")], {"i-x": [0.1] * 14})
    (finding,) = checks.idle_instances(ctx)
    assert finding.monthly_cost == 0
    assert "price unknown" in finding.summary
