"""Configuration.

Sources, highest priority first: explicit kwargs, environment variables
(``SOULSAKA_SECTION__KEY``), then ``<data_dir>/config.toml``, then defaults.
"""

from __future__ import annotations

import platform
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from soulsaka.paths import config_path

Accelerator = Literal["auto", "cuda", "mps", "cpu"]


class HubConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8765
    # Requests arriving from 127.0.0.1 without a token are treated as the local user.
    trust_loopback: bool = True
    # Background job workers. Keep at 1 so only one copy of each ML model is resident.
    workers: int = 1
    accelerator: Accelerator = "auto"
    # Optional path to a built web UI (defaults to the repo's web/dist if present).
    web_dir: str | None = None


class MeConfig(BaseModel):
    """Who "me" is inside imported data. Used to label training targets."""

    display_name: str = ""
    names: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)


class LLMProfile(BaseModel):
    """One model endpoint the hub can talk to.

    backend:
      openai     - any OpenAI-compatible server: llama.cpp, Ollama, LM Studio, vLLM,
                   or the OpenAI API itself.
      anthropic  - the Anthropic Messages API (needs an API key).
      command    - run a local CLI and read its stdout, e.g. the official ``claude`` or
                   ``codex`` CLI signed in with your own account (experimental).
    """

    backend: Literal["openai", "anthropic", "command"] = "openai"
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "local"
    api_key: str = ""
    # Name of an environment variable holding the key, so keys stay out of config files.
    api_key_env: str = ""
    # For backend=command: argv; the prompt is passed on stdin.
    command: list[str] = Field(default_factory=list)
    # True for anything that leaves the machine. Cloud profiles are refused unless
    # privacy.allow_cloud_llm is on.
    cloud: bool = False
    # Whether this endpoint serves your own adapter (the "sounds like you" path).
    personal: bool = True
    timeout_s: float = 120.0
    max_tokens: int = 512
    temperature: float = 0.8


def builtin_llm_profiles() -> dict[str, LLMProfile]:
    return {
        "local": LLMProfile(),
        "ollama": LLMProfile(base_url="http://127.0.0.1:11434/v1", model="soulsaka"),
        "claude": LLMProfile(
            backend="anthropic",
            base_url="https://api.anthropic.com",
            model="claude-sonnet-5",
            api_key_env="ANTHROPIC_API_KEY",
            cloud=True,
            personal=False,
        ),
        "openai": LLMProfile(
            backend="openai",
            base_url="https://api.openai.com/v1",
            model="gpt-5-mini",
            api_key_env="OPENAI_API_KEY",
            cloud=True,
            personal=False,
        ),
        "claude-cli": LLMProfile(
            backend="command",
            command=["claude", "-p", "--output-format", "text"],
            model="claude-cli",
            cloud=True,
            personal=False,
        ),
        "codex-cli": LLMProfile(
            backend="command",
            command=["codex", "exec", "--skip-git-repo-check", "-"],
            model="codex-cli",
            cloud=True,
            personal=False,
        ),
    }


class LLMConfig(BaseModel):
    default: str = "local"
    # User-defined profiles are merged over the built-ins (same name overrides).
    profiles: dict[str, LLMProfile] = Field(default_factory=builtin_llm_profiles)

    @model_validator(mode="after")
    def _merge_builtins(self) -> LLMConfig:
        merged = builtin_llm_profiles()
        merged.update(self.profiles)
        self.profiles = merged
        return self

    # How many prior turns of a chat to send.
    max_history: int = 12
    # How many retrieved memories / style exemplars to put in the prompt.
    retrieval_k: int = 8
    exemplar_k: int = 6


class ASRConfig(BaseModel):
    backend: Literal["faster-whisper", "mlx-whisper", "fake"] = "faster-whisper"
    model: str = "large-v3-turbo"
    device: Accelerator = "auto"
    compute_type: str = "auto"
    # None = autodetect per utterance (handles Turkish/English code switching).
    language: str | None = None
    # Whisper hallucinates on silence; drop segments below this probability.
    min_no_speech_prob: float = 0.6


class SpeakerConfig(BaseModel):
    backend: Literal["speechbrain", "fake"] = "speechbrain"
    model: str = "speechbrain/spkrec-ecapa-voxceleb"
    # Cosine similarity to the enrolled centroid above which a segment counts as "me".
    threshold: float = 0.55
    # Below this the segment is definitely someone else; in between it is "uncertain".
    reject_threshold: float = 0.35
    min_enroll_samples: int = 3
    min_segment_s: float = 0.8


class EmbedConfig(BaseModel):
    backend: Literal["sentence-transformers", "openai", "hash"] = "sentence-transformers"
    model: str = "BAAI/bge-small-en-v1.5"
    base_url: str = "http://127.0.0.1:8080/v1"
    dim: int = 384


class PrivacyConfig(BaseModel):
    # What to do with speech that is not you: drop it, or keep a transcript as context
    # (never as a training target).
    other_speakers: Literal["discard", "context_only"] = "discard"
    # Keep raw audio of your own utterances (needed for TTS fine-tuning and evals).
    keep_audio: bool = True
    # Hard switch. Off means cloud LLM profiles are refused even if configured.
    allow_cloud_llm: bool = False
    # Hosts the hub may open outbound connections to besides configured LLM profiles.
    allow_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])
    # Hash other people's handles; keep their display names only if true.
    keep_contact_names: bool = True


class TrainConfig(BaseModel):
    backend: Literal["auto", "unsloth", "mlx", "peft"] = "auto"
    base_model: str = "Qwen/Qwen3.5-4B"
    max_seq_len: int = 2048
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.0
    epochs: float = 2.0
    learning_rate: float = 2e-4
    batch_size: int = 2
    grad_accum: int = 8
    # Prior turns included as context for each training target.
    context_window: int = 8
    min_target_words: int = 2
    max_target_words: int = 400
    # Which registers to train on, and whether my side of chats with the assistant counts
    # (off by default: talking to a bot is a narrow register).
    registers: list[str] = Field(default_factory=lambda: ["text", "email", "speech", "doc"])
    include_chat_turns: bool = False
    # Conversation openers (my message with nothing before it) as standalone examples.
    include_openers: bool = True
    max_per_conversation: int = 3000
    holdout_fraction: float = 0.05
    seed: int = 7
    # Serving after training: llama.cpp binary/dir and base GGUF for CUDA; mlx for Apple.
    llama_cpp_dir: str = ""
    base_gguf: str = ""
    serve_port: int = 8080
    # Extra llama-server / mlx_lm.server arguments, e.g. ["--chat-template-kwargs", '{"enable_thinking": false}'].
    serve_extra_args: list[str] = Field(default_factory=list)


class TTSConfig(BaseModel):
    backend: Literal["f5-tts", "fish-speech", "fake"] = "f5-tts"
    reference_clip: str | None = None
    reference_text: str | None = None


class ListenerConfig(BaseModel):
    device: str | None = None  # sounddevice name or index; None = default input
    sample_rate: int = 16000
    vad_threshold: float = 0.5
    min_speech_s: float = 0.6
    max_segment_s: float = 30.0
    silence_end_s: float = 0.8
    pad_s: float = 0.25


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOULSAKA_", env_nested_delimiter="__", extra="ignore"
    )

    hub: HubConfig = Field(default_factory=HubConfig)
    me: MeConfig = Field(default_factory=MeConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    speaker: SpeakerConfig = Field(default_factory=SpeakerConfig)
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    train: TrainConfig = Field(default_factory=TrainConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    listener: ListenerConfig = Field(default_factory=ListenerConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        path = config_path()
        if path.exists():
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=path))
        return tuple(sources)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    """Forget the cached settings (tests, or after ``soulsaka config`` edits)."""
    get_settings.cache_clear()


def detect_accelerator(preference: Accelerator = "auto") -> str:
    """Pick cuda / mps / cpu without importing torch unless needed."""
    if preference != "auto":
        return preference
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:  # torch not installed
        pass
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mps"
    return "cpu"


def settings_to_toml(settings: Settings) -> str:
    """Render settings as a commented TOML document (used by ``soulsaka init``)."""
    data = settings.model_dump(mode="json")
    lines: list[str] = [
        "# soulsaka configuration. Environment variables SOULSAKA_SECTION__KEY override these.",
        "",
    ]

    def render_value(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int | float):
            return repr(v)
        if v is None:
            return '""'
        if isinstance(v, list):
            return "[" + ", ".join(render_value(x) for x in v) + "]"
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def render_table(name: str, table: dict[str, Any]) -> None:
        lines.append(f"[{name}]")
        nested: list[tuple[str, dict[str, Any]]] = []
        for k, v in table.items():
            if isinstance(v, dict):
                nested.append((k, v))
                continue
            lines.append(f"{k} = {render_value(v)}")
        lines.append("")
        for k, v in nested:
            render_table(f"{name}.{k}", v)

    for section, table in data.items():
        render_table(section, table)
    return "\n".join(lines)
