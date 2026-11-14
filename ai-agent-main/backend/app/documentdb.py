import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pymongo import ASCENDING, DESCENDING, MongoClient
from .config import settings

SYSTEM_PROMPT = """You are a Cloud-Native AI Agent powered by OpenAI GPT-4o mini via GitHub Models.
You operate inside an automated, switchable generic tool-calling loop spanning a TypeScript Frontend/Client and a Python FastAPI Backend deployed on AWS (EC2 Auto Scaling, RDS PostgreSQL, AWS DocumentDB, SNS/SQS, and AWS Lambda).
Use registered tools whenever needed and leverage windowed conversation history from AWS DocumentDB."""


class DocumentDBConversationStore:
    """
    Windowed Persistent Conversation History Store backed by AWS DocumentDB
    (with automatic local JSON fallback for offline development).
    """

    def __init__(self) -> None:
        self._client: Optional[MongoClient] = None
        self._collection = None
        self._docdb_failed = False
        self._local_file = os.path.join(os.getcwd(), "db.json")

    def _get_collection(self):
        if not settings.DOCDB_URI or self._docdb_failed:
            return None
        if self._collection is not None:
            return self._collection

        try:
            kwargs: Dict[str, Any] = {
                "retryWrites": False,  # Required by AWS DocumentDB
                "serverSelectionTimeoutMS": 3000,
            }
            if settings.DOCDB_TLS_CA_FILE:
                kwargs["tls"] = True
                kwargs["tlsCAFile"] = settings.DOCDB_TLS_CA_FILE

            self._client = MongoClient(settings.DOCDB_URI, **kwargs)
            self._client.admin.command("ping")
            db = self._client[settings.DOCDB_DB_NAME]
            self._collection = db[settings.DOCDB_COLLECTION]
            self._collection.create_index(
                [("sessionId", ASCENDING), ("createdAt", ASCENDING)]
            )
            return self._collection
        except Exception:
            self._docdb_failed = True
            return None

    def _read_local(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self._local_file):
            return []
        try:
            with open(self._local_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("messages", [])
        except Exception:
            return []

    def _write_local(self, messages: List[Dict[str, Any]]) -> None:
        with open(self._local_file, "w", encoding="utf-8") as f:
            json.dump({"messages": messages}, f, indent=2)

    @staticmethod
    def _strip_metadata(doc: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = {
            k: v
            for k, v in doc.items()
            if k not in ("_id", "id", "sessionId", "createdAt", "windowIndex")
            and v is not None
        }
        return cleaned

    def add_messages(
        self, messages: List[Dict[str, Any]], session_id: str = "default-session"
    ) -> None:
        enriched = []
        for msg in messages:
            item = dict(msg)
            item["id"] = str(uuid.uuid4())
            item["sessionId"] = session_id
            item["createdAt"] = datetime.now(timezone.utc).isoformat()
            enriched.append(item)

        collection = self._get_collection()
        if collection is not None:
            if enriched:
                collection.insert_many(enriched)
            return

        existing = self._read_local()
        existing.extend(enriched)
        self._write_local(existing)

    def get_full_history(
        self, session_id: str = "default-session"
    ) -> List[Dict[str, Any]]:
        collection = self._get_collection()
        if collection is not None:
            docs = list(
                collection.find({"sessionId": session_id}).sort("createdAt", ASCENDING)
            )
            return [self._strip_metadata(d) for d in docs]

        all_msgs = self._read_local()
        return [
            self._strip_metadata(m)
            for m in all_msgs
            if m.get("sessionId", "default-session") == session_id
        ]

    @staticmethod
    def apply_sliding_window(
        messages: List[Dict[str, Any]],
        window_size: int = 10,
        include_system_prompt: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Applies a sliding window over conversation history while guaranteeing
        that tool responses (`role == 'tool'`) are never separated from their
        originating `assistant` `tool_calls` message.
        """
        if window_size <= 0 or len(messages) <= window_size:
            sliced = list(messages)
        else:
            start_idx = max(0, len(messages) - window_size)
            while start_idx > 0 and messages[start_idx].get("role") == "tool":
                start_idx -= 1
            sliced = messages[start_idx:]

        if include_system_prompt and (
            not sliced or sliced[0].get("role") != "system"
        ):
            return [{"role": "system", "content": SYSTEM_PROMPT}] + sliced
        return sliced

    def get_windowed_history(
        self,
        session_id: str = "default-session",
        window_size: Optional[int] = None,
        include_system_prompt: bool = True,
    ) -> List[Dict[str, Any]]:
        effective_window = (
            window_size if window_size is not None else settings.MEMORY_WINDOW_SIZE
        )
        collection = self._get_collection()

        if collection is not None:
            fetch_limit = max(effective_window + 6, 12)
            recent_docs = list(
                collection.find({"sessionId": session_id})
                .sort("createdAt", DESCENDING)
                .limit(fetch_limit)
            )
            recent_docs.reverse()
            stripped = [self._strip_metadata(d) for d in recent_docs]
            return self.apply_sliding_window(
                stripped, effective_window, include_system_prompt
            )

        full_msgs = self.get_full_history(session_id)
        return self.apply_sliding_window(
            full_msgs, effective_window, include_system_prompt
        )

    def clear_session(self, session_id: str = "default-session") -> None:
        collection = self._get_collection()
        if collection is not None:
            collection.delete_many({"sessionId": session_id})
            return

        remaining = [
            m
            for m in self._read_local()
            if m.get("sessionId", "default-session") != session_id
        ]
        self._write_local(remaining)

    def check_health(self) -> Dict[str, Any]:
        collection = self._get_collection()
        return {
            "connected": True,
            "engine": "aws-documentdb" if collection is not None else "local-json-fallback",
            "database": settings.DOCDB_DB_NAME,
            "collection": settings.DOCDB_COLLECTION,
            "defaultWindowSize": settings.MEMORY_WINDOW_SIZE,
        }


docdb_store = DocumentDBConversationStore()
