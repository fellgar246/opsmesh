"""SQS consumer. One event id is stored once.

A repeated delivery does not write a second checkpoint and does not create an order.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError


def handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    table = boto3.resource("dynamodb").Table(os.environ["CHECKPOINT_TABLE"])
    for record in event.get("Records", []):
        detail = _detail(record.get("body", ""))
        event_id = str(detail.get("event_id") or record.get("messageId"))
        try:
            table.put_item(
                Item={
                    "event_id": event_id,
                    "event_type": str(detail.get("event_type") or ""),
                },
                ConditionExpression="attribute_not_exists(event_id)",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code != "ConditionalCheckFailedException":
                raise
    return {"batchItemFailures": []}


def _detail(body: str) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    detail = parsed.get("detail", parsed)
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            return {}
    if not isinstance(detail, dict):
        return {}
    return detail
