"""Synthetic public-protocol fixtures; no economic or account data."""

from copy import deepcopy
import json

import jsonschema

PAYLOAD = {
    "data": [{"fixture": "native-host-test", "value": None, "announcement_datetime": "1970-01-01T00:00:00Z"}],
    "metadata": {"units": "synthetic", "release_time_assumed": False},
}


class Response:
    def __init__(self, value=None, status=200, *, stream=False):
        self.status_code = status
        self.headers = {"Content-Type": "text/event-stream" if stream else "application/json"}
        if stream:
            self.content = b"id: fixture-event\nevent: release\ndata: " + json.dumps(value).encode() + b"\n\n"
        else:
            self.content = json.dumps(value).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.content

    def close(self):
        self.closed = True


class Transport:
    def __init__(self, payload=None, status=200):
        self.payload = deepcopy(PAYLOAD if payload is None else payload)
        self.status = status
        self.calls = []
        self.responses = []

    def request(self, session, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert url.startswith(("https://api.fxmacrodata.com/", "https://mcp.fxmacrodata.com/"))
        assert "?" not in url and kwargs["allow_redirects"] is False
        body = kwargs.get("json")
        value = deepcopy(self.payload)
        if body and body["method"] == "initialize":
            value = {"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-03-26"}}
        elif body and body["method"] == "notifications/initialized":
            response = Response(status=202)
            self.responses.append(response)
            return response
        elif body and body["method"] == "tools/call":
            value = {"jsonrpc": "2.0", "id": body["id"], "result": {"structuredContent": value, "isError": False}}
        response = Response(value, self.status, stream="/stream" in url)
        self.responses.append(response)
        return response


def sample(schema, name=""):
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    if "default" in schema and schema["default"] is not None:
        return deepcopy(schema["default"])
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            return sample(next((s for s in schema[key] if s.get("type") != "null"), schema[key][0]), name)
    kind = schema.get("type", "object" if "properties" in schema else "string")
    if kind == "object":
        return {key: sample(schema.get("properties", {}).get(key, {}), key) for key in schema.get("required", [])}
    if kind == "array":
        return [sample(schema.get("items", {}), name)] * max(1, schema.get("minItems", 0))
    if kind in {"number", "integer"}:
        return max(1, schema.get("minimum", 0))
    if kind == "boolean":
        return False
    if schema.get("examples"):
        return schema["examples"][0]
    if "example" in schema:
        return schema["example"]
    return {
        "currency": "usd",
        "base": "eur",
        "quote": "usd",
        "indicator": "policy_rate",
        "factor": "monetary_stance",
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "as_of": "2026-01-02T00:00:00Z",
    }.get(name, "fixture")


def arguments(operation):
    value = sample(operation.input_schema)
    if operation.name == "stream_events":
        value.update({"max_events": 1, "max_seconds": 1})
    jsonschema.Draft202012Validator(operation.input_schema).validate(value)
    return value
