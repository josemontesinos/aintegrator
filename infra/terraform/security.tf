resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public HTTP ingress for the fictional webhook demo"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Webhook traffic from the allow-listed test networks"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = var.alb_ingress_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-alb" }
}

resource "aws_security_group" "webhook" {
  name        = "${local.name}-webhook"
  description = "Allow the demo ALB to reach the webhook receiver"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "From the demo ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-webhook" }
}

resource "aws_security_group" "ecs_tasks" {
  name        = "${local.name}-ecs-tasks"
  description = "Fictional demo task-to-task traffic"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Provider and webhook HTTP between demo tasks"
    from_port       = 8000
    to_port         = 8003
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  ingress {
    description     = "OTLP gRPC and HTTP from demo tasks"
    from_port       = 4317
    to_port         = 4318
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-ecs-tasks" }
}
