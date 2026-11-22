terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
  }

  # Remote state example (kept commented so `terraform init -backend=false` works).
  # Create the bucket/table out of band, uncomment, then run `terraform init -migrate-state`.
  # backend "s3" {
  #   bucket         = "replace-with-your-demo-state-bucket"
  #   key            = "aintegrator/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "replace-with-your-demo-lock-table"
  #   encrypt        = true
  # }
}
