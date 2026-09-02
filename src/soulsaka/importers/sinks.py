"""Where imported messages end up: the local database or a hub over HTTP."""

from __future__ import annotations

from typing import TYPE_CHECKING

from soulsaka.client import ClientConfig, HubClient
from soulsaka.db import corpus as corpus_db
from soulsaka.models import ImportedMessage, ImportReport, MessageBatch, SourceRef

if TYPE_CHECKING:
    from soulsaka.hub.state import HubState


class DbSink:
    """Write straight into the local corpus (when the importer runs on the hub machine)."""

    def __init__(self, state: HubState):
        self.state = state

    def write(self, source: SourceRef, messages: list[ImportedMessage]) -> ImportReport:
        return corpus_db.ingest_messages(
            self.state.db,
            self.state.salt,
            source,
            messages,
            keep_names=self.state.settings.privacy.keep_contact_names,
        )

    def me_words(self) -> int:
        return corpus_db.stats(self.state.db).me_words

    def describe(self) -> str:
        return f"local database {self.state.db.path}"

    def close(self) -> None:
        self.state.close()


class HubSink:
    """Post batches to a paired hub's ``/api/messages/batch``."""

    def __init__(self, client: HubClient):
        self.client = client

    def write(self, source: SourceRef, messages: list[ImportedMessage]) -> ImportReport:
        return self.client.push_messages(MessageBatch(source=source, messages=messages))

    def me_words(self) -> int:
        return self.client.stats().me_words

    def describe(self) -> str:
        return f"hub {self.client.base_url}"

    def close(self) -> None:
        self.client.close()


def open_sink(*, local: bool = False, hub: str | None = None) -> DbSink | HubSink:
    """Pick the destination: an explicit hub URL, the paired hub, else the local database."""
    cfg = ClientConfig.load()
    if hub:
        token = cfg.token if cfg and cfg.token else None
        return HubSink(HubClient(hub, token))
    if not local and cfg and cfg.token:
        return HubSink(HubClient.from_config())
    from soulsaka.config import get_settings
    from soulsaka.hub.state import HubState

    return DbSink(HubState(get_settings()))
