"""Provider-independent model interface: Anthropic (SDK) or any OpenAI-compatible endpoint."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date

import httpx

from .. import config, db
from ..timeutil import iso, now_utc


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Turn:
    text: str
    calls: list[ToolCall]
    done: bool
    usage: dict = field(default_factory=dict)


class BudgetExceeded(Exception):
    pass


def spent_today() -> float:
    r = db.q1("SELECT COALESCE(SUM(cost_usd),0) s FROM model_usage WHERE day=?", (date.today().isoformat(),))
    return float(r["s"]) if r else 0.0


def _record_usage(provider: str, model: str, inp: int, out: int) -> float:
    s = config.settings
    cost = inp / 1e6 * s.input_cost_mtok + out / 1e6 * s.output_cost_mtok
    db.ex("INSERT INTO model_usage(day,provider,model,input_tokens,output_tokens,cost_usd,at) VALUES(?,?,?,?,?,?,?)",
          (date.today().isoformat(), provider, model, inp, out, cost, iso(now_utc())))
    return cost


def _check_budget():
    if spent_today() >= config.settings.max_daily_model_cost > 0:
        raise BudgetExceeded("Daily model cost cap reached (MAX_DAILY_MODEL_COST).")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, system: str, tools: list[dict]):
        import anthropic
        s = config.settings
        # base_url is explicit so a stray ANTHROPIC_BASE_URL in the shell is never used.
        self.client = anthropic.Anthropic(api_key=s.anthropic_key, base_url=s.anthropic_base_url, max_retries=2)
        self.model = s.anthropic_model
        self.system = system
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        self.messages: list = []

    def start(self, user: str) -> None:
        self.messages = [{"role": "user", "content": user}]

    def step(self) -> Turn:
        _check_budget()
        kwargs = dict(model=self.model, max_tokens=16000, system=self.system, tools=self.tools, messages=self.messages)
        effort = config.settings.anthropic_effort
        if effort:
            kwargs["output_config"] = {"effort": effort}
        resp = self.client.messages.create(**kwargs)
        u = resp.usage
        _record_usage("anthropic", self.model, u.input_tokens, u.output_tokens)
        if resp.stop_reason == "refusal":
            return Turn("The model declined this request.", [], True, {"stop": "refusal"})
        self.messages.append({"role": "assistant", "content": resp.content})
        text = "\n".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        return Turn(text, calls, resp.stop_reason != "tool_use",
                    {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens, "stop": resp.stop_reason})

    def add_results(self, results: list[tuple[str, str, bool]], note: str | None = None) -> None:
        blocks = [{"type": "tool_result", "tool_use_id": cid, "content": content, "is_error": err}
                  for cid, content, err in results]
        if note:
            blocks.append({"type": "text", "text": note})
        self.messages.append({"role": "user", "content": blocks})


# Models that refused recently are skipped until their cooldown ends, so an exhausted free-tier quota or an
# overloaded model never costs a round-trip (and a retry wait) on every agent step.
_cooldown: dict[str, float] = {}


def _candidates() -> list[tuple[str, str, str]]:
    """(base_url, api_key, model) in preference order: configured model, its fallbacks, then Groq if configured."""
    s = config.settings
    base = s.compat_base_url.rstrip("/")
    out = [(base, s.compat_key, m) for m in [s.compat_model] + [x for x in s.compat_fallbacks if x != s.compat_model]]
    if s.groq_key:
        out.append(("https://api.groq.com/openai/v1", s.groq_key, s.groq_model))
    return out


def post_chat(body: dict, timeout: float = 45) -> tuple[httpx.Response, str]:
    """POST a chat completion to the first available candidate. Returns (response, 'model')."""
    cands = _candidates()
    live = [c for c in cands if _cooldown.get(c[2], 0) <= time.time()] or cands  # if all are cooling down, try anyway
    last = ""
    for base, key, model in live:
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
        try:
            r = httpx.post(f"{base}/chat/completions", headers=headers, timeout=timeout, json={**body, "model": model})
        except httpx.TimeoutException:
            _cooldown[model], last = time.time() + 120, f"{model}: timeout"
            continue
        if r.status_code == 429:
            daily = "PerDay" in r.text or "per day" in r.text.lower()
            _cooldown[model] = time.time() + (6 * 3600 if daily else 60)
            last = f"{model}: {'daily quota used up' if daily else 'rate limited'}"
            continue
        if r.status_code in (404, 500, 502, 503, 504):
            _cooldown[model] = time.time() + (86400 if r.status_code == 404 else 120)
            last = f"{model}: HTTP {r.status_code}"
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Model endpoint error {r.status_code}: {r.text[:300]}")
        return r, model
    raise RuntimeError(f"All configured models unavailable ({last}). Try again later.")


class OpenAICompatProvider:
    name = "openai_compatible"

    def __init__(self, system: str, tools: list[dict]):
        s = config.settings
        self.model = s.compat_model
        self.tools = [{"type": "function", "function": t} for t in tools]
        self.messages: list = [{"role": "system", "content": system}]

    def start(self, user: str) -> None:
        self.messages.append({"role": "user", "content": user})

    def step(self) -> Turn:
        _check_budget()
        r, self.model = post_chat({"messages": self.messages, "tools": self.tools, "tool_choice": "auto"})
        data = r.json()
        msg = data["choices"][0]["message"]
        u = data.get("usage") or {}
        _record_usage(self.name, self.model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        clean = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            clean["tool_calls"] = msg["tool_calls"]
        self.messages.append(clean)
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except ValueError:
                args = {"_invalid_json": tc["function"].get("arguments")}
            calls.append(ToolCall(tc["id"], tc["function"]["name"], args))
        return Turn(msg.get("content") or "", calls, not calls,
                    {"input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")})

    def add_results(self, results: list[tuple[str, str, bool]], note: str | None = None) -> None:
        for cid, content, err in results:
            self.messages.append({"role": "tool", "tool_call_id": cid, "content": ("ERROR: " if err else "") + content})
        if note:
            self.messages.append({"role": "user", "content": note})


def make_provider(system: str, tools: list[dict]):
    s = config.settings
    if not s.model_configured:
        return None
    if s.model_provider == "anthropic":
        return AnthropicProvider(system, tools)
    return OpenAICompatProvider(system, tools)


def complete_json(prompt: str, max_tokens: int = 800) -> tuple[dict | None, str]:
    """One-shot JSON completion with the configured model (no tools). Returns (data, model) or (None, reason)."""
    s = config.settings
    if not s.model_configured:
        return None, "no model configured"
    _check_budget()
    text, model = "", ""
    if s.model_provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=s.anthropic_key, base_url=s.anthropic_base_url, max_retries=2)
        resp = client.messages.create(model=s.anthropic_model, max_tokens=max_tokens,
                                      messages=[{"role": "user", "content": prompt}])
        _record_usage("anthropic", s.anthropic_model, resp.usage.input_tokens, resp.usage.output_tokens)
        text, model = "".join(b.text for b in resp.content if b.type == "text"), s.anthropic_model
    else:
        try:
            r, m = post_chat({"messages": [{"role": "user", "content": prompt}]})
        except RuntimeError:
            return None, "model unavailable"
        data = r.json()
        u = data.get("usage") or {}
        _record_usage("openai_compatible", m, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        text, model = data["choices"][0]["message"].get("content") or "", m
    if not text:
        return None, "model unavailable"
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, "no JSON in model reply"
    try:
        return json.loads(text[start:end + 1]), model
    except ValueError:
        return None, "invalid JSON from model"
