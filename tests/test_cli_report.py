import json
from datetime import UTC, datetime

import pytest
from conftest import REGION

from cost_guard import cli, report
from cost_guard.config import load_settings
from cost_guard.models import Finding
from cost_guard.scanner import ScanResult, scan


def _result(errors=()):
    return ScanResult(
        findings=[
            Finding("unattached_volumes", REGION, "vol-1", 80.0, "vol-1: 1000 GiB gp3", "Delete"),
            Finding("unused_elastic_ips", REGION, "eipalloc-1", 3.65, "eip | pipe", "Release"),
        ],
        errors=list(errors),
        regions=[REGION],
        generated_at=datetime(2026, 1, 5, 7, 0, tzinfo=UTC),
    )


def test_markdown_has_totals_and_escapes_pipes():
    md = report.to_markdown(_result())
    assert "$83.65 / month" in md
    assert "$1,003.80 / year" in md
    assert "eip \\| pipe" in md
    assert "Incomplete" not in md


def test_markdown_flags_partial_report():
    md = report.to_markdown(_result(errors=["eu-west-1/old_snapshots: AccessDenied"]))
    assert "lower bound" in md and "AccessDenied" in md


def test_json_round_trips():
    data = json.loads(report.to_json(_result()))
    assert data["total_monthly_usd"] == 83.65
    assert data["findings"][0]["resource_id"] == "vol-1"


def test_slack_rejects_non_slack_url():
    with pytest.raises(ValueError):
        report.post_slack("https://example.com/hook", {"text": "x"})


def test_config_overrides_and_rejects_typos(tmp_path):
    good = tmp_path / "ok.toml"
    good.write_text('regions = ["eu-west-1"]\nidle_days = 7\n[prices]\nebs_gp3 = 0.09\n')
    s = load_settings(good)
    assert s.regions == ["eu-west-1"] and s.idle_days == 7
    assert s.prices["ebs_gp3"] == 0.09 and s.prices["ebs_gp2"] == 0.10

    bad = tmp_path / "bad.toml"
    bad.write_text("idel_days = 7\n")
    with pytest.raises(ValueError, match="idel_days"):
        load_settings(bad)


def test_scan_records_errors_without_aborting(aws, monkeypatch):
    from botocore.exceptions import ClientError

    from cost_guard import checks

    def denied(_ctx):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "DescribeX")

    monkeypatch.setitem(checks.CHECKS, "old_snapshots", denied)
    aws.client("ec2").create_volume(AvailabilityZone=f"{REGION}a", Size=10)

    result = scan(load_settings(None), session=aws)
    assert any("AccessDenied" in e for e in result.errors)
    assert any(f.check == "unattached_volumes" for f in result.findings)


def test_cli_budget_gate_and_exit_codes(aws, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "scan", lambda settings: _result())
    out = tmp_path / "r.md"

    assert cli.main(["-f", "markdown", "-o", str(out)]) == 0
    assert "AWS cost guard report" in out.read_text()
    assert cli.main(["--fail-over", "50"]) == 1
    assert cli.main(["--fail-over", "500"]) == 0

    monkeypatch.setattr(cli, "scan", lambda settings: _result(errors=["x"]))
    assert cli.main([]) == 2


def test_cli_slack_without_webhook_is_usage_error(monkeypatch):
    monkeypatch.setattr(cli, "scan", lambda settings: _result())
    assert cli.main(["--slack"]) == 2


def test_cli_end_to_end_against_moto(aws, capsys):
    aws.client("ec2").create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp2")
    assert cli.main(["-r", REGION, "--check", "unattached_volumes", "-f", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_monthly_usd"] == 10.0
