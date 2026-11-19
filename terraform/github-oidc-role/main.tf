terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

locals {
  oidc_host = "token.actions.githubusercontent.com"

  # Exactly one of the two exists, depending on create_oidc_provider.
  provider_arn = one(concat(
    aws_iam_openid_connect_provider.github[*].arn,
    data.aws_iam_openid_connect_provider.github[*].arn,
  ))

  # Only workflows on the listed refs of this one repository may assume the role.
  subjects = [for ref in var.allowed_refs : "repo:${var.github_repository}:ref:${ref}"]
}

# One GitHub OIDC provider per account; reuse it if it already exists.
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
  tags           = var.tags
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://${local.oidc_host}"
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = local.subjects
    }
  }
}

# Keep in sync with docs/iam-policy.json (CI checks this).
data "aws_iam_policy_document" "read_only" {
  statement {
    sid = "CostGuardReadOnly"

    actions = [
      "ec2:DescribeVolumes",
      "ec2:DescribeAddresses",
      "ec2:DescribeSnapshots",
      "ec2:DescribeImages",
      "ec2:DescribeInstances",
      "cloudwatch:GetMetricStatistics",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "cost_guard" {
  name                 = var.role_name
  description          = "Read-only role for the cost-guard weekly report (GitHub OIDC)"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

resource "aws_iam_role_policy" "read_only" {
  name   = "cost-guard-read-only"
  role   = aws_iam_role.cost_guard.id
  policy = data.aws_iam_policy_document.read_only.json
}
