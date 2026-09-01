"""Service registry. Each service is built lazily from settings; tests inject fakes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from soulsaka.hub.state import HubState


def build(name: str, state: HubState):
    s = state.settings
    if name == "asr":
        from soulsaka.ml.asr import build_asr

        return build_asr(s.asr, s.hub.accelerator)
    if name == "speaker":
        from soulsaka.ml.speaker import build_speaker

        return build_speaker(s.speaker, s.hub.accelerator)
    if name == "embedder":
        from soulsaka.ml.embed import build_embedder

        return build_embedder(s.embed)
    if name == "llm":
        from soulsaka.ml.llm import LLMRouter

        return LLMRouter(s.llm, s.privacy)
    if name == "tts":
        from soulsaka.voice.tts import build_tts

        return build_tts(s.tts, state)
    raise KeyError(f"unknown service {name!r}")
