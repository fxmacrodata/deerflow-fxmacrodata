"""Real extension loading, native ToolMessages, graph wiring and public protocols."""

import asyncio
from importlib.metadata import entry_points
import json
from threading import Event
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import pytest
import requests
from deerflow_extension_api import AgentBuildContext, AgentScope, Placement
from deerflow.extensions.loader import ExtensionSpec, load_extensions
from deerflow.extensions.injection import inject_middlewares
from deerflow.extensions.anchors import innermost
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from fxmacrodata_public import FXMacroDataError, list_operations

from deerflow_fxmacrodata import install
from deerflow_fxmacrodata.tools import FXMacroDataClient, build_tools
from transport_fixtures import PAYLOAD, Transport, arguments


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.delenv("FXMACRODATA_API_KEY", raising=False)
    monkeypatch.delenv("FXMD_API_KEY", raising=False)
    value = Transport()
    monkeypatch.setattr(requests.Session, "request", lambda session, *a, **kw: value.request(session, *a, **kw))
    return value


@pytest.fixture(scope="module")
def tools():
    return {tool.name: tool for tool in build_tools()}


def call(tool, args):
    return tool.invoke({"type": "tool_call", "id": "synthetic-call", "name": tool.name, "args": args})


def test_installed_entrypoint_loads_in_native_extension_loader():
    eps = [ep for ep in entry_points(group="deerflow.extensions") if ep.name == "fxmacrodata"]
    assert len(eps) == 1 and eps[0].load() is install
    loaded, diagnostics = load_extensions([ExtensionSpec(use="deerflow_fxmacrodata:install", required=True)])
    assert loaded.has_middleware_contributors
    assert not [item for item in diagnostics if item.level == "error"]
    for scope in (AgentScope.LEAD, AgentScope.SUBAGENT):
        stack, provenance, diagnostic = inject_middlewares(
            [], {Placement.STANDARD: innermost()}, scope, AgentBuildContext(scope=scope), loaded
        )
        assert len(stack) == 1 and provenance and not diagnostic
        assert {tool.name for tool in stack[0].tools} == {"fxmd_" + op.name for op in list_operations()} | {"fxmd_usd_macro_brief"}


@pytest.mark.parametrize("operation", list_operations(), ids=lambda item: item.name)
def test_every_operation_native_message_and_preserved_response(tools, transport, operation):
    tool = tools["fxmd_" + operation.name]
    assert tool.args_schema == operation.input_schema
    message = call(tool, arguments(operation))
    assert isinstance(message, ToolMessage)
    output = message.artifact
    assert "error" not in output, output
    assert output["operation"] == operation.name and output["citations"]
    assert all(response.closed for response in transport.responses)
    assert all("api_key" not in options["params"] for _, _, options in transport.calls)
    if operation.name == "stream_events":
        assert output["data"]["events"][0]["data"] == PAYLOAD
    elif operation.method == "MCP":
        assert output["data"]["structuredContent"] == PAYLOAD
    else:
        assert output["data"] == PAYLOAD
    assert "fxmacrodata.com" in message.content and output["records"]


@pytest.mark.parametrize("operation", list_operations(), ids=lambda item: item.name)
def test_every_operation_async_native_execution(tools, transport, operation):
    tool = tools["fxmd_" + operation.name]
    message = asyncio.run(tool.ainvoke({"type": "tool_call", "id": "synthetic-async", "name": tool.name, "args": arguments(operation)}))
    assert message.artifact["operation"] == operation.name and "error" not in message.artifact


class Model(FakeMessagesListChatModel):
    bound_names: list[str] = []

    def bind_tools(self, tools, **kwargs):
        self.bound_names = [tool.name for tool in tools]
        return self


@pytest.mark.parametrize("scope", [AgentScope.LEAD, AgentScope.SUBAGENT])
def test_native_graph_binds_and_executes_middleware_tools(scope, transport):
    loaded, _ = load_extensions([ExtensionSpec(use="deerflow_fxmacrodata:install", required=True)])
    middleware, _, errors = inject_middlewares([], {Placement.STANDARD: innermost()}, scope, AgentBuildContext(scope=scope), loaded)
    assert not errors
    model = Model(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "fxmd_usd_macro_brief", "args": {}, "id": "native-brief"}]),
            AIMessage(content="Sourced research complete."),
        ]
    )
    graph = create_agent(model, tools=[], middleware=middleware)
    result = graph.invoke({"messages": [{"role": "user", "content": "Research USD macro releases."}]})
    message = next(msg for msg in result["messages"] if isinstance(msg, ToolMessage))
    assert message.artifact["complete"] and len(message.artifact["sections"]) == 3
    assert len(model.bound_names) == 73 and len(transport.calls) == 3


def test_actual_lead_and_subagent_builders_retain_complete_toolset():
    from deerflow.agents.lead_agent.agent import build_middlewares
    from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.extensions.isolation import IsolatedMiddleware

    app_config = AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))
    loaded, _ = load_extensions([ExtensionSpec(use="deerflow_fxmacrodata:install", required=True)])
    lead = build_middlewares(config={"configurable": {}}, model_name="fixture", app_config=app_config, extensions=loaded)
    subagent = build_subagent_runtime_middlewares(app_config=app_config, extensions=loaded)
    for stack in (lead, subagent):
        contributed = [item for item in stack if isinstance(item, IsolatedMiddleware) and item.source == "deerflow_fxmacrodata:install"]
        assert len(contributed) == 1
        assert len(contributed[0].tools) == 73


@pytest.mark.parametrize("operation", ["ping", "mcp_ping", "stream_events"])
def test_credentials_are_removed_from_native_content_and_artifact(tools, transport, monkeypatch, operation):
    key = "synthetic/credential + alpha"
    monkeypatch.setenv("FXMACRODATA_API_KEY", key)
    transport.payload = {
        "data": [
            {"apiKey": key, "nested": json.dumps({"authorization": "Bearer " + key}), "text": [key, quote(key, safe=""), quote_plus(key)]}
        ],
        key: "dictionary-key",
        "safe": "preserved",
    }
    args = {"max_events": 1, "max_seconds": 1} if operation == "stream_events" else {}
    message = call(tools["fxmd_" + operation], args)
    value = message.content + json.dumps(message.artifact)
    assert "error" not in message.artifact
    assert all(secret not in value for secret in (key, quote(key, safe=""), quote_plus(key)))
    assert "preserved" in value and "redacted" in value


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_safe_errors(tools, transport, status):
    transport.status = status
    transport.payload = {"error": "https://example.invalid/?api_key=never-disclose-this"}
    message = call(tools["fxmd_ping"], {})
    assert message.status == "error" and '"error":' in message.content
    assert "never-disclose-this" not in message.content and "example.invalid" not in message.content


@pytest.mark.parametrize("name,status", [("fxmd_ping", 200), ("fxmd_usd_macro_brief", 200), ("fxmd_ping", 429)])
def test_native_runtime_attribution_identifies_deerflow(tools, transport, name, status):
    transport.status = status
    message = call(tools[name], {})
    output = message.artifact if message.status == "success" else json.loads(message.content.split("\n\n", 1)[1])
    for item in [output, *output.get("sections", [])]:
        url = urlsplit(item["provider_url"])
        assert item["provider_url"] in message.content
        assert url.scheme == "https" and url.hostname == "fxmacrodata.com"
        assert parse_qs(url.query) == {
            "utm_source": ["deerflow"],
            "utm_medium": ["integration"],
            "utm_campaign": ["open_source_integrations"],
            "utm_content": ["deerflow_extension"],
        }


@pytest.mark.parametrize(
    "config",
    [
        {"api_key": "synthetic"},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": 0},
        {"timeout_seconds": True},
        {"timeout_seconds": "invalid"},
    ],
)
def test_bad_settings_fail_without_echoing_values(config):
    with pytest.raises(ValueError) as error:
        install(None, config)
    assert "synthetic" not in str(error.value)


def test_secret_or_operation_override_arguments_fail_before_network(tools, transport):
    for extra in ({"api_key": "synthetic"}, {"_operation": "ping"}, {"base_url": "https://example.invalid"}):
        message = call(tools["fxmd_data_catalogue"], {"currency": "usd", **extra})
        assert message.status == "error" and '"error":' in message.content
    assert not transport.calls


def test_cancelled_async_brief_does_not_start_later_requests(tools, monkeypatch):
    started = Event()
    release = Event()
    transport = Transport()

    def request(session, *args, **kwargs):
        started.set()
        assert release.wait(5), "test did not release the in-flight request"
        return transport.request(session, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", request)

    async def cancel_brief():
        task = asyncio.create_task(tools["fxmd_usd_macro_brief"].ainvoke({}))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()

    # asyncio.run also joins the executor, so any post-cancellation requests are observed.
    asyncio.run(cancel_brief())
    assert len(transport.calls) == 1
    assert all(response.closed for response in transport.responses)


def test_failed_operation_is_a_native_error_message(tools, transport):
    transport.status = 429
    message = call(tools["fxmd_ping"], {})
    assert message.status == "error"
    assert "FXMacroData" in message.content


@pytest.mark.parametrize("sensitive_value", [True, False, None, 123])
def test_encoded_mcp_text_is_safe_in_native_content_and_artifact(tools, transport, monkeypatch, sensitive_value):
    key = "synthetic/review-key + never-valid"
    monkeypatch.setenv("FXMACRODATA_API_KEY", key)
    encoded = "".join(f"\\u{ord(character):04x}" for character in key)
    payload_text = '{"note":"' + encoded + '","apiKey":' + json.dumps(sensitive_value) + ',"value":1.25,"requires_api_key":false}'
    transport.payload = {"content": [{"type": "text", "text": payload_text}]}
    message = call(tools["fxmd_mcp_ping"], {})
    assert message.status == "success"
    assert key not in message.content + json.dumps(message.artifact)
    assert message.artifact["records"][0]["value"] == 1.25
    assert message.artifact["records"][0]["requires_api_key"] is False
    assert message.artifact["records"][0]["apiKey"] == "[redacted]"


@pytest.mark.parametrize("operation", ["ping", "mcp_ping"])
@pytest.mark.parametrize("hex_case", ["04x", "04X"])
def test_plain_text_encoded_credential_is_redacted(tools, transport, monkeypatch, operation, hex_case):
    key = "synthetic-review-key-never-valid"
    monkeypatch.setenv("FXMACRODATA_API_KEY", key)
    encoded = "".join("\\u" + format(ord(character), hex_case) for character in key)
    transport.payload = {"data": [{"note": "Public note containing " + encoded + " and preserved context.", "value": None}]}
    message = call(tools["fxmd_" + operation], {})
    assert message.status == "success"
    rendered = (message.content + json.dumps(message.artifact)).replace("\\\\", "\\")
    assert key not in rendered and encoded not in rendered
    assert "preserved context" in rendered and "[redacted]" in rendered


def test_async_invalid_brief_is_native_error_without_network(tools, transport):
    tool = tools["fxmd_usd_macro_brief"]
    message = asyncio.run(tool.ainvoke({"type": "tool_call", "id": "invalid", "name": tool.name, "args": {"api_key": "synthetic"}}))
    assert message.status == "error"
    assert "synthetic" not in message.content
    assert not transport.calls


@pytest.mark.parametrize("depth", [1, 70])
def test_encoded_errors_are_redacted_or_fail_closed_at_native_boundary(tools, monkeypatch, depth):
    key = "synthetic-error-key-never-valid"
    monkeypatch.setenv("FXMACRODATA_API_KEY", key)
    encoded = "".join(f"\\u{ord(character):04x}" for character in key)
    message = "[" * depth + '{"note":"' + encoded + '"}' + "]" * depth

    def fail(*_):
        raise FXMacroDataError(message)

    monkeypatch.setattr(FXMacroDataClient, "execute", fail)
    result = call(tools["fxmd_ping"], {})
    assert result.status == "error"
    assert key not in result.content and encoded not in result.content.replace("\\\\", "\\")


def test_cancelling_one_async_request_preserves_other_call_and_closes_responses(tools, monkeypatch):
    started, release = Event(), Event()
    responses = []
    sessions = []

    def request(session, method, url, **kwargs):
        currency = url.rsplit("/", 1)[-1]
        sessions.append(session)
        if currency == "usd":
            started.set()
            assert release.wait(5)
        response = Transport(payload={"data": [{"currency": currency, "value": None}]}).request(session, method, url, **kwargs)
        responses.append(response)
        return response

    monkeypatch.setattr(requests.Session, "request", request)

    async def exercise():
        tool = tools["fxmd_data_catalogue"]
        first = asyncio.create_task(tool.ainvoke({"type": "tool_call", "id": "one", "name": tool.name, "args": {"currency": "usd"}}))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            second = await tool.ainvoke({"type": "tool_call", "id": "two", "name": tool.name, "args": {"currency": "eur"}})
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert second.status == "success" and second.artifact["records"] == [{"currency": "eur", "value": None}]
        finally:
            release.set()

    asyncio.run(exercise())
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert len(responses) == 2 and all(response.closed for response in responses)
