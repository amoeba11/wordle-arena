-- Wordle Arena server schema (PostgreSQL). Load this, then seed.sql.
-- Answers never leave the server; the browser only ever sees colour patterns
-- until a game is finished.

CREATE TABLE IF NOT EXISTS answers (
  day_index  integer PRIMARY KEY,          -- 0 = 2026-09-28 (Arena No. 1); day N uses N mod row count
  word       char(5) NOT NULL
);

CREATE TABLE IF NOT EXISTS valid_words (
  word       char(5) PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS players (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                text NOT NULL CHECK (name ~ '^[A-Za-z0-9 _-]{2,20}$'),
  recovery_code_hash  text UNIQUE,                 -- sha256 of the current recovery code
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS players_name_ci ON players (lower(name));

-- One row per signed-in browser; recovering on a new device adds a session.
CREATE TABLE IF NOT EXISTS sessions (
  token_hash  text PRIMARY KEY,                    -- sha256 of the wa_token cookie
  player_id   uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS games (
  player_id    uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  day_index    integer NOT NULL,
  guesses      text[] NOT NULL DEFAULT '{}',
  started_at   timestamptz,
  finished_at  timestamptz,
  solved       boolean,
  PRIMARY KEY (player_id, day_index)
);
CREATE INDEX IF NOT EXISTS games_day ON games (day_index) WHERE finished_at IS NOT NULL;
