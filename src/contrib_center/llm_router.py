"""LLM multi-provider router with fallback support.

This module provides a unified interface to multiple LLM providers
with automatic fallback on failure.

Security:
- API keys are only read from environment variables
- Keys are REDACTED in all logs
- Only the provider name, model name, and base_url host are logged
- No prompts or responses are logged in full
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("CONTRIB_CENTER_CONFIG_DIR", "config"))


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    name: str
    api_key_env: str
    base_url_env: str
    model_env: str
    timeout_seconds: int = 120
    max_retries: int = 2

    def get_api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

    def get_base_url(self) -> str | None:
        return os.environ.get(self.base_url_env)

    def get_model(self) -> str | None:
        return os.environ.get(self.model_env)

    def is_configured(self) -> bool:
        return bool(self.get_api_key() and self.get_base_url() and self.get_model())


@dataclass
class LLMResult:
    ok: bool
    provider: str
    model: str
    text: str = ""
    error: str | None = None
    fallback_used: bool = False
    provider_name: str = ""  # human-readable name


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _redact(text: str) -> str:
    """Redact any API key-like strings from text."""
    # Redact common API key patterns
    text = re.sub(r"ghp_[a-zA-Z0-9_]{36,}", "[REDACTED_GH]", text)
    text = re.sub(r"sk-[a-zA-Z0-9]{48,}", "[REDACTED_OPENAI]", text)
    text = re.sub(r"Bearer [a-zA-Z0-9_\-\.]{20,}", "[REDACTED_TOKEN]", text)
    return text


def load_llm_routes(config_dir: Path | None = None) -> dict[str, Any]:
    config_dir = config_dir or CONFIG_DIR
    path = config_dir / "llm_routes.yml"
    if not path.exists():
        return _default_routes()
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _default_routes() -> dict[str, Any]:
    return {
        "default_provider_order": ["primary", "backup_1", "backup_2"],
        "providers": {
            "primary": {
                "name": "deepseek_or_ds",
                "api_key_env": "LLM_PRIMARY_API_KEY",
                "base_url_env": "LLM_PRIMARY_BASE_URL",
                "model_env": "LLM_PRIMARY_MODEL",
                "timeout_seconds": 120,
                "max_retries": 2,
            },
            "backup_1": {
                "name": "sensenova",
                "api_key_env": "LLM_BACKUP_1_API_KEY",
                "base_url_env": "LLM_BACKUP_1_BASE_URL",
                "model_env": "LLM_BACKUP_1_MODEL",
                "timeout_seconds": 120,
                "max_retries": 2,
            },
            "backup_2": {
                "name": "agnes",
                "api_key_env": "LLM_BACKUP_2_API_KEY",
                "base_url_env": "LLM_BACKUP_2_BASE_URL",
                "model_env": "LLM_BACKUP_2_MODEL",
                "timeout_seconds": 120,
                "max_retries": 2,
            },
        },
        "routing": {
            "fallback_on": [
                "timeout",
                "rate_limit",
                "server_error",
                "connection_error",
                "invalid_response",
            ],
            "stop_on": ["invalid_api_key", "forbidden"],
            "redact_secrets_in_logs": True,
        },
    }


def build_provider_configs(routes: dict[str, Any]) -> list[ProviderConfig]:
    configs = []
    providers_raw = routes.get("providers", {})
    order = routes.get("default_provider_order", [])
    for key in order:
        p = providers_raw.get(key)
        if p:
            configs.append(
                ProviderConfig(
                    name=p.get("name", key),
                    api_key_env=p.get("api_key_env", ""),
                    base_url_env=p.get("base_url_env", ""),
                    model_env=p.get("model_env", ""),
                    timeout_seconds=int(p.get("timeout_seconds", 120)),
                    max_retries=int(p.get("max_retries", 2)),
                )
            )
    return configs


# ---------------------------------------------------------------------------
# OpenAI-compatible client
# ---------------------------------------------------------------------------

def _make_openai_client(provider: ProviderConfig):
    """Create an OpenAI-compatible client using the provider config.

    Returns a tuple: (client, model_name) or (None, None) on failure.
    """
    try:
        import openai
    except ImportError:
        logger.error("openai_package_not_installed")
        return None, None

    api_key = provider.get_api_key()
    base_url = provider.get_base_url()
    model = provider.get_model()

    if not api_key or not base_url or not model:
        return None, None

    client = openai.OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=provider.timeout_seconds,
    )
    return client, model


def _classify_error(exc: Exception) -> str:
    """Classify an exception into a fallback category."""
    err_str = str(exc).lower()

    if "401" in err_str or "unauthorized" in err_str or "invalid_api_key" in err_str:
        return "invalid_api_key"
    if "403" in err_str or "forbidden" in err_str:
        return "forbidden"
    if "429" in err_str or "rate limit" in err_str:
        return "rate_limit"
    if "timeout" in err_str:
        return "timeout"
    if "connection" in err_str or "connect" in err_str:
        return "connection_error"
    if len(err_str) >= 1 and err_str[0] in ("5",):
        return "server_error"

    return "unknown_error"


# ---------------------------------------------------------------------------
# Core routing function
# ---------------------------------------------------------------------------

def complete(
    prompt: str,
    system: str | None = None,
    task_type: str = "debug",
    timeout: int = 120,
) -> LLMResult:
    """Send a chat completion request with automatic provider fallback.

    Args:
        prompt: The user prompt.
        system: Optional system message.
        task_type: Label for logging (e.g., "debug", "patch", "report").
        timeout: Per-request timeout in seconds.

    Returns:
        LLMResult with the response text or error information.
    """
    routes = load_llm_routes()
    configs = build_provider_configs(routes)

    if not configs:
        return LLMResult(
            ok=False,
            provider="none",
            model="",
            error="No LLM providers configured",
        )

    fallback_on = set(
        routes.get("routing", {}).get(
            "fallback_on",
            ["timeout", "rate_limit", "server_error"],
        )
    )
    stop_on = set(
        routes.get("routing", {}).get(
            "stop_on",
            ["invalid_api_key", "forbidden"],
        )
    )

    last_error = None
    last_error_type = None

    for idx, provider in enumerate(configs):
        if not provider.is_configured():
            logger.warning(
                f"llm_provider_not_configured provider={provider.name} "
                f"provider_idx={idx}"
            )
            continue

        api_key = provider.get_api_key()
        base_url = provider.get_base_url()
        model = provider.get_model()

        # Redact sensitive info in logs
        log_ctx = {
            "provider": provider.name,
            "model": model,
            "base_url_host": _extract_host(base_url),
            "task_type": task_type,
        }

        try:
            client, actual_model = _make_openai_client(provider)
            if client is None:
                continue

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            logger.info(
                f"llm_request_start provider={provider.name} "
                f"model={actual_model} "
                f"base_url_host={_extract_host(base_url)} task_type={task_type}"
            )

            response = client.chat.completions.create(
                model=actual_model,
                messages=messages,
                timeout=timeout,
            )

            text = response.choices[0].message.content or ""

            logger.info(
                f"llm_request_success provider={provider.name} "
                f"model={actual_model} fallback_used={idx > 0}"
            )

            return LLMResult(
                ok=True,
                provider=provider.name,
                model=actual_model,
                text=text,
                fallback_used=(idx > 0),
                provider_name=provider.name,
            )

        except Exception as exc:
            error_type = _classify_error(exc)

            # Check if we should stop trying this provider
            if error_type in stop_on:
                logger.error(
                    f"llm_provider_auth_failed provider={provider.name} "
                    f"error_type={error_type}"
                )
                # Don't try remaining providers if auth failed
                break

            # Check if we should fallback
            if error_type in fallback_on or error_type == "unknown_error":
                logger.warning(
                    f"llm_request_fallback provider={provider.name} "
                    f"error_type={error_type} "
                    f"will_try_next={idx < len(configs) - 1}"
                )
                last_error = str(exc)
                last_error_type = error_type
                continue

            # Unknown error - also fallback
            logger.warning(
                f"llm_request_error provider={provider.name} "
                f"error_type={error_type}"
            )
            last_error = str(exc)
            last_error_type = error_type
            continue

    # All providers failed
    return LLMResult(
        ok=False,
        provider="all_failed",
        model="",
        text="",
        error=f"All providers failed. Last error: {last_error_type or 'unknown'}",
    )


def _extract_host(url: str | None) -> str:
    """Extract host from URL for logging (no credentials)."""
    if not url:
        return "unknown"
    # Remove protocol and path
    host = re.sub(r"^https?://", "", url or "")
    host = host.split("/")[0]
    return host


# ---------------------------------------------------------------------------
# Health check command
# ---------------------------------------------------------------------------

def llm_check() -> dict[str, Any]:
    """Check LLM provider configuration and connectivity.

    Returns a dict with provider status (NO API keys exposed).
    """
    routes = load_llm_routes()
    configs = build_provider_configs(routes)

    results = {}

    for provider in configs:
        cfg_result: dict[str, Any] = {
            "configured": provider.is_configured(),
            "ok": False,
            "model": provider.get_model() or "(not set)",
            "base_url_host": _extract_host(provider.get_base_url()),
            "error": None,
        }

        if not cfg_result["configured"]:
            cfg_result["error"] = "Missing env vars"
            results[provider.name] = cfg_result
            continue

        # Send a minimal test prompt
        result = complete(
            prompt="Return exactly: OK",
            system="You are a helpful assistant. Respond with exactly OK.",
            task_type="health_check",
            timeout=30,
        )

        cfg_result["ok"] = result.ok
        if not result.ok:
            # Don't expose detailed error (may contain key info)
            cfg_result["error"] = result.error or "Unknown error"
        results[provider.name] = cfg_result

    return results
