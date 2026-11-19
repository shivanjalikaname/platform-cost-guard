# platform-cost-guard

[![ci](https://github.com/shivanjalikaname/platform-cost-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/shivanjalikaname/platform-cost-guard/actions/workflows/ci.yml)

Finds AWS resources that cost money and do nothing, puts a monthly price on each one,
and sends a ranked report every Monday. It is **read-only**: it never stops, deletes
or changes anything, so it is safe to run against production.

It runs in GitHub Actions with **short-lived OIDC credentials**. There are no access
keys to create, store or rotate.

## What it finds

| Check | Why it costs money | Suggested fix |
|---|---|---|
| `unattached_volumes` | EBS volumes are billed per GB even when nothing is attached | Snapshot if needed, delete |
| `gp2_volumes` | gp3 is ~20% cheaper than gp2 with equal or better baseline performance | Change type online, no downtime |
| `unused_elastic_ips` | Every public IPv4 address is billed hourly | Release it |
| `old_snapshots` | Snapshots older than 90 days that no AMI depends on | Delete or archive |
| `idle_instances` | Running instances whose daily average CPU stayed under 5% for 14 days | Stop or downsize |
| `stopped_instances` | Instances stopped for 30+ days still pay for their volumes | AMI + terminate |

Anything tagged `cost-guard:keep=true` is skipped, so a deliberate cold standby
doesn't show up every week. All thresholds and prices can be changed in
[`examples/cost-guard.toml`](examples/cost-guard.toml).

## Example report

Output of `python examples/demo.py`, which seeds a fake account in memory with
[moto](https://github.com/getmoto/moto) (no AWS account needed):

> **Estimated avoidable spend: $111.92 / month** ($1,343.04 / year) across 9 resources.
>
> | Check | Resources | Monthly |
> |---|---:|---:|
> | Unattached EBS volumes | 2 | $85.00 |
> | Old snapshots not used by an AMI | 1 | $12.50 |
> | Unassociated Elastic IPs | 2 | $7.30 |
> | gp2 volumes that could be gp3 | 3 | $6.32 |
> | Long-stopped instances | 1 | $0.80 |
>
> | Monthly | Resource | Suggested action |
> |---:|---|---|
> | $45.00 | vol-…(migration-scratch): 1000 GiB st1, not attached | Snapshot if needed, then delete the volume |
> | $40.00 | vol-…(old-elasticsearch-data): 500 GiB gp3, not attached | Snapshot if needed, then delete the volume |
> | $12.50 | snap-…: 250 GiB, 120 days old, no AMI uses it | Delete, or move to the archive tier |

The same report goes to the GitHub Actions job summary, a JSON artifact, and
(optionally) a short Slack message.

## Quick start

```bash
pip install -e ".[dev]"
python examples/demo.py                      # try it with no AWS account

aws sso login --profile readonly             # or any credentials you already use
AWS_PROFILE=readonly cost-guard -r us-east-1 -r eu-west-1
cost-guard -c examples/cost-guard.toml -f markdown -o report.md
cost-guard --check unattached_volumes --fail-over 100   # budget gate for CI
```

| Exit code | Meaning |
|---|---|
| 0 | Scan complete, under budget (or no `--fail-over` set) |
| 1 | Estimated waste is over `--fail-over` USD per month |
| 2 | Bad usage, missing credentials, or a check failed (report marked partial) |

If one region or API call is denied, the other checks still run. The report says
which ones failed and that the total is a lower bound.

## Running it weekly in GitHub Actions

1. Create the read-only role with the Terraform module in
   [`terraform/github-oidc-role`](terraform/github-oidc-role). Only the `main` branch
   of this one repository can assume it.
2. Save the `role_arn` output as the repository **variable** `AWS_ROLE_ARN`.
3. Optional: add the **secret** `SLACK_WEBHOOK_URL`. The tool only sends to
   `https://hooks.slack.com/` URLs.

[`weekly-report.yml`](.github/workflows/weekly-report.yml) runs every Monday at
06:00 UTC, or on demand with an optional budget. Without `AWS_ROLE_ARN` (for example
on a fork) the job is skipped.

## Permissions

The tool needs only these six read-only actions ([`docs/iam-policy.json`](docs/iam-policy.json)):

```
ec2:DescribeVolumes  ec2:DescribeAddresses  ec2:DescribeSnapshots
ec2:DescribeImages   ec2:DescribeInstances  cloudwatch:GetMetricStatistics
```

CI fails if that policy gains a write action or drifts from the Terraform module.

## How it is tested

- 23 pytest tests run against AWS mocked by moto: pricing per volume type, keep tag,
  attached vs unattached, AMI-backed snapshots, stopped-time parsing, partial
  failures, CLI exit codes, budget gate, report escaping.
- CI runs the tests on Python 3.11, 3.12 and 3.13, plus ruff lint and format.
- `terraform fmt` and `terraform validate` run on the IAM module.
- The test suite sets dummy credentials, so it can never reach a real account.

## Design choices

- **Estimates, not invoices.** Prices are us-east-1 on-demand list prices. They are
  close enough to rank waste, and you can override them per region or discount.
  Cost Explorer would be exact, but it costs $0.01 per request and can't point at a
  single resource.
- **No data, no verdict.** An instance with too few CPU datapoints is not called
  idle. An instance type with no known price is still listed, with a note.
- **Report, don't act.** Deleting is a human decision. The tool gives the evidence
  and the suggested action, and the keep tag records decisions so they aren't
  asked again.

## Layout

```
src/cost_guard/       checks, scanner, reports, CLI
tests/                pytest + moto
examples/             sample config and offline demo
terraform/            GitHub OIDC read-only role
docs/iam-policy.json  minimal IAM policy
.github/workflows/    CI and the weekly report
```

## License

[MIT](LICENSE)
