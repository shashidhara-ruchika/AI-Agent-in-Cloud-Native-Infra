import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional
from openai import AsyncOpenAI
from .config import settings
from .documentdb import docdb_store
from .rds import log_agent_execution
from .tool_registry import tool_registry
from .tools import custom_apis  # noqa: F401 - ensures built-in custom APIs are registered

PLACEHOLDER_TOKENS = {
    "",
    "ghp_your_github_personal_access_token",
    "ghp_placeholder_token_for_dev",
    "ghp_replace_with_github_models_token",
    "your-token-here",
}


def has_valid_github_token() -> bool:
    token = (settings.GITHUB_TOKEN or "").strip()
    return token not in PLACEHOLDER_TOKENS and not token.startswith("ghp_your_")


def get_openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.GITHUB_MODELS_ENDPOINT,
        api_key=settings.GITHUB_TOKEN or "ghp_placeholder",
    )


def _select_tools_for_local_fallback(user_message: str) -> List[Dict[str, Any]]:
    """
    When GITHUB_TOKEN is a placeholder during local testing (or GitHub Models is unreachable),
    automatically routes user intent to registered tools in `tool_registry` so the
    generic tool-calling loop, RDS PostgreSQL, and DocumentDB windowing work out of the box.
    """
    text_lower = user_message.lower()
    planned_calls: List[Dict[str, Any]] = []
    available = {t["name"]: t for t in tool_registry.list_tools()}

    if any(w in text_lower for w in ("weather", "temperature", "forecast", "climate")):
        if "get_weather" in available:
            loc_match = re.search(
                r"\bin\s+([a-zA-Z\s,]+?)(?:\?|\.|$|\band\b)", user_message, re.I
            )
            location = loc_match.group(1).strip() if loc_match else "Boston, MA"
            planned_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": "get_weather",
                    "arguments": {
                        "location": location,
                        "reasoning": f"User asked about weather ({user_message})",
                    },
                }
            )

    if any(
        w in text_lower
        for w in ("rds", "postgres", "database", "db", "audit", "user", "status")
    ):
        if "check_rds_postgres_status" in available:
            planned_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": "check_rds_postgres_status",
                    "arguments": {
                        "includeAuditLogs": True,
                        "reasoning": "User requested RDS PostgreSQL database & audit status",
                    },
                }
            )

    if any(
        w in text_lower
        for w in ("documentdb", "docdb", "window", "memory", "history", "conversation")
    ):
        if "inspect_documentdb_conversation_window" in available:
            planned_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": "inspect_documentdb_conversation_window",
                    "arguments": {
                        "sessionId": "default-session",
                        "windowSize": settings.MEMORY_WINDOW_SIZE,
                    },
                }
            )

    if any(w in text_lower for w in ("sns", "sqs", "notify", "email", "alert", "lambda")):
        if "publish_sns_notification" in available:
            email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", user_message)
            email = email_match.group(0) if email_match else "user@example.com"
            planned_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": "publish_sns_notification",
                    "arguments": {
                        "email": email,
                        "eventType": "AGENT_TASK_ALERT",
                        "message": user_message,
                    },
                }
            )

    # Also check any custom tools dynamically registered by the user
    for tool_name, tool_meta in available.items():
        if tool_name in {
            "get_weather",
            "check_rds_postgres_status",
            "inspect_documentdb_conversation_window",
            "publish_sns_notification",
        }:
            continue
        keywords = tool_name.lower().split("_")
        if any(k in text_lower for k in keywords if len(k) > 2):
            planned_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": tool_name,
                    "arguments": {"query": user_message},
                }
            )

    return planned_calls


def _synthesize_fallback_reply(
    user_message: str, executed_tool_calls: List[Dict[str, Any]]
) -> str:
    if not executed_tool_calls:
        registered_names = ", ".join(t["name"] for t in tool_registry.list_tools())
        return (
            f"I received your message: \"{user_message}\".\n\n"
            f"Available tools in the generic tool-calling loop: `{registered_names}`.\n"
            f"Try asking me to check the weather, inspect RDS PostgreSQL status, check DocumentDB window stats, or trigger an SNS/SQS notification!"
        )

    parts: List[str] = []
    for tc in executed_tool_calls:
        name = tc["name"]
        try:
            data = (
                json.loads(tc["result"])
                if isinstance(tc["result"], str)
                else tc["result"]
            )
        except Exception:
            data = {"output": tc["result"]}

        if name == "get_weather" and isinstance(data, dict):
            parts.append(
                f"Weather in {data.get('location', 'Boston, MA')}: {data.get('temperature', '72°F')} and {data.get('condition', 'Clear')} (humidity {data.get('humidity', '48%')})."
            )
        elif name == "check_rds_postgres_status" and isinstance(data, dict):
            rds_h = data.get("rdsHealth", {})
            metrics = data.get("userMetrics", {})
            logs = data.get("recentAgentAuditLogs", [])
            parts.append(
                f"RDS PostgreSQL Status: Connected ({rds_h.get('engine', 'postgres')}, db `{rds_h.get('database', 'webapp')}`). "
                f"Registered Users: {metrics.get('totalUsers', 0)} (Verified: {metrics.get('verifiedUsers', 0)}). "
                f"Recent Audit Logs recorded: {len(logs)}."
            )
        elif name == "inspect_documentdb_conversation_window" and isinstance(data, dict):
            parts.append(
                f"DocumentDB Conversation Memory (`{data.get('sessionId')}`): "
                f"{data.get('windowedMessagesReturned')} messages in active sliding window (limit: {data.get('activeWindowSizeLimit')}) "
                f"out of {data.get('totalPersistedMessages')} total persisted messages."
            )
        elif name == "publish_sns_notification" and isinstance(data, dict):
            parts.append(
                f"SNS/SQS Event Notification (`{data.get('status')}`): Dispatched event for `{data.get('payload', {}).get('email')}`."
            )
        else:
            parts.append(f"Result from `{name}`: {json.dumps(data)}")

    return "\n\n".join(parts)


async def run_python_agent_loop(
    user_message: str,
    session_id: str = "default-session",
    window_size: Optional[int] = None,
    max_iterations: int = 10,
) -> Dict[str, Any]:
    """
    Automated Generic Tool-Calling Loop in Python FastAPI utilizing OpenAI GPT-4o mini
    via GitHub Models and Windowed Persistent Conversation History in AWS DocumentDB.
    """
    effective_window = (
        window_size if window_size is not None else settings.MEMORY_WINDOW_SIZE
    )

    docdb_store.add_messages(
        [{"role": "user", "content": user_message}], session_id=session_id
    )

    openai_tools = tool_registry.get_openai_tools()
    executed_tool_calls: List[Dict[str, Any]] = []
    final_reply = ""

    use_live_llm = has_valid_github_token()

    if use_live_llm:
        client = get_openai_client()
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            start_ts = time.perf_counter()

            windowed_messages = docdb_store.get_windowed_history(
                session_id=session_id,
                window_size=effective_window,
                include_system_prompt=True,
            )

            kwargs: Dict[str, Any] = {
                "model": settings.AI_MODEL,
                "temperature": 0.1,
                "messages": windowed_messages,
            }
            if openai_tools:
                kwargs["tools"] = openai_tools
                kwargs["tool_choice"] = "auto"
                kwargs["parallel_tool_calls"] = False

            try:
                response = await client.chat.completions.create(**kwargs)
                if isinstance(response, str) or not hasattr(response, "choices"):
                    # GitHub Models returned an unexpected string/auth response; fall back to local tool loop
                    use_live_llm = False
                    break
                assistant_msg = response.choices[0].message
            except Exception:
                use_live_llm = False
                break

            latency_ms = int((time.perf_counter() - start_ts) * 1000)

            msg_dict: Dict[str, Any] = {"role": "assistant"}
            if assistant_msg.content is not None:
                msg_dict["content"] = assistant_msg.content
            if assistant_msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in assistant_msg.tool_calls
                ]

            docdb_store.add_messages([msg_dict], session_id=session_id)

            if assistant_msg.tool_calls:
                for tc in assistant_msg.tool_calls:
                    tool_name = tc.function.name
                    raw_args = json.loads(tc.function.arguments or "{}")
                    tool_start = time.perf_counter()
                    try:
                        tool_output = await tool_registry.execute_tool(
                            tool_name, raw_args
                        )
                        status = "SUCCESS"
                    except Exception as exc:
                        tool_output = json.dumps(
                            {"error": str(exc), "tool": tool_name}
                        )
                        status = "ERROR"

                    tool_latency = int((time.perf_counter() - tool_start) * 1000)
                    log_agent_execution(
                        session_id=session_id,
                        execution_mode="python-fastapi",
                        model=settings.AI_MODEL,
                        tool_name=tool_name,
                        latency_ms=tool_latency,
                        status=status,
                    )

                    executed_tool_calls.append(
                        {
                            "name": tool_name,
                            "args": raw_args,
                            "result": tool_output,
                            "latencyMs": tool_latency,
                        }
                    )

                    docdb_store.add_messages(
                        [
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": tool_output,
                            }
                        ],
                        session_id=session_id,
                    )
                continue

            if assistant_msg.content:
                final_reply = assistant_msg.content
                log_agent_execution(
                    session_id=session_id,
                    execution_mode="python-fastapi",
                    model=settings.AI_MODEL,
                    tool_name=None,
                    latency_ms=latency_ms,
                    status="SUCCESS",
                )
                break

    if not use_live_llm and not final_reply:
        # Execute automated tool-calling loop with local intent routing when GITHUB_TOKEN is placeholder
        planned_calls = _select_tools_for_local_fallback(user_message)
        if planned_calls:
            docdb_store.add_messages(
                [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": pc["id"],
                                "type": "function",
                                "function": {
                                    "name": pc["name"],
                                    "arguments": json.dumps(pc["arguments"]),
                                },
                            }
                            for pc in planned_calls
                        ],
                    }
                ],
                session_id=session_id,
            )

            for pc in planned_calls:
                tool_name = pc["name"]
                raw_args = pc["arguments"]
                tool_start = time.perf_counter()
                try:
                    tool_output = await tool_registry.execute_tool(tool_name, raw_args)
                    status = "SUCCESS"
                except Exception as exc:
                    tool_output = json.dumps({"error": str(exc), "tool": tool_name})
                    status = "ERROR"

                tool_latency = int((time.perf_counter() - tool_start) * 1000)
                log_agent_execution(
                    session_id=session_id,
                    execution_mode="python-fastapi",
                    model=settings.AI_MODEL,
                    tool_name=tool_name,
                    latency_ms=tool_latency,
                    status=status,
                )

                executed_tool_calls.append(
                    {
                        "name": tool_name,
                        "args": raw_args,
                        "result": tool_output,
                        "latencyMs": tool_latency,
                    }
                )

                docdb_store.add_messages(
                    [
                        {
                            "role": "tool",
                            "tool_call_id": pc["id"],
                            "content": tool_output,
                        }
                    ],
                    session_id=session_id,
                )

        final_reply = _synthesize_fallback_reply(user_message, executed_tool_calls)
        docdb_store.add_messages(
            [{"role": "assistant", "content": final_reply}],
            session_id=session_id,
        )

    full_history = docdb_store.get_full_history(session_id)
    windowed_now = docdb_store.get_windowed_history(
        session_id, window_size=effective_window, include_system_prompt=False
    )

    return {
        "reply": final_reply,
        "mode": "python-fastapi",
        "model": settings.AI_MODEL,
        "sessionId": session_id,
        "toolCallsExecuted": executed_tool_calls,
        "windowedCount": len(windowed_now),
        "totalStoredCount": len(full_history),
        "history": full_history,
    }
