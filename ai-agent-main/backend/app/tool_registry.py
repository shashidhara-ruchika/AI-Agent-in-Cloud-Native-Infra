import inspect
import json
from typing import Any, Callable, Dict, List, Optional, Type
import httpx
from pydantic import BaseModel
from .rds import CustomToolApiModel, SessionLocal


class RegisteredPythonTool:
    def __init__(
        self,
        name: str,
        description: str,
        parameters_schema: Dict[str, Any],
        handler: Optional[Callable[..., Any]] = None,
        endpoint_url: Optional[str] = None,
        http_method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        source: str = "python-fastapi",
    ) -> None:
        self.name = name
        self.description = description
        self.parameters_schema = parameters_schema
        self.handler = handler
        self.endpoint_url = endpoint_url
        self.http_method = http_method.upper()
        self.headers = headers or {}
        self.source = source

    def to_openai_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
            "source": self.source,
            "endpoint": self.endpoint_url or f"/api/v1/tools/{self.name}/execute",
            "method": self.http_method,
        }


class GenericPythonToolRegistry:
    """
    Automated Switchable Generic Tool Registry for Python FastAPI.
    Allows registering Python callables (via @tool_registry.register_api)
    as well as external/custom REST API endpoints persisted in RDS PostgreSQL.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, RegisteredPythonTool] = {}

    def register_api(
        self,
        name: str,
        description: str,
        args_schema: Type[BaseModel],
        endpoint_path: Optional[str] = None,
    ):
        """
        Decorator to register any Python function in FastAPI as an AI Agent tool
        AND expose its JSON Schema to both the Python and TypeScript tool-calling loops.
        """
        json_schema = args_schema.model_json_schema()
        # Clean up Pydantic title metadata for OpenAI function calling compatibility
        json_schema.pop("title", None)
        if "type" not in json_schema:
            json_schema["type"] = "object"

        def decorator(func: Callable[..., Any]):
            tool = RegisteredPythonTool(
                name=name,
                description=description,
                parameters_schema=json_schema,
                handler=func,
                endpoint_url=endpoint_path or f"/api/v1/tools/{name}/execute",
                http_method="POST",
                source="python-fastapi",
            )
            self._tools[name] = tool
            return func

        return decorator

    def register_external_rest_tool(
        self,
        name: str,
        description: str,
        endpoint_url: str,
        http_method: str = "POST",
        parameters_schema: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        persist_to_rds: bool = True,
    ) -> RegisteredPythonTool:
        schema = parameters_schema or {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Input parameter"}
            },
        }
        tool = RegisteredPythonTool(
            name=name,
            description=description,
            parameters_schema=schema,
            handler=None,
            endpoint_url=endpoint_url,
            http_method=http_method,
            headers=headers,
            source="custom-rest-api",
        )
        self._tools[name] = tool

        if persist_to_rds:
            try:
                with SessionLocal() as db:
                    existing = (
                        db.query(CustomToolApiModel)
                        .filter(CustomToolApiModel.name == name)
                        .first()
                    )
                    if existing:
                        existing.description = description
                        existing.endpoint_url = endpoint_url
                        existing.http_method = http_method.upper()
                        existing.parameters_schema = json.dumps(schema)
                        existing.headers_json = json.dumps(headers or {})
                        existing.enabled = True
                    else:
                        db.add(
                            CustomToolApiModel(
                                name=name,
                                description=description,
                                endpoint_url=endpoint_url,
                                http_method=http_method.upper(),
                                parameters_schema=json.dumps(schema),
                                headers_json=json.dumps(headers or {}),
                                enabled=True,
                            )
                        )
                    db.commit()
            except Exception:
                pass

        return tool

    def load_persisted_rds_tools(self) -> None:
        try:
            with SessionLocal() as db:
                rows = (
                    db.query(CustomToolApiModel)
                    .filter(CustomToolApiModel.enabled.is_(True))
                    .all()
                )
                for row in rows:
                    if row.name not in self._tools:
                        self._tools[row.name] = RegisteredPythonTool(
                            name=row.name,
                            description=row.description,
                            parameters_schema=json.loads(row.parameters_schema or "{}"),
                            endpoint_url=row.endpoint_url,
                            http_method=row.http_method,
                            headers=json.loads(row.headers_json or "{}"),
                            source="rds-custom-rest-api",
                        )
        except Exception:
            pass

    def unregister_tool(self, name: str) -> bool:
        removed = self._tools.pop(name, None) is not None
        try:
            with SessionLocal() as db:
                db.query(CustomToolApiModel).filter(
                    CustomToolApiModel.name == name
                ).delete()
                db.commit()
        except Exception:
            pass
        return removed

    def list_tools(self) -> List[Dict[str, Any]]:
        self.load_persisted_rds_tools()
        return [t.to_metadata() for t in self._tools.values()]

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        self.load_persisted_rds_tools()
        return [t.to_openai_tool() for t in self._tools.values()]

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        self.load_persisted_rds_tools()
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool '{name}' in Python FastAPI tool registry.")

        # 1. Execute local Python handler if registered via @tool_registry.register_api
        if tool.handler is not None:
            if inspect.iscoroutinefunction(tool.handler):
                res = await tool.handler(**arguments)
            else:
                res = tool.handler(**arguments)
            return res if isinstance(res, str) else json.dumps(res)

        # 2. Execute external/custom HTTP REST API tool
        if tool.endpoint_url:
            async with httpx.AsyncClient(timeout=10.0) as client:
                if tool.http_method == "GET":
                    resp = await client.get(
                        tool.endpoint_url, params=arguments, headers=tool.headers
                    )
                else:
                    resp = await client.request(
                        tool.http_method,
                        tool.endpoint_url,
                        json=arguments,
                        headers=tool.headers,
                    )
                try:
                    return json.dumps(resp.json())
                except Exception:
                    return resp.text

        raise ValueError(f"Tool '{name}' has no executable handler or endpoint_url.")


tool_registry = GenericPythonToolRegistry()
