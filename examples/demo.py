"""Run cost-guard against a fake, in-memory AWS account (moto). No credentials needed.

pip install -e ".[dev]"
python examples/demo.py
"""

import os
from datetime import UTC, datetime, timedelta

for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
    os.environ[key] = "demo"
os.environ.pop("AWS_PROFILE", None)
# Skip moto's bundled sample AMIs; their snapshots would swamp the demo report.
os.environ["MOTO_EC2_LOAD_DEFAULT_AMIS"] = "false"

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

from cost_guard.config import Settings  # noqa: E402
from cost_guard.report import to_markdown  # noqa: E402
from cost_guard.scanner import scan  # noqa: E402

REGION = "us-east-1"


def seed(ec2) -> None:
    ami = "ami-0abcdef1234567890"  # any id works in moto once sample AMIs are off
    az = f"{REGION}a"

    def tag(name):
        return [{"ResourceType": "volume", "Tags": [{"Key": "Name", "Value": name}]}]

    ec2.create_volume(
        AvailabilityZone=az,
        Size=500,
        VolumeType="gp3",
        TagSpecifications=tag("old-elasticsearch-data"),
    )
    ec2.create_volume(
        AvailabilityZone=az, Size=1000, VolumeType="st1", TagSpecifications=tag("migration-scratch")
    )
    kept = ec2.create_volume(AvailabilityZone=az, Size=200, VolumeType="gp3")["VolumeId"]
    ec2.create_tags(Resources=[kept], Tags=[{"Key": "cost-guard:keep", "Value": "true"}])

    web = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1, InstanceType="m5.large")[
        "Instances"
    ][0]["InstanceId"]
    db = ec2.create_volume(AvailabilityZone=az, Size=300, VolumeType="gp2")["VolumeId"]
    ec2.attach_volume(VolumeId=db, InstanceId=web, Device="/dev/sdf")

    old = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1, InstanceType="t3.large")[
        "Instances"
    ][0]["InstanceId"]
    ec2.stop_instances(InstanceIds=[old])

    for _ in range(2):
        ec2.allocate_address(Domain="vpc")
    snap_src = ec2.create_volume(AvailabilityZone=az, Size=250)["VolumeId"]
    ec2.create_snapshot(VolumeId=snap_src, Description="pre-upgrade backup")
    ec2.delete_volume(VolumeId=snap_src)


def main() -> None:
    with mock_aws():
        session = boto3.Session(region_name=REGION)
        seed(session.client("ec2"))
        # Pretend 120 days have passed so the snapshot and stopped instance count as old.
        result = scan(
            Settings(regions=[REGION]), session=session, now=datetime.now(UTC) + timedelta(days=120)
        )
        print(to_markdown(result))


if __name__ == "__main__":
    main()
