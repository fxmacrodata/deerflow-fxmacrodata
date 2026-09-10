"""Native LangChain tools with sourced content and lossless structured artifacts."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any

import jsonschema
from fxmacrodata_public import FXMacroDataClient as _PublicClient
from fxmacrodata_public import FXMacroDataError, Result, list_operations
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import StructuredTool, ToolException

from .response_safety import sanitize_response


class FXMacroDataClient(_PublicClient):
    """Apply JSON-aware redaction before the pinned client's record projection."""

    def _safe(self, value: Any) -> Any:
        return sanitize_response(value, self._api_key)


SITE_URL = (
    "https://fxmacrodata.com/?utm_source=deerflow&utm_medium=integration&utm_campaign=open_source_integrations&utm_content=deerflow_extension"
)
GUIDANCE = (
    "Discover indicator slugs with fxmd_data_catalogue before querying history. "
    "Public USD catalogue, history and release calendar need no key. Cite source URLs. "
    "Preserve announcement timestamps and distinguish observed releases, scheduled releases, "
    "market consensus and FXMacroData-generated predictions. Empty records mean unavailable. "
    "MCP resources are preserved as artifacts; this extension does not render MCP Apps."
)
BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "indicator": {"type": "string", "default": "policy_rate", "description": "USD indicator slug from catalogue discovery."}
    },
    "additionalProperties": False,
}


def query(operation: str, arguments: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    """Keep credentials solely in the per-call public client lifecycle."""
    key = os.getenv("FXMACRODATA_API_KEY") or os.getenv("FXMD_API_KEY") or ""
    try:
        with FXMacroDataClient(api_key=key, timeout=timeout) as client:
            result = client.execute(operation, arguments)
            safe_result = Result(operation, sanitize_response(result.payload, key), sanitize_response(result.source_url, key))
            output = safe_result.as_dict()
            output["provider_url"] = SITE_URL
            output["citations"] = [{"title": "FXMacroData", "url": safe_result.source_url}]
            return output
    except FXMacroDataError as exc:
        try:
            message = sanitize_response(str(exc), key)
        except ValueError:
            message = "FXMacroData could not safely decode the response. Retry the request."
        return {"operation": operation, "error": message, "provider_url": SITE_URL}
    except Exception:
        return {
            "operation": operation,
            "error": "FXMacroData request failed. Check parameters and access, then retry.",
            "provider_url": SITE_URL,
        }


def _brief_requests(arguments: dict[str, Any]) -> tuple[tuple[str, dict[str, Any]], ...]:
    try:
        jsonschema.Draft202012Validator(BRIEF_SCHEMA).validate(arguments)
    except (jsonschema.ValidationError, TypeError):
        raise FXMacroDataError("Use an optional indicator slug; credentials are not tool arguments.") from None
    today = datetime.now(timezone.utc).date()
    return (
        ("data_catalogue", {"currency": "usd"}),
        ("indicator_history", {"currency": "usd", "indicator": arguments.get("indicator", "policy_rate"), "limit": 10}),
        (
            "release_calendar",
            {"currency": "usd", "start_date": today.isoformat(), "end_date": (today + timedelta(days=7)).isoformat(), "timezone": "UTC"},
        ),
    )


def _brief_result(sections: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": "USD macro research brief",
        "sections": sections,
        "complete": all("error" not in section for section in sections),
        "instructions": GUIDANCE,
        "provider_url": SITE_URL,
    }


def usd_brief(arguments: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    try:
        requests = _brief_requests(arguments)
    except FXMacroDataError as exc:
        return {"error": str(exc), "provider_url": SITE_URL}
    return _brief_result([query(name, args, timeout) for name, args in requests])


def _message(output: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    title = output.get("title") or "FXMacroData: " + output.get("operation", "request")
    content = f"{title}\nSource: {SITE_URL}\n\n" + json.dumps(output, ensure_ascii=False)
    if "error" in output:
        raise ToolException(content)
    return content, output


def build_tools(timeout: float = 30):
    """Create native tools; no network, session or credential is read here."""
    tools = []

    def entrypoints(operation_name):
        def invoke(**arguments):
            return _message(query(operation_name, arguments, timeout))

        async def ainvoke(**arguments):
            return await asyncio.to_thread(invoke, **arguments)

        return invoke, ainvoke

    for operation in list_operations():
        invoke, ainvoke = entrypoints(operation.name)

        tools.append(
            StructuredTool.from_function(
                func=invoke,
                coroutine=ainvoke,
                name="fxmd_" + operation.name,
                description=operation.description,
                args_schema=deepcopy(operation.input_schema),
                infer_schema=False,
                response_format="content_and_artifact",
                handle_tool_error=True,
            )
        )

    def brief(**arguments):
        return _message(usd_brief(arguments, timeout))

    async def abrief(**arguments):
        try:
            requests = _brief_requests(arguments)
        except FXMacroDataError as exc:
            return _message({"error": str(exc), "provider_url": SITE_URL})
        sections = []
        # An in-flight request is timeout-bounded; cancellation prevents subsequent calls.
        for name, args in requests:
            sections.append(await asyncio.to_thread(query, name, args, timeout))
        return _message(_brief_result(sections))

    tools.append(
        StructuredTool.from_function(
            func=brief,
            coroutine=abrief,
            name="fxmd_usd_macro_brief",
            description="Build a sourced USD catalogue, history and release-calendar research brief without a key. " + GUIDANCE,
            args_schema=deepcopy(BRIEF_SCHEMA),
            infer_schema=False,
            response_format="content_and_artifact",
            handle_tool_error=True,
        )
    )
    return tools


class FXMacroDataMiddleware(AgentMiddleware):
    """LangChain contributes middleware tools to both model binding and ToolNode."""

    def __init__(self, timeout: float = 30):
        self.tools = build_tools(timeout)
