# FXMacroData for DeerFlow

Use FXMacroData's always-free public USD catalogue, indicator history and release calendar in DeerFlow without an API key or account. This native extension supplies research tools to lead agents and subagents, with source citations and structured report artifacts.

It registers all 23 public REST operations and 49 hosted MCP tools, plus a composed USD macro brief. The [operation matrix](CAPABILITIES.md) lists every callable tool and its schema. Optional authenticated coverage uses your own process credentials.

## Install through DeerFlow

From your DeerFlow checkout, give the extension manager the absolute path to the extracted source package:

```bash
make extension-install SOURCE="/absolute/path/to/deerflow-fxmacrodata"
make extension-list
```

The manager reviews the package metadata, asks you to trust the extension, copies its deployable source snapshot, resolves dependencies into DeerFlow's lockfile, and enables `deerflow_fxmacrodata:install`. Installation downloads the shared public client from a SHA-256-pinned GitHub release.

Restart DeerFlow using your usual startup method. Ask: “Use the FXMacroData USD macro brief, retain source citations, and explain any unavailable sections.” Every tool is available to both lead agents and subagents through the extension middleware's native tools collection.

The equivalent startup entry, normally managed by DeerFlow, is:

```yaml
plugins:
  - name: fxmacrodata
    package: deerflow-fxmacrodata
    use: deerflow_fxmacrodata:install
    enabled: true
    required: false
    config:
      timeout_seconds: 30
```

Use `make extension-disable NAME=fxmacrodata`, `make extension-enable NAME=fxmacrodata` or `make extension-remove NAME=fxmacrodata` to manage it. Restart after changing the installed or enabled set.

## Optional credentials

Set `FXMACRODATA_API_KEY` or `FXMD_API_KEY` in the DeerFlow process environment through your normal secret-management mechanism. The extension configuration accepts only `timeout_seconds` (1–120); keys are never model-visible arguments, extension settings or shared configuration. Each call reads the current process credential and closes its client/session afterward. A server-wide credential applies to that DeerFlow process; use separate deployments for separate account access.

Without either variable, USD catalogue, history and calendar calls work. Protected datasets return an explicit access error. Do not paste a key into a chat, report, tool input or shell history.

## Native results

Tools produce LangChain `ToolMessage` content and structured artifacts. Artifacts preserve the original public response under `data`, plus an additive `records` view, operation identity and citations. The macro brief combines catalogue, recent indicator history and release-calendar sections; unavailable sections remain explicit.

Date ranges, pagination, timestamp fields and source metadata retain their public meanings. Bounded SSE tools capture a finite event window and close the connection. MCP text, structured data and resource links are preserved; interactive MCP Apps are not rendered. Distinguish observed releases, future schedules, market consensus and FXMacroData-generated predictions when writing reports.

Registration makes no network requests. Credential redaction occurs before tool messages, artifacts or errors reach the model. The extension emits no telemetry; static campaign tags appear only on website links.

[FXMacroData](https://fxmacrodata.com/?utm_source=github&utm_medium=referral&utm_campaign=open_source_integrations&utm_content=deerflow_readme) · [Public API reference](https://fxmacrodata.com/documentation/reference?utm_source=github&utm_medium=referral&utm_campaign=open_source_integrations&utm_content=deerflow_docs)
