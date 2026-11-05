"""Codex plugin manifests stay aligned with the published schemas."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(relative: str) -> dict[str, object]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_plugin_manifests() -> None:
    plugin = _load("plugin.json")
    assert plugin["name"] == "personal-video-guide"
    assert str(plugin["$schema"]).endswith("/plugin.schema.json")

    portable = _load("mcp.json")
    assert str(portable["$schema"]).endswith("/mcp.schema.json")
    servers = portable["mcpServers"]
    assert isinstance(servers, dict)
    entry = servers["video-guide"]
    assert isinstance(entry, dict)
    assert entry["type"] == "stdio"
    assert entry["command"] == "video-guide"
    assert entry["args"] == ["mcp"]

    overlay = _load(".codex-plugin/plugin.json")
    assert overlay["skills"] == "./skills/"
    assert overlay["mcpServers"] == "./.mcp.json"
    assert str(overlay["skills"]).startswith("./")
    assert str(overlay["mcpServers"]).startswith("./")

    compat = _load(".mcp.json")
    compat_servers = compat["mcpServers"]
    assert isinstance(compat_servers, dict)
    assert compat_servers["video-guide"] == entry

    marketplace = _load(".agents/plugins/marketplace.json")
    plugins = marketplace["plugins"]
    assert isinstance(plugins, list)
    source = plugins[0]["source"]
    assert isinstance(source, dict)
    assert source == {"source": "local", "path": "./"}
    policy = plugins[0]["policy"]
    assert isinstance(policy, dict)
    assert policy["installation"] == "AVAILABLE"
    assert policy["authentication"] == "ON_INSTALL"
    assert plugins[0]["category"] == "Education & Research"
