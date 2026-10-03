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

-- ---------- Group competitions ----------
-- Contest and race words never leave the server until that puzzle/round is finished.

CREATE TABLE IF NOT EXISTS contests (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code        char(6) NOT NULL UNIQUE,                 -- invite code, alphabet ABCDEFGHJKMNPQRSTVWXYZ23456789
  name        text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 40),
  host_id     uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  start_day   integer NOT NULL,                        -- same day index as answers/games
  days        integer NOT NULL CHECK (days BETWEEN 1 AND 14),
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contest_members (
  contest_id  uuid NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
  player_id   uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  joined_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (contest_id, player_id)
);

CREATE TABLE IF NOT EXISTS contest_puzzles (
  contest_id  uuid NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
  n           integer NOT NULL,                        -- 0-based day within the contest
  word        char(5) NOT NULL,
  PRIMARY KEY (contest_id, n)
);

CREATE TABLE IF NOT EXISTS contest_games (
  contest_id   uuid NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
  n            integer NOT NULL,
  player_id    uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  guesses      text[] NOT NULL DEFAULT '{}',
  started_at   timestamptz,
  finished_at  timestamptz,
  solved       boolean,
  PRIMARY KEY (contest_id, n, player_id)
);

CREATE TABLE IF NOT EXISTS races (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code            char(6) NOT NULL UNIQUE,
  name            text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 40),
  host_id         uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  created_at      timestamptz NOT NULL DEFAULT now(),
  last_active_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS race_members (
  race_id    uuid NOT NULL REFERENCES races(id) ON DELETE CASCADE,
  player_id  uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  joined_at  timestamptz NOT NULL DEFAULT now(),
  last_seen  timestamptz,                              -- refreshed while an events stream is open
  PRIMARY KEY (race_id, player_id)
);

CREATE TABLE IF NOT EXISTS race_rounds (
  race_id    uuid NOT NULL REFERENCES races(id) ON DELETE CASCADE,
  n          integer NOT NULL,                         -- 1-based
  word       char(5) NOT NULL,
  starts_at  timestamptz NOT NULL,
  ends_at    timestamptz NOT NULL,
  ended_at   timestamptz,
  PRIMARY KEY (race_id, n)
);

-- A row per player taking part in the round (created when the round starts).
CREATE TABLE IF NOT EXISTS race_games (
  race_id      uuid NOT NULL REFERENCES races(id) ON DELETE CASCADE,
  n            integer NOT NULL,
  player_id    uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  guesses      text[] NOT NULL DEFAULT '{}',
  finished_at  timestamptz,
  solved       boolean,
  PRIMARY KEY (race_id, n, player_id)
);
