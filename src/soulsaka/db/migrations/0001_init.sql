-- Core schema. Timestamps are ISO-8601 UTC strings. Integer primary keys everywhere;
-- `uid` columns carry client-generated identifiers for idempotent sync.

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE devices (
  id           INTEGER PRIMARY KEY,
  uid          TEXT NOT NULL UNIQUE,
  name         TEXT NOT NULL,
  kind         TEXT NOT NULL,             -- browser | listener | importer | cli
  token_hash   TEXT NOT NULL UNIQUE,
  created_at   TEXT NOT NULL,
  last_seen_at TEXT
);

CREATE TABLE pairing_codes (
  code       TEXT PRIMARY KEY,
  expires_at TEXT NOT NULL,
  used_at    TEXT
);

-- Where corpus data came from.
CREATE TABLE sources (
  id             INTEGER PRIMARY KEY,
  kind           TEXT NOT NULL,           -- imessage | whatsapp | email | discord | git | capture | doc | ...
  label          TEXT NOT NULL,
  locator        TEXT NOT NULL DEFAULT '', -- path / account, local only
  device_uid     TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL,
  last_import_at TEXT,
  UNIQUE(kind, locator, device_uid)
);

-- People. Handles (phone numbers, emails, usernames) are stored only as salted hashes.
CREATE TABLE contacts (
  id           INTEGER PRIMARY KEY,
  handle_hash  TEXT NOT NULL UNIQUE,
  display_name TEXT,
  is_me        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE conversations (
  id          INTEGER PRIMARY KEY,
  source_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  external_id TEXT NOT NULL,
  title       TEXT,
  is_group    INTEGER NOT NULL DEFAULT 0,
  UNIQUE(source_id, external_id)
);

-- The corpus. `is_me` rows are the only ones ever used as training targets.
CREATE TABLE messages (
  id              INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  contact_id      INTEGER REFERENCES contacts(id),
  is_me           INTEGER NOT NULL,
  ts              TEXT NOT NULL,
  register        TEXT NOT NULL,          -- text | email | speech | doc
  lang            TEXT,                   -- en | tr | ... | NULL
  text            TEXT NOT NULL,
  word_count      INTEGER NOT NULL,
  external_id     TEXT,
  content_hash    TEXT NOT NULL,
  meta            TEXT,                   -- JSON
  UNIQUE(conversation_id, content_hash)
);
CREATE INDEX idx_messages_conv_ts ON messages(conversation_id, ts);
CREATE INDEX idx_messages_me_reg  ON messages(is_me, register, ts);
CREATE INDEX idx_messages_ts      ON messages(ts);

CREATE VIRTUAL TABLE messages_fts USING fts5(
  text, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER messages_au AFTER UPDATE OF text ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;

-- Raw captures from clients (typed text, push-to-talk audio, listener segments).
CREATE TABLE captures (
  id             INTEGER PRIMARY KEY,
  uid            TEXT NOT NULL UNIQUE,
  device_uid     TEXT NOT NULL,
  kind           TEXT NOT NULL,           -- text | audio
  origin         TEXT NOT NULL DEFAULT 'manual', -- manual | listener | chat
  status         TEXT NOT NULL DEFAULT 'pending', -- pending | processing | done | failed | discarded
  client_ts      TEXT NOT NULL,
  received_at    TEXT NOT NULL,
  processed_at   TEXT,
  text           TEXT,                    -- typed text, or transcript once processed
  lang           TEXT,
  audio_path     TEXT,                    -- relative to the data dir
  duration_s     REAL,
  speaker_is_me  INTEGER,                 -- NULL until verified; 1 me, 0 other
  speaker_score  REAL,
  message_id     INTEGER REFERENCES messages(id) ON DELETE SET NULL,
  error          TEXT,
  meta           TEXT
);
CREATE INDEX idx_captures_status ON captures(status, received_at);
CREATE INDEX idx_captures_received ON captures(received_at);

-- Things the assistant should know. Searchable, syncs to every device.
CREATE TABLE memories (
  id          INTEGER PRIMARY KEY,
  uid         TEXT NOT NULL UNIQUE,
  kind        TEXT NOT NULL,              -- note | fact | preference | todo | number | event | person
  text        TEXT NOT NULL,
  source_kind TEXT NOT NULL,              -- explicit | extracted | import | manual
  source_ref  TEXT,                       -- capture uid / message id
  confidence  REAL NOT NULL DEFAULT 1.0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  expires_at  TEXT,
  archived    INTEGER NOT NULL DEFAULT 0,
  meta        TEXT
);
CREATE INDEX idx_memories_updated ON memories(updated_at);
CREATE VIRTUAL TABLE memories_fts USING fts5(
  text, content='memories', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER memories_au AFTER UPDATE OF text ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;

-- Vector index. Float32 little-endian blobs; brute-force cosine is fine at this scale.
CREATE TABLE embeddings (
  owner_kind TEXT NOT NULL,               -- message | memory
  owner_id   INTEGER NOT NULL,
  model      TEXT NOT NULL,
  dim        INTEGER NOT NULL,
  vec        BLOB NOT NULL,
  PRIMARY KEY (owner_kind, owner_id, model)
);

-- Enrolled voices. Only "me" is required.
CREATE TABLE speaker_profiles (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  model      TEXT NOT NULL,
  dim        INTEGER NOT NULL,
  centroid   BLOB NOT NULL,
  n_samples  INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);

-- Durable background job queue.
CREATE TABLE jobs (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,
  payload     TEXT NOT NULL,              -- JSON
  status      TEXT NOT NULL DEFAULT 'queued', -- queued | running | done | failed
  priority    INTEGER NOT NULL DEFAULT 0,
  attempts    INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  run_after   TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  started_at  TEXT,
  finished_at TEXT,
  error       TEXT
);
CREATE INDEX idx_jobs_status ON jobs(status, priority DESC, run_after);

-- Chat with the assistant. Assistant turns are never training targets.
CREATE TABLE chats (
  id         INTEGER PRIMARY KEY,
  uid        TEXT NOT NULL UNIQUE,
  title      TEXT,
  device_uid TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE chat_turns (
  id         INTEGER PRIMARY KEY,
  chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role       TEXT NOT NULL,               -- user | assistant | system
  text       TEXT NOT NULL,
  profile    TEXT,                        -- llm profile used for assistant turns
  created_at TEXT NOT NULL,
  meta       TEXT
);
CREATE INDEX idx_chat_turns_chat ON chat_turns(chat_id, id);

-- Versioned adapters. Cumulative retrains from base, never incremental.
CREATE TABLE training_runs (
  id           INTEGER PRIMARY KEY,
  version      TEXT NOT NULL UNIQUE,      -- v1, v2, ...
  backend      TEXT NOT NULL,
  base_model   TEXT NOT NULL,
  status       TEXT NOT NULL,             -- planned | running | done | failed
  config       TEXT NOT NULL,             -- JSON
  dataset_path TEXT,
  dataset_hash TEXT,
  data_cutoff  TEXT,
  n_examples   INTEGER,
  n_words      INTEGER,
  adapter_path TEXT,
  gguf_path    TEXT,
  metrics      TEXT,                      -- JSON
  started_at   TEXT,
  finished_at  TEXT,
  error        TEXT
);

-- Fidelity measurements per version. This table is the deliverable.
CREATE TABLE eval_results (
  id         INTEGER PRIMARY KEY,
  version    TEXT NOT NULL,
  kind       TEXT NOT NULL,               -- blind_pairs | discriminator | voice_similarity
  metric     TEXT NOT NULL,               -- guess_accuracy | clf_accuracy | cosine
  value      REAL NOT NULL,
  n          INTEGER,
  details    TEXT,                        -- JSON
  created_at TEXT NOT NULL
);
CREATE INDEX idx_eval_results_version ON eval_results(version, kind);

CREATE TABLE eval_pairs (
  id         INTEGER PRIMARY KEY,
  uid        TEXT NOT NULL UNIQUE,
  version    TEXT NOT NULL,
  context    TEXT NOT NULL,
  real_text  TEXT NOT NULL,
  model_text TEXT NOT NULL,
  real_first INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE eval_guesses (
  id                    INTEGER PRIMARY KEY,
  pair_uid              TEXT NOT NULL REFERENCES eval_pairs(uid) ON DELETE CASCADE,
  rater                 TEXT NOT NULL,
  guessed_real_is_first INTEGER NOT NULL,
  correct               INTEGER NOT NULL,
  created_at            TEXT NOT NULL
);
CREATE INDEX idx_eval_guesses_pair ON eval_guesses(pair_uid);
