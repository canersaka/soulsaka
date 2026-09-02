"""Nothing in the hub may talk to a host that is not either local or an explicitly
documented endpoint. This scans the source so a stray telemetry URL cannot sneak in."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "soulsaka"
URL_RE = re.compile(r"https?://([A-Za-z0-9.\-]+)")

ALLOWED_HOSTS = {
    "127.0.0.1",
    "localhost",
    # cloud LLM profiles, off unless privacy.allow_cloud_llm is set
    "api.anthropic.com",
    "api.openai.com",
    # documentation links only
    "github.com",
    "pytorch.org",
    "www.w3.org",
    "www.apple.com",
}


def test_no_unexpected_hosts_in_source():
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        for host in URL_RE.findall(path.read_text(encoding="utf-8")):
            if host not in ALLOWED_HOSTS:
                found.setdefault(host, set()).add(str(path.relative_to(SRC)))
    assert not found, f"unexpected hosts referenced: {found}"


def test_cloud_profiles_are_marked_cloud():
    from urllib.parse import urlparse

    from soulsaka.config import builtin_llm_profiles
    from soulsaka.ml.llm import is_local_host

    for name, prof in builtin_llm_profiles().items():
        if prof.backend in ("openai", "anthropic"):
            host = urlparse(prof.base_url).hostname or ""
            assert prof.cloud == (not is_local_host(host, [])), name
        else:
            assert prof.cloud, f"CLI bridge {name} must be marked cloud"
