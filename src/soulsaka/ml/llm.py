"""Chat-completion backends behind one router.

Profiles come from config. Local servers (llama.cpp, Ollama, LM Studio, vLLM) and the
OpenAI API share the ``openai`` backend; ``anthropic`` talks to the Messages API;
``command`` pipes the prompt through a local CLI such as the official ``claude`` or
``codex`` tools. Anything marked cloud is refused unless privacy.allow_cloud_llm is on.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from soulsaka.config import LLMConfig, LLMProfile, PrivacyConfig

log = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    text: str
    profile: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


class LLMError(Exception):
    pass


class CloudRefused(LLMError):
    pass


def is_local_host(host: str, allow_hosts: list[str]) -> bool:
    if not host:
        return False
    if host in allow_hosts or host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _api_key(profile: LLMProfile) -> str:
    if profile.api_key:
        return profile.api_key
    if profile.api_key_env:
        return os.environ.get(profile.api_key_env, "")
    return ""


def _flatten(messages: list[ChatMessage]) -> str:
    parts = []
    for m in messages:
        label = {"system": "System", "user": "User", "assistant": "Assistant"}.get(m.role, m.role)
        parts.append(f"{label}: {m.content}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


class LLMRouter:
    def __init__(self, cfg: LLMConfig, privacy: PrivacyConfig):
        self.cfg = cfg
        self.privacy = privacy
        self._clients: dict[str, httpx.Client] = {}

    # -- profiles ----------------------------------------------------------------------
    def profile(self, name: str | None = None) -> tuple[str, LLMProfile]:
        name = name or self.cfg.default
        prof = self.cfg.profiles.get(name)
        if prof is None:
            raise LLMError(f"unknown llm profile {name!r}")
        if prof.cloud and not self.privacy.allow_cloud_llm:
            raise CloudRefused(
                f"profile {name!r} sends data off this machine; set privacy.allow_cloud_llm = true to allow it"
            )
        if prof.backend in ("openai", "anthropic") and not prof.cloud:
            host = urlparse(prof.base_url).hostname or ""
            if not is_local_host(host, self.privacy.allow_hosts):
                raise LLMError(
                    f"profile {name!r} points at {host!r}, which is not a local host; mark it cloud = true"
                )
        return name, prof

    def list_profiles(self) -> list[dict[str, Any]]:
        out = []
        for name, p in self.cfg.profiles.items():
            out.append(
                {
                    "name": name,
                    "backend": p.backend,
                    "model": p.model,
                    "cloud": p.cloud,
                    "personal": p.personal,
                    "enabled": (not p.cloud) or self.privacy.allow_cloud_llm,
                    "default": name == self.cfg.default,
                }
            )
        return out

    def available(self, name: str | None = None, timeout: float = 2.0) -> bool:
        try:
            _, prof = self.profile(name)
        except LLMError:
            return False
        try:
            if prof.backend == "openai":
                r = self._client(prof).get("/models", headers=self._headers(prof), timeout=timeout)
                return r.status_code < 500
            if prof.backend == "anthropic":
                return bool(_api_key(prof))
            if prof.backend == "command":
                return bool(prof.command) and shutil.which(prof.command[0]) is not None
        except Exception:  # noqa: BLE001
            return False
        return False

    # -- completion --------------------------------------------------------------------
    def complete(
        self,
        messages: list[ChatMessage],
        *,
        profile: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        name, prof = self.profile(profile)
        max_tokens = max_tokens or prof.max_tokens
        temperature = prof.temperature if temperature is None else temperature
        if prof.backend == "openai":
            return self._openai(name, prof, messages, max_tokens, temperature, json_mode, stop)
        if prof.backend == "anthropic":
            return self._anthropic(name, prof, messages, max_tokens, temperature, stop)
        return self._command(name, prof, messages)

    def stream(
        self,
        messages: list[ChatMessage],
        *,
        profile: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> Iterator[str]:
        name, prof = self.profile(profile)
        max_tokens = max_tokens or prof.max_tokens
        temperature = prof.temperature if temperature is None else temperature
        if prof.backend == "openai":
            yield from self._openai_stream(prof, messages, max_tokens, temperature, stop)
        elif prof.backend == "anthropic":
            yield from self._anthropic_stream(prof, messages, max_tokens, temperature, stop)
        else:
            yield self._command(name, prof, messages).text

    # -- backends ----------------------------------------------------------------------
    def _client(self, prof: LLMProfile) -> httpx.Client:
        key = prof.base_url
        c = self._clients.get(key)
        if c is None:
            c = httpx.Client(base_url=prof.base_url.rstrip("/"), timeout=prof.timeout_s)
            self._clients[key] = c
        return c

    @staticmethod
    def _headers(prof: LLMProfile) -> dict[str, str]:
        key = _api_key(prof)
        if prof.backend == "anthropic":
            return {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {"Authorization": f"Bearer {key or 'none'}"}

    def _openai(
        self, name, prof, messages, max_tokens, temperature, json_mode, stop
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": prof.model,
            "messages": [m.as_dict() for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop:
            body["stop"] = stop
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = self._client(prof).post("/chat/completions", json=body, headers=self._headers(prof))
        if r.status_code >= 400:
            raise LLMError(f"{name}: HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"{name}: malformed response: {data}") from e
        return LLMResponse(
            text=text,
            profile=name,
            model=data.get("model", prof.model),
            usage=data.get("usage") or {},
            raw=data,
        )

    def _openai_stream(self, prof, messages, max_tokens, temperature, stop) -> Iterator[str]:
        body: dict[str, Any] = {
            "model": prof.model,
            "messages": [m.as_dict() for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if stop:
            body["stop"] = stop
        with self._client(prof).stream(
            "POST", "/chat/completions", json=body, headers=self._headers(prof)
        ) as r:
            if r.status_code >= 400:
                r.read()
                raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0].get("delta", {}).get("content")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta

    @staticmethod
    def _split_system(messages: list[ChatMessage]) -> tuple[str, list[dict[str, str]]]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        rest = [m.as_dict() for m in messages if m.role != "system"]
        if not rest or rest[0]["role"] != "user":
            rest.insert(0, {"role": "user", "content": "(continue)"})
        return system, rest

    def _anthropic(self, name, prof, messages, max_tokens, temperature, stop) -> LLMResponse:
        system, msgs = self._split_system(messages)
        body: dict[str, Any] = {
            "model": prof.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": msgs,
        }
        if system:
            body["system"] = system
        if stop:
            body["stop_sequences"] = stop
        r = self._client(prof).post("/v1/messages", json=body, headers=self._headers(prof))
        if r.status_code >= 400:
            raise LLMError(f"{name}: HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        return LLMResponse(
            text=text,
            profile=name,
            model=data.get("model", prof.model),
            usage=data.get("usage") or {},
            raw=data,
        )

    def _anthropic_stream(self, prof, messages, max_tokens, temperature, stop) -> Iterator[str]:
        system, msgs = self._split_system(messages)
        body: dict[str, Any] = {
            "model": prof.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": msgs,
            "stream": True,
        }
        if system:
            body["system"] = system
        if stop:
            body["stop_sequences"] = stop
        with self._client(prof).stream(
            "POST", "/v1/messages", json=body, headers=self._headers(prof)
        ) as r:
            if r.status_code >= 400:
                r.read()
                raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "content_block_delta":
                    delta = ev.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]

    def _command(self, name, prof, messages) -> LLMResponse:
        if not prof.command:
            raise LLMError(f"{name}: no command configured")
        prompt = _flatten(messages)
        argv = [a.replace("{prompt}", prompt) for a in prof.command]
        use_stdin = not any("{prompt}" in a for a in prof.command)
        try:
            proc = subprocess.run(
                argv,
                input=prompt if use_stdin else None,
                capture_output=True,
                text=True,
                timeout=prof.timeout_s,
                check=False,
            )
        except FileNotFoundError as e:
            raise LLMError(f"{name}: command not found: {prof.command[0]}") from e
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"{name}: command timed out") from e
        if proc.returncode != 0:
            raise LLMError(f"{name}: exit {proc.returncode}: {proc.stderr[-300:]}")
        return LLMResponse(text=proc.stdout.strip(), profile=name, model=prof.model)


def extract_json(text: str) -> Any:
    """Parse the first JSON object in a model reply, tolerating chatter around it."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object found")
