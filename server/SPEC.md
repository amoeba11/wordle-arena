# Wordle Arena server: build spec

This is the contract between the server and the existing page `index.html`. The page's `serverBackend` relies on these exact paths, fields and status codes.
`devserver.py` in this folder is a working reference implementation (Python + SQLite). It's for local testing; production is Node + Express + PostgreSQL on Replit.

## Stack
- Node 20, Express, `pg` (Replit PostgreSQL via `DATABASE_URL`).
- On boot, run `schema.sql`, then `seed.sql` if `answers` is empty. Both files are in this folder.
- No frontend build. The server serves the page from the "Serving the page" section below.

## The day
- `day = floor((now_utc - 2026-09-28T00:00Z) / 86400000)` and `puzzle = day + 1`.
- `answer = answers.word WHERE day_index = day mod (SELECT count(*) FROM answers)`.
- Use the server clock only.

## Players and cookies
- Cookie `wa_token`: 32 random bytes in base64url, with `HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=34560000` (400 days).
  - Store only `sha256(token)` in `sessions`.
  - The current player is the one whose session matches the cookie. If there's no match, there's no player.
- Recovery code: 12 characters from `ABCDEFGHJKMNPQRSTVWXYZ23456789`, shown as `XXXX-XXXX-XXXX`.
  - Store `sha256` of the uppercased code with the dashes removed, in `players.recovery_code_hash`.
  - The plain code is returned once and never stored.
- Nickname:
  - Trim it, then require `^[A-Za-z0-9 _-]{2,20}$`. It must be unique ignoring case.
  - Reject offensive names with a profanity filter (e.g. the `bad-words` npm package).
- `color` for a player is `hsl(H 55% 50%)`, where `H` = (first 4 bytes of sha256(player id)) mod 360.

## Scoring (must match the page)
Two passes:
1. Mark exact matches `correct` and count the remaining answer letters.
2. Mark a letter `present` while its remaining count is above 0, decrementing the count; otherwise mark it `absent`.

The pattern is an array of 5 strings, each `"correct"`, `"present"` or `"absent"`.

## Endpoints
All endpoints use JSON. Every error response is `{ "error": "<sentence shown to the player>" }` with the status listed below.
Times are **milliseconds since the epoch** (numbers), not ISO strings.

### `GET /api/game`
Always 200:
```json
{ "day": 5, "puzzle": 6, "player": { "name": "quietfox" } | null,
  "guesses": ["crane"], "patterns": [["absent","present","correct","absent","absent"]],
  "startedAt": 1759480000000 | null, "finishedAt": null, "solved": null,
  "answer": "major" }
```
- `answer` is present **only** when today's game for this player is finished.
- With no player, `guesses` and `patterns` are `[]`.

### `POST /api/player` `{ "name": "quietfox" }`
- **200:** `{ "name", "recoveryCode" }`, plus a new player, session and cookie.
- **400** if the name is invalid: "Nicknames are 2–20 letters, numbers, spaces, - or _."
- **400** if the profanity filter rejects it: "Please pick a different nickname."
- **409** if the name is taken: "That nickname is taken. Try another."
- **409** if this browser already has a player: "You already have a nickname."

### `POST /api/recover` `{ "code": "ABCD-EFGH-JKMN" }`
- **200:** `{ "name" }`, plus a new session and cookie for that player. Any other sessions stay valid.
- **404** if no player matches: "That code doesn't match any player."

### `POST /api/recovery-code` `{}`
- **200:** `{ "recoveryCode" }`. The new code replaces the old one.
- **401** if there's no player: "Pick a nickname first."

### `POST /api/guess` `{ "word": "crane" }`
Run this in one transaction, locking the game row with `SELECT … FOR UPDATE` so a double submit can't add two guesses.

- **401** if there's no player: "Pick a nickname first."
- **400** if the word is shorter than 5 letters: "Not enough letters."
- **400** if it's not in `valid_words` (lowercase it first): "Not in word list."
- **409** if today's game is already finished: "You've already finished today's puzzle."

Then:
- Set `started_at = now()` on the first guess and append the word.
- The game is finished when the word equals the answer or there are 6 guesses. In that case set `finished_at = now()` and `solved`.

Response:
- **200:** `{ "pattern", "finished", "solved", "startedAt", "finishedAt", "answer"? }`. `answer` is present only when `finished`.

### `GET /api/standings`
Response:
- **200:** `{ "count", "today", "all" }`.

`count`
- The number of finished games for today.

`today`
- **`null` unless the caller has finished today's game.** This is the rule that stops people peeking before they play.
- Otherwise it lists today's finished games, ordered by: solved first, then fewest guesses, then shortest `finished_at - started_at`.
- Each row is `{ "id", "name", "color", "solved", "guesses", "ms", "me" }`, where `guesses` is a count and `id` is the player uuid.
- Limit 100 rows, and always include the caller's own row.

`all`
- Players with at least 3 finished games, ordered by `winPct` descending, then `avg` ascending (nulls last), then `played` descending. Limit 100.
- Each row is `{ "id", "name", "color", "played", "winPct", "avg", "streak", "me" }`:
  - `winPct` runs from 0 to 100.
  - `avg` is the mean guesses over solved games, or null.
  - `streak` counts consecutive solved days ending today or yesterday.

### `GET /api/me`
- **200:**
  ```json
  { "played", "wins", "streak", "maxStreak", "dist": [6 counts],
    "today": { "date": "YYYY-MM-DD", "guesses", "solved", "ms" } | null,
    "lastDate", "lastSolvedDate" }
  ```
  Dates are UTC `YYYY-MM-DD` strings for day indexes, and `today` is set only if today's game is finished.
- **401** if there's no player.

## Rate limits (`express-rate-limit`, in-memory is fine)
- 120 requests/min per IP across `/api/*`.
- `POST /api/player`: 5/hour per IP.
- `POST /api/recover`: 10/hour per IP.
- `POST /api/guess`: 30/min per player.

## Serving the page
- At startup, download `https://raw.githubusercontent.com/amoeba11/wordle-arena/main/index.html` and keep it in memory. Fall back to `public/index.html` if the download fails, and commit a copy there.
- Serve it at `GET /`, `GET /c/:code` and `GET /r/:code` with `Content-Type: text/html; charset=utf-8` and `Cache-Control: no-store`.
- **Before serving, replace the line that starts with `const ANSWERS_RAW = "`** with `const ANSWERS_RAW = "";`. This removes the answers from the page.
- Re-download every 10 minutes, so pushes to GitHub show up without a redeploy.
- Set `app.set("trust proxy", 1)` so rate limits see real client IPs on Replit.

---

# Group competitions: contests and live races

`devserver.py` implements everything below. When anything is unclear, match its behavior.

## Shared rules
- **Pages.** Serve the same sanitized page (answers stripped) at `GET /c/:code` and `GET /r/:code` as well as `/`. Codes are 6 characters from `ABCDEFGHJKMNPQRSTVWXYZ23456789`. The page picks its view from the path.
- **Points.** `points = solved ? 7 − guesses : 0`. Ties are broken by total solve time over solved puzzles (lower is better).
- **One scoring path.**
  - Contest and race guesses use the same validation as `/api/guess`, with the same 400 messages.
  - They use the same two-pass scoring, and finish on a solve or on the 6th guess.
  - They run in one transaction with `SELECT … FOR UPDATE` on the game row.
  - They return the same shape as `/api/guess`: `{ pattern, finished, solved, startedAt, finishedAt, answer? }`. For a race, `startedAt` is the round's `startsAt`.
- **Word choice.**
  - Contest words are picked when the contest is created; a race word is picked when its round starts.
  - Picks are random, using a cryptographic RNG.
  - Exclude any answer whose `day_index` is `(today + k) mod count` for `k` in −30…400, so a competition never spoils a recent or upcoming daily word.
  - A contest never repeats a word, and a room never repeats one across rounds.
  - **Never send a contest or race word to a browser** before that player's puzzle is finished (contest) or before the round has ended (race).
- **Players.** All `POST` endpoints require a player and return **401** "Pick a nickname first." otherwise. The `GET` views work without one.
- **Names.** Contest and room names are trimmed, 1–40 characters, with no control characters and no `<` or `>`. Otherwise return **400** "Give the contest a name of up to 40 characters." or "Give the room a name of up to 40 characters." Run them through the same profanity filter as nicknames.
- **Rate limits.**
  - creating contests or rooms: 10 per hour per player
  - joining: 30 per hour per player
  - race and contest guesses share the existing 30 per minute per player guess limit

## Contests

### `POST /api/contests` `{ "name", "startDate": "YYYY-MM-DD", "days": 1..14 }`
- **400** "Pick a start date between today and 30 days from now." The start date must be today through today + 30 (UTC).
- **400** "Contests run for 1 to 14 days."
- **409** "You can host up to 5 contests at a time." This counts contests where `start_day + days > today`.
- **200** `{ "code" }`. The host becomes a member automatically.

### `GET /api/contests/:code`
- **404** "This contest link doesn't exist."
- **200:**
```json
{ "code", "name", "host": "<host nickname>", "isHost", "startDate": "YYYY-MM-DD", "days",
  "day": today - start_day, "status": "upcoming" | "live" | "over", "member",
  "now": <server ms>, "nextUnlock": <ms of next 00:00 UTC>,
  "game": null | { "n", "guesses", "patterns", "startedAt", "finishedAt", "solved", "answer"? },
  "rows": [ { "id", "name", "color", "points", "ms", "days": [ points | 0 | null ], "me" } ] }
```

`game` is non-null only for a member while the contest is `live`. It holds the viewer's own board for today's puzzle, and `answer` is present only once that game is finished.

`rows` lists every member, sorted by points descending, then `ms` ascending, then name. Each row's `days` has one entry per contest day:
- `null` for a future day, or for every day while the contest is `upcoming`
- `null` for **today**, for other members, until the viewer has finished today's puzzle; this hides today's results
- `null` for today while that member hasn't finished it yet
- the points earned (0 for a miss) once that member has finished that day
- `0` for a past day the member never finished (a missed day)

`points` and `ms` are summed only over the entries the viewer can see, so the totals never reveal today's hidden results.

### Joining, leaving and guessing
- **`POST /api/contests/:code/join`:**
  - **409** "This contest is over."
  - **409** "This contest is full." at 200 members.
  - **200** `{ "ok": true }`. Joining again is a no-op.
- **`POST /api/contests/:code/leave`:**
  - **409** "The host can't leave their own contest."
  - **200** `{ "ok": true }`.
- **`POST /api/contests/:code/guess` `{ "word" }`:**
  - **403** "Join this contest to play."
  - **409** "This contest hasn't started yet." or "This contest is over."
  - **409** "You've already finished today's puzzle."
  - Otherwise it plays puzzle `n = today − start_day`. `started_at` is set on the first guess.

## Live races

### `POST /api/races` `{ "name" }`
**200** `{ "code" }`. The host becomes a member.

### `GET /api/races/:code`
Before building the response, check whether the current round should end (see below).
- **404** "This race room doesn't exist or has expired."
- **200:**
```json
{ "code", "name", "isHost", "member", "now": <server ms>,
  "members": [ { "id", "name", "color", "online", "me", "host", "points", "ms" } ],
  "round": null | { "n", "startsAt", "endsAt", "ended",
    "players": [ { "id", "name", "color", "patterns", "finished", "solved", "ms", "points", "me" } ],
    "mine": null | { "guesses", "patterns", "finished", "solved", "finishedAt" },
    "answer"? } }
```

`members` are sorted by `points` descending, then `ms` ascending. `points` and `ms` total **ended** rounds only.

`round` is the latest round.
- `players` lists only the people taking part in it, sorted with finished players first, then by points descending, then by `ms` ascending. **Their guesses are never included, only `patterns`.** For a finished player, `ms = finished_at − starts_at`.
- `mine` is the viewer's own entry, including their guesses, or `null` if they're only watching.
- `answer` is present only when `ended`.

`online` is true while the member has an open events stream on any instance, or their `last_seen` is within the last 45 seconds.

### `POST /api/races/:code/join`
**200** `{ "ok": true }`. Joining again is a no-op. Notifies the room.

### `POST /api/races/:code/start` (host only)
- **403** "Join this room first."
- **403** "Only the host can start a round."
- **409** "A round is already running."
- **200** `{ "n", "startsAt" }`.

It creates round `n` = last + 1 with:
- `starts_at = now + 3 s`
- `ends_at = starts_at + 5 min`
- a new word

It also creates a `race_games` row for every member who is online at that moment, plus the host. Those people are the round's players; anyone else watches. Notifies the room.

### `POST /api/races/:code/guess` `{ "word" }`
- **403** "Join this room first."
- **409** "The round hasn't started yet." This applies when there's no round, or before `starts_at`.
- **409** "This round is over."
- **403** "Wait for the next round to join in." This applies when the player isn't one of the round's players.
- **409** "You've finished this round."

Otherwise it plays the guess, checks whether the round should end, and notifies the room.

**Ending a round.** A round ends once `ended_at IS NULL` and either:
- every row in `race_games` for it is finished, or
- `now ≥ ends_at`.

To end it, set `ended_at = least(now, ends_at)` and notify. Check this on every room `GET`, on every guess, and from a timer on each instance (every 5 s is fine).

**Activity and expiry.** Every `POST` to a room updates `races.last_active_at`. Delete rooms idle for 24 hours when the server starts and once an hour; cascades remove the rest.

### `GET /api/races/:code/events` (Server-Sent Events)
- Send the headers `Content-Type: text/event-stream`, `Cache-Control: no-store` and `X-Accel-Buffering: no`, and flush them immediately.
- Send `event: changed\ndata: 1\n\n` straight away, and again whenever the room is notified.
- Send a `: ping` comment every 20 s.
- While the stream is open for a signed-in member, update their `last_seen` every 30 s.
- When it opens and when it closes, notify the room so others see who's online. On close, clear that member's `last_seen`.
- The browser re-fetches the `GET` snapshot on every `changed` event, so the event carries no data.

### Notifying across instances
Replit Autoscale may run several instances, so the server must use Postgres **`LISTEN/NOTIFY`**:
- "Notify the room" means `SELECT pg_notify('race_events', <race id>)` (inside the transaction where there is one).
- Each instance keeps **one dedicated** `pg` client that runs `LISTEN race_events` (not from the pool). It reconnects with backoff if that client drops.
- On each notification, the instance writes `changed` to its own open streams for that race.
