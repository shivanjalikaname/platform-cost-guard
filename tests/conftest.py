from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from cost_guard.checks import Context
from cost_guard.config import Settings

REGION = "us-east-1"


@pytest.fixture(autouse=True)
def fake_aws_env(monkeypatch):
    # Dummy values so nothing can ever reach a real account from the test suite.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)


@pytest.fixture
def aws():
    with mock_aws():
        yield boto3.Session(region_name=REGION)


@pytest.fixture
def ec2(aws):
    return aws.client("ec2", region_name=REGION)


@pytest.fixture
def ami(ec2):
    return ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]


def make_ctx(session, settings=None, days_ahead=0):
    """Context whose clock is `days_ahead` in the future, so new resources look old."""
    return Context(
        region=REGION,
        ec2=session.client("ec2", region_name=REGION),
        cloudwatch=session.client("cloudwatch", region_name=REGION),
        settings=settings or Settings(),
        now=datetime.now(UTC) + timedelta(days=days_ahead),
    )
