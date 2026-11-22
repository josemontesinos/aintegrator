resource "aws_cloudwatch_log_group" "tasks" {
  for_each = local.repositories

  name              = "/ecs/${local.name}-${each.value}"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-${each.value}" }
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}-sfn-bridge"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-sfn-bridge" }
}
