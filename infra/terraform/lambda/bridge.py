"""Dependency-free bridge from SQS task tokens to Step Functions callbacks."""

import json
import os

import boto3

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
SUCCESS_STATUSES = {"accepted", "completed", "shipped"}
FAILURE_STATUSES = {"cancelled", "failed", "rejected"}
FAILURE_ERROR = "ProviderStatusRejected"

_stepfunctions = boto3.client("stepfunctions")
print(f"sfn-bridge configured for {STATE_MACHINE_ARN}")


def handler(event, context):
    """Complete one or more fictional waitForTaskToken tasks from SQS statuses."""
    del context  # Lambda supplies a context object that the bridge does not need.
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        token = body.get("TaskToken") or body.get("taskToken")
        if not token:
            raise ValueError("SQS message is missing a Step Functions task token")
        status = str(body.get("status", "")).lower()
        result = {"order_id": body.get("order_id"), "status": status}
        if status in SUCCESS_STATUSES:
            _stepfunctions.send_task_success(taskToken=token, output=json.dumps(result))
        elif status in FAILURE_STATUSES:
            _stepfunctions.send_task_failure(
                taskToken=token,
                error=FAILURE_ERROR,
                cause=json.dumps(result),
            )
        else:
            raise ValueError(f"unsupported fictional status: {status!r}")
