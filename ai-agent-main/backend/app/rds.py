import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import settings

Base = declarative_base()


class User(Base):
    """RDS PostgreSQL User table shared with AWS Lambda email verification service."""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    first_name = Column(String(120), nullable=False)
    last_name = Column(String(120), nullable=False)
    password = Column(String(255), nullable=False)
    username = Column(String(255), unique=True, nullable=False, index=True)
    verified = Column(Boolean, nullable=False, default=False)
    verification_token = Column(String(255), nullable=True)
    verification_expiry_timestamp = Column(DateTime, nullable=True)
    account_created = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    account_updated = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class CustomToolApiModel(Base):
    """Persists dynamically registered REST API tools in RDS PostgreSQL."""

    __tablename__ = "agent_custom_tools"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(120), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=False)
    endpoint_url = Column(String(512), nullable=False)
    http_method = Column(String(16), nullable=False, default="POST")
    parameters_schema = Column(Text, nullable=False)
    headers_json = Column(Text, nullable=True, default="{}")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


class AgentAuditLog(Base):
    """Stores structured telemetry for agent tool-calling loop runs in RDS PostgreSQL."""

    __tablename__ = "agent_audit_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(120), nullable=False, index=True)
    execution_mode = Column(String(64), nullable=False)
    model = Column(String(120), nullable=False)
    tool_name = Column(String(120), nullable=True)
    latency_ms = Column(Integer, nullable=False, default=0)
    status = Column(String(64), nullable=False, default="SUCCESS")
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


def _create_db_engine():
    try:
        db_url = settings.DATABASE_URL
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+pg8000://", 1)
        eng = create_engine(
            db_url,
            pool_pre_ping=True,
            connect_args={"timeout": 3},
        )
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        Base.metadata.create_all(bind=eng)
        return eng, True
    except Exception:
        # Fallback to local SQLite when RDS PostgreSQL is not reachable in local dev
        fallback_engine = create_engine(
            "sqlite:///./local_rds_fallback.db",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=fallback_engine)
        return fallback_engine, False


engine, is_rds_postgres_connected = _create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def check_rds_health() -> Dict[str, Any]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "connected": True,
            "engine": "aws-rds-postgresql" if is_rds_postgres_connected else "sqlite-local-fallback",
            "host": settings.DATABASE_HOST,
            "database": settings.DATABASE_NAME,
        }
    except Exception as exc:
        return {
            "connected": False,
            "error": str(exc),
        }


def log_agent_execution(
    session_id: str,
    execution_mode: str,
    model: str,
    tool_name: Optional[str],
    latency_ms: int,
    status: str = "SUCCESS",
) -> None:
    try:
        with SessionLocal() as db:
            entry = AgentAuditLog(
                session_id=session_id,
                execution_mode=execution_mode,
                model=model,
                tool_name=tool_name,
                latency_ms=latency_ms,
                status=status,
            )
            db.add(entry)
            db.commit()
    except Exception:
        pass
