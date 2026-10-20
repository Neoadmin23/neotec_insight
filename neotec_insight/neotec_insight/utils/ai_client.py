"""One AI client for every Insight feature (v2.96.0).

Before this, AI analysis, Studio's AI builder and the Copilot each built their
own request in the OpenAI format. Now every feature calls `chat()`, and the
provider chosen in Insight AI Settings decides the wire format:

  OpenAI-compatible   POST {endpoint}/chat/completions, Bearer key.
                      OpenAI, Google Gemini (…/v1beta/openai/), Ollama, Groq,
                      Mistral, DeepSeek, OpenRouter, vLLM, LM Studio, and
                      Anthropic's own OpenAI-compatible layer.
  Anthropic (Claude)  POST {endpoint}/v1/messages, x-api-key and
                      anthropic-version headers, max_tokens required.
  Azure OpenAI        POST {resource}/openai/deployments/{model}/chat/completions
                      ?api-version=…, api-key header (model = deployment name).

Errors are turned into plain advice (bad key, unknown model, quota, outage)
and a rate-limit or server error is retried once.
"""
from __future__ import annotations

import ipaddress
import time
from urllib.parse import urlparse

import frappe
from frappe import _

OPENAI = "OpenAI-compatible"
ANTHROPIC = "Anthropic (Claude)"
AZURE = "Azure OpenAI"
PROVIDERS = (OPENAI, ANTHROPIC, AZURE)
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ENDPOINT = {ANTHROPIC: "https://api.anthropic.com"}


class AIError(RuntimeError):
    pass


def settings():
    return frappe.get_single("Insight AI Settings")


def provider(s=None) -> str:
    s = s or settings()
    p = getattr(s, "ai_provider", None) or OPENAI
    return p if p in PROVIDERS else OPENAI


def ready(s=None) -> bool:
    s = s or settings()
    if not getattr(s, "ai_enabled", 0):
        return False
    return bool(getattr(s, "ai_endpoint", None) or provider(s) == ANTHROPIC)


def _key(s) -> str | None:
    try:
        return s.get_password("ai_api_key", raise_exception=False) if getattr(s, "ai_api_key", None) else None
    except Exception:
        return None


def _endpoint(s) -> str:
    return (getattr(s, "ai_endpoint", None) or DEFAULT_ENDPOINT.get(provider(s), "")).strip().rstrip("/")


# ── pure request/response shaping (unit-tested) ─────────────────────────────

def build_request(prov: str, endpoint: str, model: str, key: str | None, system: str, user: str,
                  temperature: float, max_tokens: int, api_version: str = "") -> tuple[str, dict, dict]:
    """(url, headers, json body) for one chat call."""
    base = (endpoint or "").rstrip("/")
    if prov == ANTHROPIC:
        base = base or DEFAULT_ENDPOINT[ANTHROPIC]
        url = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        headers = {"content-type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
        if key:
            headers["x-api-key"] = key
        body = {"model": model, "max_tokens": int(max_tokens), "temperature": temperature,
                "messages": [{"role": "user", "content": user}]}
        if system:
            body["system"] = system
        return url, headers, body
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": int(max_tokens),
            "stream": False}
    if prov == AZURE:
        url = f"{base}/openai/deployments/{model}/chat/completions?api-version={api_version or '2024-10-21'}"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["api-key"] = key
        body.pop("model")
        return url, headers, body
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return base + "/chat/completions", headers, body


def parse_response(prov: str, data) -> str:
    if not isinstance(data, dict):
        return ""
    if prov == ANTHROPIC:
        return "".join(b.get("text", "") for b in (data.get("content") or [])
                       if isinstance(b, dict) and b.get("type") == "text").strip()
    choice = (data.get("choices") or [{}])[0] or {}
    msg = choice.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):          # some gateways return content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return (content or "").strip()


def explain_error(prov: str, status: int, body_text: str) -> str:
    """Plain advice for a failed call; includes the provider's own message."""
    detail = ""
    try:
        import json
        j = json.loads(body_text or "{}")
        err = j.get("error") if isinstance(j, dict) else None
        if isinstance(j, list) and j and isinstance(j[0], dict):   # Gemini sometimes wraps errors in a list
            err = j[0].get("error")
        detail = (err.get("message") if isinstance(err, dict) else str(err or "")) or j.get("message", "")
    except Exception:
        detail = (body_text or "")[:200]
    hint = {
        400: "The provider rejected the request — usually a wrong model name for this provider.",
        401: "The API key was rejected. Check AI API Key in Insight AI Settings.",
        403: "The API key is not allowed to use this model or endpoint.",
        404: "Endpoint or model not found. Check AI Endpoint and AI Model for the chosen provider.",
        408: "The provider timed out.",
        413: "The request was too large for this model.",
        429: "Rate limit or quota reached at the provider. Wait, or raise the plan's limits.",
        500: "The provider had an internal error.", 502: "The provider is unreachable.",
        503: "The provider is overloaded or down.", 529: "The provider is overloaded.",
    }.get(status, f"The provider returned HTTP {status}.")
    return f"{hint} ({prov}{': ' + detail[:240] if detail else ''})"


def is_external(endpoint: str) -> bool:
    """True when the endpoint is a public host (data leaves your network)."""
    host = (urlparse(endpoint or "").hostname or "").lower()
    if not host or host in ("localhost", "host.docker.internal") or host.endswith((".local", ".internal", ".lan")):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return True


# ── calls ────────────────────────────────────────────────────────────────────

def chat(system: str, user: str, temperature: float = 0.2, max_tokens: int | None = None,
         timeout: int | None = None) -> str:
    """Send one system+user exchange; return the reply text. Raises AIError."""
    import requests
    s = settings()
    if not ready(s):
        raise AIError(_("AI is off. Turn it on in Insight AI Settings."))
    prov = provider(s)
    model = (getattr(s, "ai_model", None) or "").strip()
    if not model:
        raise AIError(_("Set AI Model in Insight AI Settings."))
    url, headers, body = build_request(
        prov, _endpoint(s), model, _key(s), system, user, temperature,
        max_tokens or int(getattr(s, "ai_max_tokens", 0) or 4096), getattr(s, "ai_api_version", "") or "")
    timeout = timeout or int(getattr(s, "ai_timeout_seconds", 0) or 120)
    last = None
    for attempt in range(2):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.exceptions.RequestException as e:
            raise AIError(_("Could not reach the AI endpoint ({0}): {1}").format(prov, str(e)[:200])) from None
        if r.status_code < 300:
            text = parse_response(prov, r.json())
            if not text:
                raise AIError(_("The AI returned an empty answer ({0}).").format(prov))
            return text
        last = explain_error(prov, r.status_code, r.text)
        if r.status_code in (429, 500, 502, 503, 529) and attempt == 0:
            time.sleep(2)
            continue
        break
    raise AIError(last)


def list_models() -> list[str]:
    import requests
    s = settings()
    prov = provider(s)
    if prov == AZURE:
        return []
    base, key = _endpoint(s), _key(s)
    if prov == ANTHROPIC:
        url = base + ("/models" if base.endswith("/v1") else "/v1/models")
        headers = {"anthropic-version": ANTHROPIC_VERSION, **({"x-api-key": key} if key else {})}
    else:
        url = base + "/models"
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get("data", data) if isinstance(data, dict) else data
        return sorted({str(m.get("id")).removeprefix("models/") for m in items if isinstance(m, dict) and m.get("id")})
    except Exception:
        return []


def describe() -> dict:
    s = settings()
    ep = _endpoint(s)
    return {"provider": provider(s), "model": getattr(s, "ai_model", "") or "", "endpoint": ep,
            "external": is_external(ep), "enabled": bool(getattr(s, "ai_enabled", 0))}
