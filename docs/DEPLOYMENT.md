# Deployment (fictional demo)

> **Warning:** every name, provider credential and ARN here is fictional. Examples use the
> all-zero account ID `000000000000`. Never use real telecom data or real credentials.

## Deploy

1. Install Terraform >= 1.5, configure the AWS CLI for a **demo** account, and install Docker.
2. Create SSM/Secrets Manager entries holding fictional credential strings and export their ARNs as
   `TF_VAR_llm_api_key_secret_arn`, `TF_VAR_database_url_secret_arn` and
   `TF_VAR_provider_secret_arns`. See `infra/terraform/terraform.tfvars.example`.
3. Build and push the six images (run from the repository root after `terraform apply` creates the
   ECR repositories):

   ```sh
   AWS_ACCOUNT=000000000000
   AWS_REGION=us-east-1
   TAG=demo
   aws ecr get-login-password --region "$AWS_REGION" | \
     docker login --username AWS --password-stdin "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"

   docker build -f Dockerfile.providers -t "aintegrator-demo-fibernet:$TAG" .
   docker tag "aintegrator-demo-fibernet:$TAG" "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/aintegrator-demo-fibernet:$TAG"
   docker push "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/aintegrator-demo-fibernet:$TAG"
   ```

   Repeat for `legacytel`, `skylink`, `webhook`, `reconciler`, and build the collector with
   `docker build -f infra/terraform/otel-collector/Dockerfile` for the `otel-collector` repository.
   `Dockerfile.providers` installs the optional `otlp` extra, so task definitions can set
   `OTEL_ENABLED=true` without a second image variant.
4. Apply the module:

   ```sh
   cd infra/terraform
   terraform init -backend=false
   terraform fmt -check
   terraform validate
   terraform plan
   terraform apply
   ```

   For a persistent backend, create the S3 bucket and lock table out of band, uncomment the
   `backend "s3"` example in `versions.tf`, and run `terraform init -migrate-state`.
5. Verify the outputs: `alb_dns_name`, `otlp_endpoint`, `state_machine_arn`,
   `ecr_repository_urls` and `cloud_map_namespace`. The webhook is reachable at
   `http://<alb_dns_name>:8000`. Plain HTTP is **demo-only**; real deployments require TLS.
6. Start an order execution with `{"order_id": "demo-order-1"}` and watch the state machine,
   the bridge Lambda logs and the X-Ray console. The DB reconciler remains authoritative
   regardless of the state machine outcome (ADR 004).

Tear down with `terraform destroy` (empty the ECR repositories first).

## docker-compose to AWS mapping

| docker-compose service | AWS resource in this module | Notes |
| --- | --- | --- |
| `postgres` | External managed Postgres/pgvector | Not provisioned by this demo module; pass its URL as a secret ARN. |
| `fibernet` | ECR repo + ECS Fargate service + Cloud Map name | `http://fibernet.aintegrator.local:8001` |
| `legacytel` | ECR repo + ECS Fargate service + Cloud Map name | `http://legacytel.aintegrator.local:8002` |
| `skylink` | ECR repo + ECS Fargate service + Cloud Map name | `http://skylink.aintegrator.local:8003` |
| `webhook` | ECR repo + ECS service behind a public ALB:8000 + Cloud Map name | Only publicly exposed service. |
| `reconciler` | ECR repo + EventBridge-scheduled ECS task | Also run once by Step Functions check/fallback states. |
| (no compose equivalent) | OTLP collector ECR repo + ECS service + SSM config | OTLP `4317`/`4318`, X-Ray and debug exporters. |
| (no compose equivalent) | SQS queue + DLQ + Lambda bridge | Completes `waitForTaskToken` tasks from fictional statuses. |
| (no compose equivalent) | Step Functions state machine + IAM roles | Complements, never replaces, the DB reconciler (ADR 004). |
