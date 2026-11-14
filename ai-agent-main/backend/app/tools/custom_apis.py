import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import boto3
from fastapi import APIRouter
from pydantic import BaseModel, Field
from ..config import settings
from ..documentdb import docdb_store
from ..rds import AgentAuditLog, SessionLocal, User, check_rds_health
from ..tool_registry import tool_registry

router = APIRouter(prefix="/api/v1/apis", tags=["Customizable Agent REST APIs"])


# ============================================================================
# HOW TO ADD A NEW CUSTOM API IN PYTHON FASTAPI:
# 1. Define a Pydantic input schema below.
# 2. Decorate your function with `@tool_registry.register_api(...)` and optionally
#    `@router.post(...)` so it is both an HTTP REST endpoint AND an automated
#    LLM tool in the TypeScript & Python GPT-4o-mini tool-calling loop!
# ============================================================================


class WeatherToolInput(BaseModel):
    location: str = Field(
        default="Boston, MA", description="City and state/country to check weather for"
    )
    reasoning: str = Field(
        default="User requested weather info", description="Why did you pick this tool?"
    )


@router.post("/weather")
@tool_registry.register_api(
    name="get_weather",
    description="Get current weather conditions and forecast for a given location via Python FastAPI.",
    args_schema=WeatherToolInput,
    endpoint_path="/api/v1/apis/weather",
)
async def get_weather_api(
    location: str = "Boston, MA",
    reasoning: str = "User requested weather",
    **kwargs: Any,
) -> Dict[str, Any]:
    return {
        "location": location,
        "temperature": "72°F (22°C)",
        "condition": "Partly Cloudy",
        "humidity": "48%",
        "reasoning": reasoning,
        "provider": "python-fastapi-weather-service",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class RdsStatusInput(BaseModel):
    includeAuditLogs: bool = Field(
        default=True,
        description="Whether to include recent agent execution audit logs from RDS PostgreSQL",
    )
    reasoning: str = Field(
        default="Checking RDS PostgreSQL status",
        description="Why did you pick this tool?",
    )


@router.post("/rds-status")
@tool_registry.register_api(
    name="check_rds_postgres_status",
    description="Queries AWS RDS PostgreSQL for database health, registered user verification statistics, and recent AI agent tool execution audit logs.",
    args_schema=RdsStatusInput,
    endpoint_path="/api/v1/apis/rds-status",
)
async def check_rds_postgres_status_api(
    includeAuditLogs: bool = True,
    reasoning: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    health = check_rds_health()
    with SessionLocal() as db:
        total_users = db.query(User).count()
        verified_users = db.query(User).filter(User.verified.is_(True)).count()
        recent_logs = []
        if includeAuditLogs:
            logs = (
                db.query(AgentAuditLog)
                .order_by(AgentAuditLog.created_at.desc())
                .limit(5)
                .all()
            )
            recent_logs = [
                {
                    "session_id": l.session_id,
                    "mode": l.execution_mode,
                    "model": l.model,
                    "tool": l.tool_name,
                    "latency_ms": l.latency_ms,
                    "status": l.status,
                    "created_at": l.created_at.isoformat() if l.created_at else None,
                }
                for l in logs
            ]

    return {
        "rdsHealth": health,
        "userMetrics": {
            "totalUsers": total_users,
            "verifiedUsers": verified_users,
        },
        "recentAgentAuditLogs": recent_logs,
        "reasoning": reasoning,
    }


class SnsNotificationInput(BaseModel):
    email: str = Field(..., description="Recipient email address")
    eventType: str = Field(
        default="VERIFY_EMAIL",
        description="Event type: VERIFY_EMAIL or AGENT_TASK_ALERT",
    )
    message: str = Field(
        default="Please verify your email address",
        description="Notification message body",
    )


@router.post("/notify-sns")
@tool_registry.register_api(
    name="publish_sns_notification",
    description="Publishes a notification message to AWS SNS (which fans out to AWS SQS and triggers the AWS Lambda email verification/notification function).",
    args_schema=SnsNotificationInput,
    endpoint_path="/api/v1/apis/notify-sns",
)
async def publish_sns_notification_api(
    email: str,
    eventType: str = "VERIFY_EMAIL",
    message: str = "Notification from Cloud-Native AI Agent",
    **kwargs: Any,
) -> Dict[str, Any]:
    token = str(uuid.uuid4())
    payload = {
        "email": email,
        "token": token,
        "eventType": eventType,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if settings.SNS_TOPIC_ARN:
        try:
            sns_client = boto3.client("sns", region_name=settings.AWS_REGION)
            response = sns_client.publish(
                TopicArn=settings.SNS_TOPIC_ARN,
                Message=json.dumps(payload),
                Subject=f"AI Agent Event: {eventType}",
            )
            return {
                "status": "PUBLISHED_TO_AWS_SNS",
                "topicArn": settings.SNS_TOPIC_ARN,
                "messageId": response.get("MessageId"),
                "payload": payload,
            }
        except Exception as exc:
            return {
                "status": "SNS_ERROR",
                "error": str(exc),
                "payload": payload,
            }

    return {
        "status": "SIMULATED_SNS_SQS_PUBLISH",
        "note": "Set SNS_TOPIC_ARN in .env to publish live to AWS SNS -> SQS -> Lambda",
        "payload": payload,
    }


class DocumentDBWindowStatsInput(BaseModel):
    sessionId: str = Field(
        default="default-session",
        description="Conversation session ID stored in AWS DocumentDB",
    )
    windowSize: int = Field(
        default=10,
        description="Sliding window size to inspect",
    )


@router.post("/docdb-window-stats")
@tool_registry.register_api(
    name="inspect_documentdb_conversation_window",
    description="Inspects the AWS DocumentDB persistent conversation store for a session, comparing total persisted messages against the active sliding context window.",
    args_schema=DocumentDBWindowStatsInput,
    endpoint_path="/api/v1/apis/docdb-window-stats",
)
async def inspect_documentdb_window_api(
    sessionId: str = "default-session",
    windowSize: int = 10,
    **kwargs: Any,
) -> Dict[str, Any]:
    full_history = docdb_store.get_full_history(sessionId)
    windowed_history = docdb_store.get_windowed_history(
        sessionId, window_size=windowSize, include_system_prompt=False
    )
    return {
        "sessionId": sessionId,
        "storageEngine": docdb_store.check_health()["engine"],
        "totalPersistedMessages": len(full_history),
        "activeWindowSizeLimit": windowSize,
        "windowedMessagesReturned": len(windowed_history),
    }
