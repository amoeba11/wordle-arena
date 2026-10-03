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
- Serve it at `GET /` with `Content-Type: text/html; charset=utf-8` and `Cache-Control: no-store`.
- **Before serving, replace the line that starts with `const ANSWERS_RAW = "`** with `const ANSWERS_RAW = "";`. This removes the answers from the page.
- Re-download every 10 minutes, so pushes to GitHub show up without a redeploy.
- Set `app.set("trust proxy", 1)` so rate limits see real client IPs on Replit.
