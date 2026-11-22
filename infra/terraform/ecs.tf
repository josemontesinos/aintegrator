resource "aws_ecs_cluster" "main" {
  name = local.name

  tags = { Name = local.name }
}

resource "aws_service_discovery_service" "app" {
  for_each = local.discovery_services

  name         = each.key == "otel_collector" ? "otel-collector" : each.key
  namespace_id = aws_service_discovery_private_dns_namespace.main.id

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id

    dns_records {
      type = "A"
      ttl  = 10
    }

    routing_policy = "MULTIVALUE"
  }

  tags = { Name = each.key }
}

resource "aws_ecs_task_definition" "app" {
  for_each = local.app_services

  family                   = "${local.name}-${each.key}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name         = each.key
      image        = "${aws_ecr_repository.app[each.key].repository_url}:${var.image_tag}"
      essential    = true
      command      = each.value.command
      portMappings = [{ containerPort = each.value.port, hostPort = each.value.port, protocol = "tcp" }]
      environment  = local.task_env[each.key]
      # ECS exposes container_definitions as JSON, so secrets are injected with for-expressions.
      secrets = local.task_secrets[each.key]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.tasks[each.key].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = { Name = "${local.name}-${each.key}" }
}

resource "aws_ecs_task_definition" "reconcile" {
  for_each = local.reconcile_task_defs

  family                   = "${local.name}-reconcile-${each.key}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "reconciler"
      image     = "${aws_ecr_repository.app["reconciler"].repository_url}:${var.image_tag}"
      essential = true
      command   = each.value.command
      environment = concat(
        local.base_env,
        local.integration_env,
        [
          { name = "WORKFLOW_STEP", value = each.value.step },
          { name = "OTEL_SERVICE_NAME", value = "${local.name}-reconcile-${each.key}" },
        ]
      )
      secrets = local.task_secrets.webhook
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.tasks["reconciler"].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "reconcile-${each.key}"
        }
      }
    }
  ])

  tags = { Name = "${local.name}-reconcile-${each.key}" }
}

resource "aws_lb" "webhook" {
  name               = "${local.name}-webhook"
  internal           = false
  load_balancer_type = "application"
  subnets            = values(aws_subnet.public)[*].id
  security_groups    = [aws_security_group.alb.id]

  tags = { Name = "${local.name}-webhook" }
}

resource "aws_lb_target_group" "webhook" {
  name        = "${local.name}-webhook"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id

  health_check {
    path    = "/openapi.json"
    matcher = "200"
  }

  tags = { Name = "${local.name}-webhook" }
}

resource "aws_lb_listener" "webhook" {
  load_balancer_arn = aws_lb.webhook.arn
  port              = 8000
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.webhook.arn
  }
}

resource "aws_ecs_service" "app" {
  for_each = local.app_services

  name            = "${local.name}-${each.key}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app[each.key].arn
  launch_type     = "FARGATE"
  desired_count   = 1

  health_check_grace_period_seconds = each.key == "webhook" ? 60 : null

  network_configuration {
    subnets          = values(aws_subnet.public)[*].id
    security_groups  = each.key == "webhook" ? [aws_security_group.ecs_tasks.id, aws_security_group.webhook.id] : [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  service_registries {
    registry_arn = aws_service_discovery_service.app[each.key].arn
  }

  dynamic "load_balancer" {
    for_each = each.key == "webhook" ? [1] : []

    content {
      target_group_arn = aws_lb_target_group.webhook.arn
      container_name   = each.key
      container_port   = each.value.port
    }
  }

  depends_on = [aws_lb_listener.webhook]

  tags = { Name = "${local.name}-${each.key}" }
}

resource "aws_cloudwatch_event_rule" "reconcile" {
  name                = "${local.name}-reconcile"
  description         = "Fictional demo schedule for the authoritative DB reconciler (ADR 004)"
  schedule_expression = var.reconcile_schedule

  tags = { Name = "${local.name}-reconcile" }
}

resource "aws_cloudwatch_event_target" "reconcile" {
  rule = aws_cloudwatch_event_rule.reconcile.name
  arn  = aws_ecs_cluster.main.arn

  ecs_target {
    task_count          = 1
    task_definition_arn = aws_ecs_task_definition.reconcile["scheduled"].arn
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = values(aws_subnet.public)[*].id
      security_groups  = [aws_security_group.ecs_tasks.id]
      assign_public_ip = true
    }
  }
}
