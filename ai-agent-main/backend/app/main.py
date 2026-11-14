import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import bcrypt
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, EmailStr, Field

from .agent import run_python_agent_loop
from .config import settings
from .documentdb import docdb_store
from .rds import SessionLocal, User, check_rds_health
from .tool_registry import tool_registry
from .tools.custom_apis import publish_sns_notification_api, router as custom_apis_router

app = FastAPI(
    title="Cloud-Native Agentic AI Tool",
    description=(
        "TypeScript & Python AI Agent utilizing OpenAI GPT-4o mini via GitHub Models "
        "with an automated switchable generic tool-calling loop, AWS DocumentDB windowed "
        "conversation history, AWS RDS PostgreSQL, and AWS SNS/SQS/Lambda event pipelines."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the customizable REST API router (/api/v1/apis/*)
app.include_router(custom_apis_router)


class AgentChatRequest(BaseModel):
    message: str = Field(..., description="User message to send to the AI agent")
    session_id: str = Field(
        default="default-session", description="Conversation session ID in DocumentDB"
    )
    window_size: int = Field(
        default=10, description="Sliding window size for DocumentDB conversation memory"
    )
    execution_mode: str = Field(
        default="python-fastapi",
        description="Switchable execution mode: 'python-fastapi' | 'hybrid' | 'typescript'",
    )


class DynamicToolRegisterRequest(BaseModel):
    name: str = Field(..., description="Unique function/tool name for OpenAI tool-calling")
    description: str = Field(..., description="Description telling GPT-4o mini when to invoke this API")
    endpoint_url: str = Field(..., description="Target REST API URL")
    http_method: str = Field(default="POST", description="HTTP method (GET, POST, PUT, DELETE)")
    parameters_schema: Dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Query string"}},
        },
        description="JSON Schema for tool parameters",
    )
    headers: Optional[Dict[str, str]] = Field(default=None)


class CreateUserRequest(BaseModel):
    first_name: str
    last_name: str
    username: EmailStr
    password: str


@app.get("/healthz", tags=["Cloud-Native Health Check"])
async def healthz(response: Response):
    """
    AWS Application Load Balancer & Auto Scaling Group health check endpoint.
    Verifies RDS PostgreSQL and AWS DocumentDB connectivity.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    rds_status = check_rds_health()
    docdb_status = docdb_store.check_health()

    return {
        "status": "healthy",
        "model": settings.AI_MODEL,
        "inferenceEndpoint": settings.GITHUB_MODELS_ENDPOINT,
        "rdsPostgres": rds_status,
        "documentDb": docdb_status,
        "registeredToolsCount": len(tool_registry.list_tools()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/v1/agent/chat", tags=["AI Agent Loop"])
async def agent_chat(payload: AgentChatRequest):
    """
    Executes the Automated Generic Tool-Calling Loop with OpenAI GPT-4o mini via GitHub Models
    and windowed conversation history stored in AWS DocumentDB.
    """
    try:
        result = await run_python_agent_loop(
            user_message=payload.message,
            session_id=payload.session_id,
            window_size=payload.window_size,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/v1/tools", tags=["Generic Tool Registry"])
async def list_registered_tools():
    """
    Returns all tools registered in Python FastAPI and RDS PostgreSQL so the
    TypeScript Frontend/Agent can auto-discover and invoke them in 'hybrid' mode.
    """
    return {"tools": tool_registry.list_tools()}


@app.post("/api/v1/tools/register", tags=["Generic Tool Registry"])
async def register_custom_tool(payload: DynamicToolRegisterRequest):
    """
    Dynamically registers a new custom REST API into the generic tool-calling loop
    and persists its definition in AWS RDS PostgreSQL.
    """
    tool = tool_registry.register_external_rest_tool(
        name=payload.name,
        description=payload.description,
        endpoint_url=payload.endpoint_url,
        http_method=payload.http_method,
        parameters_schema=payload.parameters_schema,
        headers=payload.headers,
        persist_to_rds=True,
    )
    return {
        "status": "registered",
        "tool": tool.to_metadata(),
    }


@app.delete("/api/v1/tools/{tool_name}", tags=["Generic Tool Registry"])
async def delete_custom_tool(tool_name: str):
    removed = tool_registry.unregister_tool(tool_name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    return {"status": "deleted", "name": tool_name}


@app.post("/api/v1/tools/{tool_name}/execute", tags=["Generic Tool Registry"])
async def execute_tool_endpoint(tool_name: str, body: Dict[str, Any]):
    """
    Executes a registered Python FastAPI tool on behalf of the TypeScript Agent loop.
    """
    args = body.get("toolArgs", body)
    try:
        output = await tool_registry.execute_tool(tool_name, args)
        try:
            return json.loads(output)
        except Exception:
            return {"result": output}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/v1/conversations/{session_id}", tags=["DocumentDB Windowed Memory"])
async def get_conversation_history(
    session_id: str,
    window_size: int = Query(default=10, ge=1, le=200),
):
    """
    Retrieves both the active sliding window and the full persistent history
    stored in AWS DocumentDB for the given session.
    """
    full_history = docdb_store.get_full_history(session_id)
    windowed_history = docdb_store.get_windowed_history(
        session_id=session_id,
        window_size=window_size,
        include_system_prompt=False,
    )
    return {
        "sessionId": session_id,
        "windowSize": window_size,
        "windowedCount": len(windowed_history),
        "totalStoredCount": len(full_history),
        "windowedMessages": windowed_history,
        "fullHistory": full_history,
    }


@app.delete("/api/v1/conversations/{session_id}", tags=["DocumentDB Windowed Memory"])
async def clear_conversation_history(session_id: str):
    docdb_store.clear_session(session_id)
    return {"status": "cleared", "sessionId": session_id}


@app.post("/v1/user", status_code=201, tags=["RDS PostgreSQL Users & SNS/SQS Verification"])
async def create_user(payload: CreateUserRequest):
    """
    Creates a user in AWS RDS PostgreSQL and publishes a verification event
    to AWS SNS -> AWS SQS -> AWS Lambda.
    """
    with SessionLocal() as db:
        existing = db.query(User).filter(User.username == payload.username).first()
        if existing:
            raise HTTPException(status_code=400, detail="User with this email already exists")

        hashed_pw = bcrypt.hashpw(
            payload.password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        token = str(uuid.uuid4())

        user = User(
            id=str(uuid.uuid4()),
            first_name=payload.first_name,
            last_name=payload.last_name,
            username=payload.username,
            password=hashed_pw,
            verified=False,
            verification_token=token,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        sns_result = await publish_sns_notification_api(
            email=user.username,
            eventType="VERIFY_EMAIL",
            message=f"Verify account for {user.first_name} {user.last_name}",
        )

        return {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "verified": user.verified,
            "account_created": user.account_created.isoformat(),
            "account_updated": user.account_updated.isoformat(),
            "snsEvent": sns_result,
        }


@app.get("/v1/user/verify", tags=["RDS PostgreSQL Users & SNS/SQS Verification"])
async def verify_user_email(token: str = Query(...)):
    """
    Verifies a user's email token against the expiry timestamp set by the AWS Lambda function in RDS PostgreSQL.
    """
    with SessionLocal() as db:
        user = db.query(User).filter(User.verification_token == token).first()
        if not user:
            raise HTTPException(status_code=400, detail="Invalid verification token")

        if user.verified:
            return {"status": "already_verified", "username": user.username}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if (
            user.verification_expiry_timestamp
            and now > user.verification_expiry_timestamp.replace(tzinfo=None)
        ):
            raise HTTPException(status_code=400, detail="Verification link has expired")

        user.verified = True
        db.commit()
        return {"status": "verified", "username": user.username}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend_ui():
    frontend_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "frontend",
        "index.html",
    )
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path)
    return HTMLResponse("<h1>Cloud-Native AI Agent FastAPI Backend is running.</h1>")
