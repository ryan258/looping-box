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
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_env_loaded_for: set[str] = set()


def load_env(root: Path | str = ".") -> None:
    """Load `<root>/.env` into os.environ once per root. Existing vars win."""
    key = str(Path(root).resolve())
    if key in _env_loaded_for:
        return
    _env_loaded_for.add(key)
    env_path = Path(root) / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def model_for(role: str) -> str | None:
    return os.environ.get(f"MODEL_{role.upper()}") or None


def is_enabled(role: str, *, root: Path | str = ".") -> bool:
    load_env(root)
    return bool(os.environ.get("OPENROUTER_API_KEY") and model_for(role))


# Injectable so tests never hit the network: (url, headers, body) -> raw text.
def _http_post(url: str, headers: dict[str, str], body: bytes) -> str:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (trusted base url)
        return response.read().decode("utf-8")


_transport: Callable[[str, dict[str, str], bytes], str] = _http_post


def complete(
    role: str,
    prompt: str,
    *,
    system: str | None = None,
    root: Path | str = ".",
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Call the role's model. Raises if the role/key is not configured."""
    load_env(root)
    model = model_for(role)
    if not model:
        raise RuntimeError(f"no model configured for role {role!r} (set MODEL_{role.upper()})")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    base_url = os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    body = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature}
    ).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    raw = _transport(f"{base_url}/chat/completions", headers, body)
    data = json.loads(raw)
    text = data["choices"][0]["message"]["content"]
    # temperature=0 is requested, but providers don't guarantee determinism, so
    # callers key idempotency on (input hash + model id) and store this hash.
    return {
        "text": text,
        "model": data.get("model", model),
        "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def generate_if_enabled(
    role: str,
    prompt: str,
    *,
    system: str | None = None,
    root: Path | str = ".",
) -> dict[str, Any] | None:
    """Return a completion for the role, or None when the role isn't configured."""
    if not is_enabled(role, root=root):
        return None
    return complete(role, prompt, system=system, root=root)
