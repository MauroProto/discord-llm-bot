"""MCP server configuration loader.

The bot supports Anthropic's native MCP connector for the Messages API
(beta header `mcp-client-2025-11-20`), which lets Claude call remote MCP
servers (HTTP/SSE) without us having to run an MCP client. Other providers
don't have a comparable native feature in 2026 — for them, MCP servers
configured here will be silently ignored with a warning.

Configuration is via the `MCP_SERVERS_JSON` env var. Set it to a JSON
array like:

```json
[
  {
    "name": "github",
    "url": "https://mcp.github.com/sse",
    "authorization_token": "ghp_..."
  },
  {
    "name": "notion",
    "url": "https://mcp.notion.com/sse",
    "authorization_token": "secret_..."
  }
]
```

Each entry produces:
- One `mcp_servers` entry of `{type: url, url, name, authorization_token?}`.
- One `tools` entry of `{type: mcp_toolset, mcp_server_name: name}`.

Optional per-server tool filters can be added via `MCP_TOOL_FILTERS_JSON`,
a JSON map like:

```json
{
  "github": {"deny": ["delete_repo", "delete_issue"]},
  "notion": {"allow": ["search", "read_page"]}
}
```

Allowlist mode disables everything by default and only enables listed tools.
Denylist mode enables everything by default and disables listed tools.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from config import settings


@dataclass(frozen=True)
class McpServer:
    name: str
    url: str
    authorization_token: str | None = None
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()


def _parse_filters() -> dict[str, dict[str, list[str]]]:
    raw = settings.MCP_TOOL_FILTERS_JSON
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception as e:
        print(f"[mcp] MCP_TOOL_FILTERS_JSON invalid JSON: {e}; ignoring")
        return {}
    if not isinstance(parsed, dict):
        print("[mcp] MCP_TOOL_FILTERS_JSON should be a JSON object; ignoring")
        return {}
    return parsed


def load_servers() -> list[McpServer]:
    """Parse MCP_SERVERS_JSON into a list of McpServer dataclasses."""
    raw = settings.MCP_SERVERS_JSON
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except Exception as e:
        print(f"[mcp] MCP_SERVERS_JSON invalid JSON: {e}; ignoring")
        return []

    if not isinstance(parsed, list):
        print("[mcp] MCP_SERVERS_JSON should be a JSON array; ignoring")
        return []

    filters = _parse_filters()
    out: list[McpServer] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        url = entry.get("url")
        if not name or not url:
            print(f"[mcp] skipping server entry missing name/url: {entry!r}")
            continue
        f = filters.get(name, {})
        out.append(McpServer(
            name=name,
            url=url,
            authorization_token=entry.get("authorization_token"),
            allow=tuple(f.get("allow", []) or []),
            deny=tuple(f.get("deny", []) or []),
        ))
    return out


def to_anthropic_payload(servers: list[McpServer]) -> tuple[list[dict], list[dict]]:
    """Build the (`mcp_servers`, `tools`) lists for Anthropic's Messages API."""
    if not servers:
        return [], []

    server_defs: list[dict[str, Any]] = []
    toolsets: list[dict[str, Any]] = []

    for s in servers:
        srv: dict[str, Any] = {"type": "url", "url": s.url, "name": s.name}
        if s.authorization_token:
            srv["authorization_token"] = s.authorization_token
        server_defs.append(srv)

        ts: dict[str, Any] = {"type": "mcp_toolset", "mcp_server_name": s.name}
        if s.allow:
            ts["default_config"] = {"enabled": False}
            ts["configs"] = {tool: {"enabled": True} for tool in s.allow}
        elif s.deny:
            ts["configs"] = {tool: {"enabled": False} for tool in s.deny}
        toolsets.append(ts)

    return server_defs, toolsets


__all__ = ["McpServer", "load_servers", "to_anthropic_payload"]
