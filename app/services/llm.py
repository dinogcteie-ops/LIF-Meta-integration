"""LLM transport — Gemini (free tier) or Claude, over plain HTTPS.

Design rules (see docs/migrations-runbook.md sibling docs and CLAUDE.md):

* **No SDKs.** Both providers are called with ``requests`` (already a
  dependency) — zero extra RAM on Render's 512MB free tier.
* **Fail soft.** Callers treat LLM features as best-effort: every entry point
  raises :class:`LLMError` on any problem (no key, over budget, HTTP error,
  malformed response) and callers catch it and degrade (lead stays untriaged,
  report falls back to rule-based insights). An LLM outage must never break
  intake or a page render.
* **Budget.** A daily call counter persisted in Settings (one key per day,
  ``llm_calls_<ISO date>``) enforces ``llm_daily_budget``; yesterday's key is
  pruned opportunistically. Keeps us inside the Gemini free quota even if a
  bug loops.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta

import requests

from app.config import get_settings

log = logging.getLogger("uvicorn.error")

_TIMEOUT = 30  # seconds; cron-driven callers can afford it, page callers are async-ish
# Transient server-side blips worth a quick retry (503 = model overloaded).
_RETRY_STATUSES = {503}
_MAX_ATTEMPTS = 3


class LLMError(Exception):
    """Any LLM failure — callers degrade gracefully, never crash."""


# ─── Budget counter (Settings-backed) ────────────────────────────────────────

_BUDGET_PREFIX = "llm_calls_"


def _check_and_count(db) -> None:
    """Increment today's call counter; raise LLMError when over budget."""
    s = get_settings()
    today = date.today().isoformat()
    key = f"{_BUDGET_PREFIX}{today}"
    settings = db.get_settings_dict()
    used = int(settings.get(key) or 0)
    if used >= s.llm_daily_budget:
        raise LLMError(f"LLM daily budget exhausted ({used}/{s.llm_daily_budget})")
    updates = {key: str(used + 1)}
    # Prune yesterday's counter so the settings table doesn't accumulate keys.
    y_key = f"{_BUDGET_PREFIX}{(date.today() - timedelta(days=1)).isoformat()}"
    if y_key in settings:
        updates[y_key] = ""
    db.set_settings(updates)


def calls_used_today(db) -> int:
    key = f"{_BUDGET_PREFIX}{date.today().isoformat()}"
    return int(db.get_settings_dict().get(key) or 0)


# ─── Provider transports ─────────────────────────────────────────────────────

def _complete_gemini(prompt: str, system: str, max_tokens: int,
                     json_mode: bool = False) -> str:
    s = get_settings()
    if not s.gemini_api_key:
        raise LLMError("GEMINI_API_KEY is not set")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{s.gemini_model}:generateContent")
    gen_config: dict = {"maxOutputTokens": max_tokens, "temperature": 0.2}
    if json_mode:
        # Force clean, parseable JSON (no prose, no ``` fences).
        gen_config["responseMimeType"] = "application/json"
    if "2.5" in s.gemini_model:
        # 2.5 flash models "think" by default, which silently consumes the
        # output-token budget and truncates the answer. Disable it — triage and
        # drafts don't need chain-of-thought.
        gen_config["thinkingConfig"] = {"thinkingBudget": 0}
    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": gen_config,
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    headers = {"x-goog-api-key": s.gemini_api_key}
    resp = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, json=body, timeout=_TIMEOUT, headers=headers)
        except requests.RequestException as e:
            if attempt == _MAX_ATTEMPTS:
                raise LLMError(f"Gemini request failed: {e}") from e
            time.sleep(1.5 * attempt)
            continue
        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
            time.sleep(1.5 * attempt)   # transient (e.g. 503 overloaded) — retry
            continue
        break
    if resp.status_code != 200:
        raise LLMError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        cand = resp.json()["candidates"][0]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise LLMError(f"Gemini response malformed: {resp.text[:300]}") from e
    # A truncated answer (MAX_TOKENS) yields invalid JSON downstream — surface a
    # clear, actionable error instead of a confusing parse failure.
    if cand.get("finishReason") == "MAX_TOKENS":
        raise LLMError(f"Gemini hit the {max_tokens}-token limit before finishing "
                       "— raise max_tokens.")
    try:
        return cand["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Gemini response had no text: {resp.text[:300]}") from e


def _complete_anthropic(prompt: str, system: str, max_tokens: int,
                        json_mode: bool = False) -> str:
    # Claude follows the "reply with JSON only" instruction reliably; complete_json
    # strips any ``` fences. json_mode is accepted for a uniform signature.
    s = get_settings()
    if not s.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    body: dict = {
        "model": s.anthropic_model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages", json=body, timeout=_TIMEOUT,
            headers={"x-api-key": s.anthropic_api_key,
                     "anthropic-version": "2023-06-01"})
    except requests.RequestException as e:
        raise LLMError(f"Anthropic request failed: {e}") from e
    if resp.status_code != 200:
        raise LLMError(f"Anthropic HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()["content"][0]["text"]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise LLMError(f"Anthropic response malformed: {resp.text[:300]}") from e


# ─── Public API ───────────────────────────────────────────────────────────────

def is_configured() -> bool:
    """True when an API key exists for the active provider (cheap check for UI)."""
    s = get_settings()
    return bool(s.anthropic_api_key if s.llm_provider == "anthropic"
                else s.gemini_api_key)


def complete(db, prompt: str, system: str = "", max_tokens: int = 1024,
             json_mode: bool = False) -> str:
    """One LLM completion. Counts against the daily budget. Raises LLMError."""
    _check_and_count(db)
    s = get_settings()
    if s.llm_provider == "anthropic":
        return _complete_anthropic(prompt, system, max_tokens, json_mode)
    return _complete_gemini(prompt, system, max_tokens, json_mode)


def complete_json(db, prompt: str, system: str = "", max_tokens: int = 1024) -> dict:
    """``complete`` + strict-ish JSON parsing (tolerates ``` fences)."""
    text = complete(db, prompt, system=system, max_tokens=max_tokens,
                    json_mode=True).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        out = json.loads(text)
    except ValueError as e:
        raise LLMError(f"LLM returned non-JSON: {text[:200]}") from e
    if not isinstance(out, dict):
        raise LLMError(f"LLM JSON is not an object: {text[:200]}")
    return out
