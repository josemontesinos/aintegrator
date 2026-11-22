output "cluster_name" {
  description = "Name of the fictional ECS Fargate cluster."
  value       = aws_ecs_cluster.main.name
}

output "alb_dns_name" {
  description = "Public DNS name of the webhook ALB (HTTP only in this demo)."
  value       = aws_lb.webhook.dns_name
}

output "otlp_endpoint" {
  description = "OTLP/gRPC endpoint exposed by the collector through Cloud Map."
  value       = local.endpoints.otlp_grpc
}

output "state_machine_arn" {
  description = "ARN of the order-lifecycle state machine that complements the DB reconciler."
  value       = aws_sfn_state_machine.order_lifecycle.arn
}

output "ecr_repository_urls" {
  description = "Map of fictional service names to their ECR repository URLs."
  value       = { for name, repository in aws_ecr_repository.app : name => repository.repository_url }
}

output "cloud_map_namespace" {
  description = "Private DNS namespace used for service discovery."
  value       = aws_service_discovery_private_dns_namespace.main.name
}
