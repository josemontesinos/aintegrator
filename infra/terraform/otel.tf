resource "aws_ssm_parameter" "otel_config" {
  name        = "/${local.name}/otel-collector/config"
  description = "Inline OTLP receiver and X-Ray exporter config for the fictional collector"
  type        = "String"

  value = <<-EOT
    receivers:
      otlp:
        protocols:
          grpc:
            endpoint: 0.0.0.0:4317
          http:
            endpoint: 0.0.0.0:4318
    processors:
      batch:
    exporters:
      debug:
        verbosity: detailed
      awsxray:
        region: ${var.aws_region}
    service:
      pipelines:
        traces:
          receivers: [otlp]
          processors: [batch]
          exporters: [awsxray, debug]
  EOT

  tags = local.common_tags
}

resource "aws_ecs_task_definition" "otel_collector" {
  family                   = "${local.name}-otel-collector"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.otel_task.arn

  container_definitions = jsonencode([
    {
      name      = "otel-collector"
      image     = "${aws_ecr_repository.app["otel-collector"].repository_url}:${var.image_tag}"
      essential = true
      portMappings = [
        { containerPort = 4317, hostPort = 4317, protocol = "tcp" },
        { containerPort = 4318, hostPort = 4318, protocol = "tcp" },
      ]
      command = [
        "/bin/sh",
        "-c",
        "printf '%s' \"$OTEL_COLLECTOR_CONFIG\" > /tmp/otel-config.yaml && exec /otelcol --config=/tmp/otel-config.yaml",
      ]
      secrets = [
        { name = "OTEL_COLLECTOR_CONFIG", valueFrom = aws_ssm_parameter.otel_config.arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.tasks["otel-collector"].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = { Name = "${local.name}-otel-collector" }
}

resource "aws_ecs_service" "otel_collector" {
  name            = "${local.name}-otel-collector"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.otel_collector.arn
  launch_type     = "FARGATE"
  desired_count   = 1

  network_configuration {
    subnets          = values(aws_subnet.public)[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  service_registries {
    registry_arn = aws_service_discovery_service.app["otel_collector"].arn
  }

  tags = { Name = "${local.name}-otel-collector" }
}
