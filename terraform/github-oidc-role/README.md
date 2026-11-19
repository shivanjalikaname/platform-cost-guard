# github-oidc-role

Creates a read-only IAM role that only the `main` branch of one GitHub repository can
assume, through GitHub's OIDC provider. No access keys are created or stored.

```bash
terraform init
terraform apply -var 'github_repository=<owner>/platform-cost-guard'
# account already has the GitHub provider?
terraform apply -var 'github_repository=<owner>/platform-cost-guard' -var 'create_oidc_provider=false'
```

Copy the `role_arn` output into **Settings → Secrets and variables → Actions →
Variables** as `AWS_ROLE_ARN`. The weekly workflow starts running on its next schedule.

The role can call only `ec2:Describe*` (five actions) and `cloudwatch:GetMetricStatistics`.
