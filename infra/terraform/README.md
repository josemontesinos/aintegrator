# Terraform — AIntegrator fictional AWS demo

> **Warning:** this is a **fictional demo**. All provider names, credentials, accounts and ARNs are
> invented. The examples use the all-zero account ID `000000000000`. Do not deploy this module with
> real credentials, real provider data or a production AWS account.

## Prerequisites

- Terraform >= 1.5 and the AWS CLI configured for a demo account.
- Docker, to build and push the six demo images to the ECR repositories this module creates.
- SSM String/SecureString parameters or Secrets Manager secrets for the fictional credentials.

## Bootstrap and state backend

The S3 backend is intentionally **commented out** in `versions.tf` so CI can run
`terraform init -backend=false`. For persistent state, create an S3 bucket and a DynamoDB lock
table out of band, uncomment the `backend "s3"` block, then run:

```sh
terraform init -migrate-state
```

## Apply and destroy

```sh
cp terraform.tfvars.example terraform.tfvars   # then edit non-secret values only
terraform init -backend=false                  # or `terraform init` after enabling the backend
terraform fmt -check
terraform validate
terraform plan
terraform apply
```

Destroy with `terraform destroy`. Empty the ECR repositories first if images were pushed.

## Secrets input

No password or API key value is ever written in HCL or in `terraform.tfvars`. Supply ARNs only:

```sh
export TF_VAR_llm_api_key_secret_arn="arn:aws:secretsmanager:us-east-1:000000000000:secret:aintegrator/llm-placeholder"
export TF_VAR_database_url_secret_arn="arn:aws:secretsmanager:us-east-1:000000000000:secret:aintegrator/db-placeholder"
export TF_VAR_provider_secret_arns='{"FIBERNET_CLIENT_ID":"arn:aws:ssm:us-east-1:000000000000:parameter/aintegrator/fibernet-id","FIBERNET_CLIENT_SECRET":"arn:aws:ssm:us-east-1:000000000000:parameter/aintegrator/fibernet-secret"}'
```

The task execution role may read these ARNs; ECS injects them as container secrets. Use plain
string parameters/secrets, not JSON-keyed secrets, because no `key` field is configured.

## Architecture summary

- VPC with two public subnets, an IGW and a private Cloud Map namespace `aintegrator.local`.
- ECS Fargate services for `fibernet:8001`, `legacytel:8002`, `skylink:8003`, the webhook receiver
  and the OTLP collector. Service discovery gives stable names such as
  `http://fibernet.aintegrator.local:8001`.
- Only the webhook is exposed, through a public ALB on port **8000** over HTTP. This is acceptable
  only for the fictional demo; a real deployment must terminate TLS and restrict ingress.
- The collector uses the AWS Distro for OpenTelemetry, receives OTLP on gRPC `4317` and HTTP
  `4318`, and exports traces to AWS X-Ray plus debug output. Its config lives in an SSM String
  parameter, and its task role has X-Ray write permission.
- A scheduled EventBridge rule runs the authoritative DB reconciler every `var.reconcile_schedule`.
- A Step Functions state machine runs the reconciler once, waits on an SQS task token, and uses a
  reconciler fallback on timeout. **ADR 004:** it *complements* the DB reconciler; it never
  replaces it. A dependency-free Lambda bridge reads SQS statuses and calls `SendTaskSuccess` or
  `SendTaskFailure`.

See `../../docs/DEPLOYMENT.md` for image build/push steps and the docker-compose mapping.
