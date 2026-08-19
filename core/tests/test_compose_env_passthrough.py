"""Regression: settings env vars must be forwarded through docker-compose.

Dokploy only substitutes vars into compose; the container sees only names
listed under services.web.environment. A settings.py os.environ.get without
a matching compose entry is a silent misconfig (the MCP OAuth fail-closed
case was the third occurrence). This test parses both files so the list
cannot rot.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / "cms_platform" / "settings.py"
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

# Intentionally not forwarded to services.web (safe in-code defaults, or
# consumed only by another compose service). Document why when adding.
COMPOSE_ENV_ALLOWLIST = frozenset(
    {
        # DEBUG-only local wildcard host; production uses TENANT_BASE_DOMAIN.
        "TENANT_DEV_BASE_DOMAIN",
        # route-syncer sets this; web must leave it empty (no Traefik mount).
        "TRAEFIK_DYNAMIC_DIR",
        # Hardcoded prod-ready defaults; override rarely enough to stay here.
        "EMBED_ASSISTANT_PUBLIC_ORIGIN",
        "GHL_CHOOSELOCATION_URL",
        "GHL_CHOOSELOCATION_URL_STANDARD",
    }
)


def _settings_environ_keys(source: str) -> set[str]:
    """Return env var names read via os.environ.get(...) in settings.py."""
    tree = ast.parse(source)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # os.environ.get("NAME", ...)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "os"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
    return keys


def _compose_web_environment_keys(source: str) -> set[str]:
    """Return keys under services.web.environment in docker-compose.yml.

    Parses the YAML-ish block with a small state machine rather than adding
    a PyYAML dependency. Matches KEY: ${...} lines inside the web service's
    environment: mapping only.
    """
    keys: set[str] = set()
    in_web = False
    in_environment = False
    web_indent = None
    env_indent = None

    for raw in source.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()

        if re.match(r"^web:\s*$", line) and indent >= 2:
            in_web = True
            web_indent = indent
            in_environment = False
            continue

        if in_web and indent <= web_indent and not line.startswith("#"):
            # Next top-level service (or volumes/networks sibling under root
            # is indent 0; sibling services under `services:` share web_indent).
            if indent == web_indent and line.endswith(":") and not line.startswith("environment"):
                in_web = False
                in_environment = False
                continue
            if indent < web_indent:
                in_web = False
                in_environment = False
                continue

        if not in_web:
            continue

        if re.match(r"^environment:\s*$", line):
            in_environment = True
            env_indent = indent
            continue

        if in_environment:
            if indent <= env_indent:
                in_environment = False
                continue
            match = re.match(r"^([A-Z][A-Z0-9_]*):\s*", line)
            if match:
                keys.add(match.group(1))

    return keys


class ComposeEnvPassthroughTests(unittest.TestCase):
    def test_settings_environ_keys_are_forwarded_or_allowlisted(self):
        settings_src = SETTINGS_PATH.read_text(encoding="utf-8")
        compose_src = COMPOSE_PATH.read_text(encoding="utf-8")

        settings_keys = _settings_environ_keys(settings_src)
        compose_keys = _compose_web_environment_keys(compose_src)

        self.assertTrue(settings_keys, "expected to find os.environ.get calls in settings.py")
        self.assertTrue(compose_keys, "expected to find services.web.environment keys")

        missing = sorted(settings_keys - compose_keys - COMPOSE_ENV_ALLOWLIST)
        self.assertEqual(
            missing,
            [],
            "settings.py reads env vars that docker-compose.yml does not forward "
            f"under services.web.environment and that are not in COMPOSE_ENV_ALLOWLIST: "
            f"{missing}. Add them to the compose environment block, or document them "
            f"in COMPOSE_ENV_ALLOWLIST with a reason.",
        )

    def test_mcp_oauth_vars_are_forwarded(self):
        """Deploy blocker: these fail closed when empty, so never rely on allow-list."""
        compose_keys = _compose_web_environment_keys(
            COMPOSE_PATH.read_text(encoding="utf-8")
        )
        required = {
            "CLAUDE_OAUTH_CLIENT_ID",
            "CLAUDE_OAUTH_CLIENT_SECRET",
            "CLAUDE_OAUTH_REDIRECT_URIS",
            "MCP_ALLOWED_ORIGINS",
        }
        self.assertTrue(
            required.issubset(compose_keys),
            f"MCP/OAuth env vars missing from services.web.environment: "
            f"{sorted(required - compose_keys)}",
        )
