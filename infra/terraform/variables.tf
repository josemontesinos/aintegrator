variable "aws_region" {
  description = "AWS region used for the fictional demo."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for all AWS resource names."
  type        = string
  default     = "aintegrator-demo"
}

variable "image_tag" {
  description = "Tag pushed to every ECR repository and referenced by the task definitions."
  type        = string
  default     = "demo"
}

variable "vpc_cidr" {
  description = "CIDR block for the demo VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "Two public subnet CIDRs, one per availability zone."
  type        = list(string)
  default     = ["10.42.1.0/24", "10.42.2.0/24"]

  validation {
    condition     = length(var.public_subnet_cidrs) == 2
    error_message = "Provide exactly two public subnet CIDRs."
  }
}

variable "availability_zones" {
  description = "Two availability zones paired with public_subnet_cidrs by position."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]

  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "Provide exactly two availability zones."
  }
}

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the public webhook ALB (documentation-range demo value)."
  type        = list(string)
  default     = ["203.0.113.0/24"]
}

variable "reconcile_schedule" {
  description = "EventBridge schedule for the authoritative DB reconciler task."
  type        = string
  default     = "rate(5 minutes)"
}

variable "step_function_wait_seconds" {
  description = "How long the task-token wait state waits before the reconciler fallback."
  type        = number
  default     = 300
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days for ECS tasks and the bridge Lambda."
  type        = number
  default     = 14
}

variable "llm_api_key_secret_arn" {
  description = "SSM/Secrets Manager ARN for the fictional LLM key; pass via TF_VAR_*."
  type        = string
  sensitive   = true
  default     = null
}

variable "database_url_secret_arn" {
  description = "SSM/Secrets Manager ARN for the fictional database URL; pass via TF_VAR_*."
  type        = string
  sensitive   = true
  default     = null
}

variable "provider_secret_arns" {
  description = "Map of fictional provider credential ARNs (SSM/Secrets Manager); pass via TF_VAR_*."
  type        = map(string)
  sensitive   = true
  default     = {}
}
