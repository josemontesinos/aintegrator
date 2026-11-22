resource "aws_sqs_queue" "bridge_dlq" {
  name                      = "${local.name}-sfn-bridge-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "bridge" {
  name                       = "${local.name}-sfn-bridge"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 3600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.bridge_dlq.arn
    maxReceiveCount     = 3
  })

  tags = local.common_tags
}

data "archive_file" "bridge" {
  type        = "zip"
  source_file = "${path.module}/lambda/bridge.py"
  output_path = "${path.root}/build/lambda-bridge.zip"
}

resource "aws_lambda_function" "bridge" {
  function_name    = "${local.name}-sfn-bridge"
  description      = "Completes fictional Step Functions task tokens from SQS statuses"
  role             = aws_iam_role.lambda.arn
  handler          = "bridge.handler"
  runtime          = "python3.12"
  timeout          = 30
  filename         = data.archive_file.bridge.output_path
  source_code_hash = data.archive_file.bridge.output_base64sha256

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.order_lifecycle.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy.lambda]

  tags = local.common_tags
}

resource "aws_lambda_event_source_mapping" "bridge" {
  event_source_arn = aws_sqs_queue.bridge.arn
  function_name    = aws_lambda_function.bridge.arn
  batch_size       = 1
}

# ADR 004: this state machine complements the DB reconciler; it never replaces it.
resource "aws_sfn_state_machine" "order_lifecycle" {
  name     = "${local.name}-order-lifecycle"
  role_arn = aws_iam_role.sfn.arn

  definition = templatefile("${path.module}/step_functions.asl.json.tftpl", {
    cluster_arn                  = aws_ecs_cluster.main.arn
    check_task_definition_arn    = aws_ecs_task_definition.reconcile["check"].arn
    fallback_task_definition_arn = aws_ecs_task_definition.reconcile["fallback"].arn
    subnet_ids                   = jsonencode(values(aws_subnet.public)[*].id)
    security_group_ids           = jsonencode([aws_security_group.ecs_tasks.id])
    queue_url                    = aws_sqs_queue.bridge.url
    step_wait_seconds            = var.step_function_wait_seconds
    task_timeout_seconds         = var.step_function_wait_seconds + 60
  })

  tags = local.common_tags
}
