"""Render a ScanResult as Markdown, JSON or a terminal table, and post a Slack summary."""

from __future__ import annotations

import json
import urllib.request
from collections import defaultdict

from .scanner import ScanResult

TITLES = {
    "unattached_volumes": "Unattached EBS volumes",
    "gp2_volumes": "gp2 volumes that could be gp3",
    "unused_elastic_ips": "Unassociated Elastic IPs",
    "old_snapshots": "Old snapshots not used by an AMI",
    "idle_instances": "Idle running instances",
    "stopped_instances": "Long-stopped instances",
}


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _by_check(result: ScanResult) -> dict[str, list]:
    groups: dict[str, list] = defaultdict(list)
    for f in result.findings:
        groups[f.check].append(f)
    return groups


def to_json(result: ScanResult) -> str:
    return json.dumps(
        {
            "generated_at": result.generated_at.isoformat(),
            "regions": result.regions,
            "total_monthly_usd": round(result.total_monthly, 2),
            "findings": [f.to_dict() for f in result.findings],
            "errors": result.errors,
        },
        indent=2,
    )


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(result: ScanResult, top: int = 25) -> str:
    groups = _by_check(result)
    lines = [
        "# AWS cost guard report",
        "",
        f"Generated {result.generated_at:%Y-%m-%d %H:%M} UTC for {', '.join(result.regions)}.",
        "",
        f"**Estimated avoidable spend: {_usd(result.total_monthly)} / month** "
        f"({_usd(result.total_monthly * 12)} / year) across {len(result.findings)} resources.",
        "",
    ]
    if groups:
        lines += ["| Check | Resources | Monthly |", "|---|---:|---:|"]
        ranked = sorted(groups.items(), key=lambda kv: -sum(f.monthly_cost for f in kv[1]))
        for check, items in ranked:
            total = sum(f.monthly_cost for f in items)
            lines.append(f"| {TITLES.get(check, check)} | {len(items)} | {_usd(total)} |")
        lines += [
            "",
            f"## Top {min(top, len(result.findings))} by cost",
            "",
            "| Monthly | Region | Resource | Suggested action |",
            "|---:|---|---|---|",
        ]
        for f in result.findings[:top]:
            lines.append(
                f"| {_usd(f.monthly_cost)} | {f.region} | {_md_cell(f.summary)} | "
                f"{_md_cell(f.action)} |"
            )
    else:
        lines.append("No waste found. Nice.")

    if result.errors:
        lines += [
            "",
            "## Incomplete",
            "",
            "These checks failed, so the total above is a lower bound:",
            "",
        ]
        lines += [f"- `{_md_cell(e)}`" for e in result.errors]
    lines += [
        "",
        "_Prices are on-demand estimates; resources tagged `cost-guard:keep=true` are excluded._",
        "",
    ]
    return "\n".join(lines)


def to_table(result: ScanResult) -> str:
    if not result.findings:
        out = "No waste found."
    else:
        rows = [(_usd(f.monthly_cost), f.region, f.check, f.summary) for f in result.findings]
        widths = [max(len(r[i]) for r in rows) for i in range(3)]
        out = "\n".join(
            f"{r[0]:>{widths[0]}}  {r[1]:<{widths[1]}}  {r[2]:<{widths[2]}}  {r[3]}" for r in rows
        )
        out += f"\n\nTotal: {_usd(result.total_monthly)} / month"
    if result.errors:
        out += "\n\nErrors (report is partial):\n" + "\n".join(f"  {e}" for e in result.errors)
    return out


def slack_payload(result: ScanResult, top: int = 5) -> dict:
    head = (
        f"*AWS cost guard:* {_usd(result.total_monthly)}/month avoidable across "
        f"{len(result.findings)} resources ({', '.join(result.regions)})"
    )
    lines = [head]
    for f in result.findings[:top]:
        lines.append(f"• {_usd(f.monthly_cost)}  {f.region}  {f.summary}")
    if result.errors:
        lines.append(f":warning: {len(result.errors)} check(s) failed; total is a lower bound")
    return {"text": "\n".join(lines)}


def post_slack(webhook_url: str, payload: dict, timeout: float = 10.0) -> None:
    if not webhook_url.startswith("https://hooks.slack.com/"):
        # Refuse anything else so a typo or injected value can't ship the report elsewhere.
        raise ValueError("SLACK_WEBHOOK_URL must be a https://hooks.slack.com/ URL")
    req = urllib.request.Request(  # noqa: S310 (scheme checked above)
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (scheme checked above)
        if resp.status >= 300:
            raise RuntimeError(f"Slack webhook returned HTTP {resp.status}")
