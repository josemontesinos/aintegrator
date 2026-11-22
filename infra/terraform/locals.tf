locals {
  name = var.name_prefix

  common_tags = {
    Project     = "AIntegrator"
    Environment = "demo"
    ManagedBy   = "Terraform"
  }

  namespace_name = "aintegrator.local"

  endpoints = {
    fibernet  = "http://fibernet.${local.namespace_name}:8001"
    legacytel = "http://legacytel.${local.namespace_name}:8002"
    skylink   = "http://skylink.${local.namespace_name}:8003"
    webhook   = "http://webhook.${local.namespace_name}:8000"
    otlp_grpc = "http://otel-collector.${local.namespace_name}:4317"
    otlp_http = "http://otel-collector.${local.namespace_name}:4318"
  }

  repositories = toset([
    "fibernet",
    "legacytel",
    "skylink",
    "webhook",
    "reconciler",
    "otel-collector",
  ])

  app_services = {
    fibernet = {
      port    = 8001
      command = ["uvicorn", "aintegrator_fibernet.app:app", "--host", "0.0.0.0", "--port", "8001"]
    }
    legacytel = {
      port    = 8002
      command = ["python", "-m", "flask", "--app", "aintegrator_legacytel.app:app", "run", "--host", "0.0.0.0", "--port", "8002"]
    }
    skylink = {
      port    = 8003
      command = ["sh", "-c", "python -m aintegrator_skylink.manage migrate --noinput && python -m aintegrator_skylink.manage runserver 0.0.0.0:8003 --noreload"]
    }
    webhook = {
      port    = 8000
      command = ["sh", "-c", "alembic -c alembic.ini upgrade head && uvicorn aintegrator_integration.webhooks.receiver:app --host 0.0.0.0 --port 8000"]
    }
  }

  discovery_services = merge(
    { for service, config in local.app_services : service => { port = config.port } },
    { otel_collector = { port = 4317 } }
  )

  provider_secret_env = {
    for service, names in {
      fibernet  = ["FIBERNET_CLIENT_ID", "FIBERNET_CLIENT_SECRET"]
      legacytel = ["LEGACYTEL_USERNAME", "LEGACYTEL_PASSWORD"]
      skylink   = ["SKYLINK_API_KEY", "SKYLINK_WEBHOOK_SECRET"]
      } : service => {
      for name in names : name => var.provider_secret_arns[name]
      if contains(keys(var.provider_secret_arns), name)
    }
  }

  all_provider_secret_env = merge(values(local.provider_secret_env)...)

  webhook_secret_env = var.llm_api_key_secret_arn == null ? {} : {
    LLM_API_KEY = var.llm_api_key_secret_arn
  }

  database_secret_env = var.database_url_secret_arn == null ? {} : {
    DATABASE_URL = var.database_url_secret_arn
  }

  base_env = [
    { name = "ENVIRONMENT", value = "demo" },
    { name = "OTEL_ENABLED", value = "true" },
    { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = local.endpoints.otlp_http },
  ]

  integration_env = [
    { name = "FIBERNET_BASE_URL", value = local.endpoints.fibernet },
    { name = "LEGACYTEL_BASE_URL", value = local.endpoints.legacytel },
    { name = "SKYLINK_BASE_URL", value = local.endpoints.skylink },
    { name = "WEBHOOK_RECEIVER_BASE_URL", value = local.endpoints.webhook },
    { name = "SKYLINK_WEBHOOK_URL", value = "${local.endpoints.webhook}/webhooks/skylink" },
  ]

  task_env = {
    for service in keys(local.app_services) : service => concat(
      local.base_env,
      service == "webhook" ? local.integration_env : [],
      [{ name = "OTEL_SERVICE_NAME", value = "${local.name}-${service}" }]
    )
  }

  task_secrets = merge(
    {
      for service, secrets in local.provider_secret_env : service => [
        for name, arn in secrets : { name = name, valueFrom = arn }
      ]
    },
    {
      webhook = concat(
        [for name, arn in local.all_provider_secret_env : { name = name, valueFrom = arn }],
        [for name, arn in local.webhook_secret_env : { name = name, valueFrom = arn }],
        [for name, arn in local.database_secret_env : { name = name, valueFrom = arn }]
      )
    }
  )

  # The state machine uses the same reconciler code as the scheduled task (ADR 004).
  reconcile_task_defs = {
    scheduled = {
      command = ["python", "-m", "aintegrator_integration.reconcile", "--interval", "10"]
      step    = "scheduled"
    }
    check = {
      command = ["python", "-m", "aintegrator_integration.reconcile", "--once"]
      step    = "sfn-check"
    }
    fallback = {
      command = ["python", "-m", "aintegrator_integration.reconcile", "--once"]
      step    = "sfn-fallback"
    }
  }

  secret_arns = concat(
    values(var.provider_secret_arns),
    var.llm_api_key_secret_arn == null ? [] : [var.llm_api_key_secret_arn],
    var.database_url_secret_arn == null ? [] : [var.database_url_secret_arn],
    [aws_ssm_parameter.otel_config.arn]
  )
}
