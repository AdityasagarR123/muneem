"""LLM backends behind one small interface, so the provider can be swapped.

The agent loop only uses: start / add_model_turn / add_tool_results / generate.
GeminiBackend tries the configured models in order and skips any model that is
rate-limited (429) or unavailable, remembering a cooldown so later requests go
straight to a model that still has quota.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("muneem.llm")


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str | None
    name: str
    args: dict


@dataclass
class LLMReply:
    text: str
    calls: list[ToolCall]
    model: str
    raw: Any = None  # provider message, appended back to history unchanged
    usage: dict = field(default_factory=dict)


class LLMBackend(Protocol):
    provider: str
    models: list[str]

    def start(self, history: list[tuple[str, str]], user_text: str) -> list: ...
    def generate(self, system: str, contents: list, tools: list[dict], allow_tools: bool = True) -> LLMReply: ...
    def add_model_turn(self, contents: list, reply: LLMReply) -> None: ...
    def add_user_text(self, contents: list, text: str) -> None: ...
    def add_tool_results(self, contents: list, results: list[tuple[ToolCall, dict]]) -> None: ...
    def status(self) -> dict: ...


class GeminiBackend:
    provider = "gemini"

    def __init__(self, api_key: str, models: list[str], timeout_s: int = 30, cooldown_s: int = 60):
        from google import genai
        from google.genai import types

        self.types = types
        # no SDK-level retries: on 429 we move to the next model at once instead of waiting
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=timeout_s * 1000, retry_options=types.HttpRetryOptions(attempts=1)))
        self.models = models
        self.cooldown_s = cooldown_s
        self._cool: dict[str, float] = {}
        self._dead: set[str] = set()
        self.last_error: str | None = None

    # -- conversation building ------------------------------------------------
    def start(self, history: list[tuple[str, str]], user_text: str) -> list:
        T = self.types
        contents = [T.Content(role="user" if role == "user" else "model", parts=[T.Part(text=text)])
                    for role, text in history]
        contents.append(T.Content(role="user", parts=[T.Part(text=user_text)]))
        return contents

    def add_model_turn(self, contents: list, reply: LLMReply) -> None:
        contents.append(reply.raw)  # unchanged: keeps thought signatures intact

    def add_user_text(self, contents: list, text: str) -> None:
        T = self.types
        contents.append(T.Content(role="user", parts=[T.Part(text=text)]))

    def add_tool_results(self, contents: list, results: list[tuple[ToolCall, dict]]) -> None:
        T = self.types
        parts = [T.Part(function_response=T.FunctionResponse(id=c.id, name=c.name, response=r))
                 for c, r in results]
        contents.append(T.Content(role="user", parts=parts))

    # -- calling ----------------------------------------------------------------
    def _config(self, model: str, system: str, tools: list[dict], allow_tools: bool):
        T = self.types
        decls = [T.FunctionDeclaration(name=t["name"], description=t["description"],
                                       parameters_json_schema=t["parameters"]) for t in tools]
        # tool routing needs little reasoning; low thinking keeps answers ~2-3 s
        thinking = (T.ThinkingConfig(thinking_level=T.ThinkingLevel.LOW)
                    if model.startswith("gemini-3") else None)
        return T.GenerateContentConfig(
            system_instruction=system,
            thinking_config=thinking,
            tools=[T.Tool(function_declarations=decls)] if decls else None,
            tool_config=T.ToolConfig(function_calling_config=T.FunctionCallingConfig(
                mode="AUTO" if allow_tools else "NONE")) if decls else None,
            automatic_function_calling=T.AutomaticFunctionCallingConfig(disable=True),
        )

    def generate(self, system: str, contents: list, tools: list[dict],
                 allow_tools: bool = True) -> LLMReply:
        from google.genai import errors

        now = time.time()
        order = [m for m in self.models if m not in self._dead and self._cool.get(m, 0) <= now]
        order = order or [m for m in self.models if m not in self._dead]  # all cooling: try anyway
        last = None
        for model in order:
            try:
                cfg = self._config(model, system, tools, allow_tools)
                r = self.client.models.generate_content(model=model, contents=contents, config=cfg)
            except errors.ClientError as exc:
                last = exc
                if exc.code == 429:
                    self._cool[model] = time.time() + self.cooldown_s
                    log.warning("%s rate-limited; trying next model", model)
                    continue
                if exc.code == 404:
                    self._dead.add(model)
                    log.warning("%s not available; removed", model)
                    continue
                raise LLMUnavailable(f"{model}: {exc.code} {str(exc)[:160]}") from exc
            except errors.ServerError as exc:
                last = exc
                log.warning("%s server error %s; trying next model", model, exc.code)
                continue
            except Exception as exc:  # network / timeout
                last = exc
                log.warning("%s call failed: %s", model, exc)
                continue
            cand = (r.candidates or [None])[0]
            if cand is None or cand.content is None:
                reason = getattr(cand, "finish_reason", None) if cand else "no candidate"
                raise LLMUnavailable(f"{model}: empty response ({reason})")
            calls = [ToolCall(id=fc.id, name=fc.name, args=dict(fc.args or {}))
                     for fc in (r.function_calls or [])]
            text = "".join(p.text for p in (cand.content.parts or [])
                           if getattr(p, "text", None) and not getattr(p, "thought", False))
            um = r.usage_metadata
            usage = {"in": getattr(um, "prompt_token_count", None),
                     "out": getattr(um, "candidates_token_count", None)} if um else {}
            self.last_error = None
            return LLMReply(text=text.strip(), calls=calls, model=model, raw=cand.content, usage=usage)
        self.last_error = f"{type(last).__name__}: {str(last)[:200]}" if last else "no model available"
        raise LLMUnavailable(self.last_error)

    def status(self) -> dict:
        now = time.time()
        return {"provider": self.provider, "models": self.models,
                "cooling_down": {m: round(t - now) for m, t in self._cool.items() if t > now},
                "unavailable": sorted(self._dead), "last_error": self.last_error}


def build_backend(settings) -> LLMBackend | None:
    if not settings.llm_enabled:
        return None
    if settings.llm_provider.lower() == "gemini":
        return GeminiBackend(settings.llm_api_key.strip(), settings.llm_models,
                             settings.llm_timeout_s, settings.llm_cooldown_s)
    log.warning("LLM provider '%s' is not supported; chat runs in rules mode",
                settings.llm_provider)
    return None
