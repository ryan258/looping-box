"""Per-role model access via OpenRouter (OpenAI-compatible chat endpoint).

Each role reads its model from `MODEL_<ROLE>` (e.g. MODEL_EXECUTION_ENGINE).
The whole layer is gated: with no `OPENROUTER_API_KEY` or no model for a role,
`generate_if_enabled` returns None and the caller keeps its deterministic path,
so the offline test suite and demos still work.

ponytail: stdlib urllib, no streaming/retries/backoff. Swap to the `openai`
SDK (base_url=OPENROUTER_BASE_URL) if you need those.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import SplitResult, urlsplit

from .paths import file_path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_env_loaded_for: dict[str, dict[str, str]] = {}
# Only the model layer's own settings are read from `.env`: it must not be able to set
# unrelated variables (e.g. LOOPING_BOX_REVIEW_KEY, LOOPING_BOX_ALLOW_NONINTERACTIVE).
_ENV_PREFIXES = ("OPENROUTER_", "MODEL_")


class ModelError(RuntimeError):
    """A configured model call failed (network, provider, or response shape).

    Callers catch this to turn an outage into a structured failure instead of
    an unhandled crash.
    """


@dataclass(frozen=True)
class RoleSettings:
    model_id: str | None
    api_key: str | None = field(repr=False)
    base_url: str = field(repr=False)


def _read_workspace_settings(path: Path) -> dict[str, str]:
    settings: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return settings
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if name.startswith(_ENV_PREFIXES):
            settings.setdefault(name, value.strip().strip('"').strip("'"))
    return settings


def _with_process_settings(settings: dict[str, str]) -> dict[str, str]:
    process = {name: value for name, value in os.environ.items() if name.startswith(_ENV_PREFIXES)}
    return {**settings, **process}


def load_env(root: Path | str = ".") -> dict[str, str]:
    """Read model settings once per resolved workspace and apply process overrides."""
    return _with_process_settings(_workspace_settings(Path(root).resolve()))


def _workspace_settings(root_path: Path) -> dict[str, str]:
    path = file_path(root_path / ".env")
    key = str(root_path)
    if key not in _env_loaded_for:
        _env_loaded_for[key] = _read_workspace_settings(path)
    return _env_loaded_for[key]


def inspect_settings(root: Path | str) -> dict[str, str]:
    """Fresh model settings for diagnostics, without changing the process environment/cache."""
    path = file_path(Path(root).resolve() / ".env")
    return _with_process_settings(_read_workspace_settings(path))


def model_for(role: str, *, root: Path | str = ".") -> str | None:
    return role_settings(role, root=root).model_id


def role_settings(role: str, *, root: Path | str = ".") -> RoleSettings:
    """Capture one request's effective settings without exposing its credential."""
    workspace = _workspace_settings(Path(root).resolve())
    settings = _with_process_settings(workspace)
    model_id = settings.get(f"MODEL_{role.upper()}") or None
    api_key = settings.get("OPENROUTER_API_KEY")
    workspace_key = workspace.get("OPENROUTER_API_KEY")
    if model_id is not None:
        if any(_redact(model_id, key) != model_id for key in (api_key, workspace_key)):
            raise ModelError("configured model ID contains an API key")
        try:
            model_id.encode("utf-8")
        except UnicodeError:
            raise ModelError("configured model ID is not valid UTF-8") from None
    return RoleSettings(model_id, api_key, settings.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL))


def is_enabled(role: str, *, root: Path | str = ".") -> bool:
    settings = load_env(root)
    return bool(settings.get("OPENROUTER_API_KEY") and settings.get(f"MODEL_{role.upper()}"))


def parse_base_url(value: str) -> SplitResult:
    """Validate the endpoint format used by both diagnostics and transport."""
    message = "configured endpoint must be an HTTP(S) URL without credentials, a query, or a fragment"
    try:
        if any(ch.isspace() or ord(ch) < 32 for ch in value) or "?" in value or "#" in value:
            raise ValueError
        endpoint = urlsplit(value)
        _ = endpoint.port  # Access validates the port.
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username is not None or endpoint.password is not None):
            raise ValueError
    except ValueError:
        raise ValueError(message) from None
    return endpoint


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The configured endpoint is the only destination for this request.
        return None


# Injectable so tests never hit the network: (url, headers, body, timeout) -> raw text.
def _http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> str:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


_transport: Callable[[str, dict[str, str], bytes, float], str] = _http_post


def _redact(value: object, secret: str | None) -> str:
    text = str(value)
    if secret:
        forms = {secret, json.dumps(secret)[1:-1], repr(secret)[1:-1]}
        for form in sorted(forms, key=len, reverse=True):
            text = text.replace(form, "[REDACTED]")
    return text


def redact_error(value: object, *, root: Path | str = ".") -> str:
    """Sanitize this workspace's configured credential before persisting errors."""
    return _redact(value, load_env(root).get("OPENROUTER_API_KEY"))


def complete(
    role: str,
    prompt: str,
    *,
    system: str | None = None,
    root: Path | str = ".",
    temperature: float = 0.0,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Call the role's model. Raises if the role/key is not configured."""
    return _complete(role, prompt, role_settings(role, root=root), system, temperature, timeout)


def _complete(
    role: str, prompt: str, settings: RoleSettings, system: str | None, temperature: float, timeout: float
) -> dict[str, Any]:
    model = settings.model_id
    if not model:
        raise RuntimeError(f"no model configured for role {role!r} (set MODEL_{role.upper()})")
    api_key = settings.api_key
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    try:
        base_url = parse_base_url(settings.base_url).geturl().rstrip("/")
    except ValueError as exc:
        raise ModelError(str(exc)) from None
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    body = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature}
    ).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        raw = _transport(f"{base_url}/chat/completions", headers, body, timeout)
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("error"):
            raise ValueError(f"provider error: {_redact(data['error'], api_key)[:200]}")
        text = data["choices"][0]["message"]["content"]
        if not isinstance(text, str):  # refusals / tool calls come back as null content
            raise ValueError("response had no text content")
        returned_model = data.get("model", model)
        if not isinstance(returned_model, str) or not returned_model:
            raise ValueError("response had no valid model ID")
        if _redact(text, api_key) != text or _redact(returned_model, api_key) != returned_model:
            raise ValueError("provider response contained the configured API key")
        returned_model.encode("utf-8")
        response_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    except Exception as exc:  # network, JSON, or unexpected provider shape
        raise ModelError(f"{role} model call failed: {_redact(exc, api_key)}") from None
    # temperature=0 is requested, but providers don't guarantee determinism, so
    # callers key idempotency on (input hash + model id) and store this hash.
    return {
        "text": text,
        "model": returned_model,
        "response_sha256": response_sha256,
    }


def generate_if_enabled(
    role: str,
    prompt: str,
    *,
    system: str | None = None,
    root: Path | str = ".",
    timeout: float = 60.0,
    _settings: RoleSettings | None = None,
) -> dict[str, Any] | None:
    """Return a completion for the role, or None when the role isn't configured."""
    settings = _settings if _settings is not None else role_settings(role, root=root)
    if not settings.api_key or not settings.model_id:
        return None
    return _complete(role, prompt, settings, system, 0.0, timeout)
