# CLAUDE.md

## What this is

`index.html` is the whole project: a daily five-letter word game with a shared
leaderboard, published as a Claude Artifact at
https://claude.ai/artifact/LBx8ARu96TLfAJVGMrzEs9. There's no build step and no
dependencies other than Google Fonts: Big Shoulders Display for tiles and
headings, Figtree for body text, and JetBrains Mono for times and labels. Keep it
to a single static page.

Opened as a plain file or over localhost, the page runs in **solo mode**
(`window.claude` is absent), which means no leaderboard and stats in
`localStorage` only. That's expected. The leaderboard only works in the
published artifact.

To republish from a Claude session, call the Artifact tool with `url` set to the
link above. Omitting `capabilities` keeps the stored ones:

```js
{ user: { scopes: ["profile"] },
  db: { rules: [
    { path: "results", read: "view", write: "admin" },
    { path: "results/{self}", write: "interact" } ] } }
```

Because an artifact that declares `db` is org-internal, only signed-in members of
the owner's claude.ai org can play on the leaderboard.

The code also lives at https://github.com/amoeba11/wordle-arena, and GitHub Pages
serves it from `main` / root at https://amoeba11.github.io/wordle-arena/. Any
push to `main` deploys there. That copy always runs in solo mode (no
leaderboard). The Artifact doesn't update on push, so republish it separately
after changes.

## Three backends

`index.html` picks one at load time; the game core only calls `backend.guess(word)`:

- `artifactBackend` when `window.claude` exists (the Claude Artifact). It uses the artifact db, scores locally, and its leaderboard is org-only.
- `serverBackend` when `GET /api/game` answers with JSON. This is the public Replit app. The server holds the answers and scores guesses, and serves this same file with `ANSWERS_RAW` blanked, so players can't read the answer. The contract is in `server/SPEC.md`, the schema in `server/schema.sql`, and the generated `server/seed.sql` contains the answers and valid words.
- `soloBackend` everywhere else, such as GitHub Pages or a local file. There's no leaderboard, and stats stay in `localStorage`. Set `LIVE_URL` in the script to show a link to the public app.

To test the server mode locally, run `python3 server/devserver.py`, a stdlib + SQLite reference implementation of the spec, and open http://localhost:8787. `WA_DAY=n` fakes the day.

## Word lists

`ANSWERS_RAW` (space-separated) and `VALID_RAW` (concatenated 5-letter chunks, which include every
answer) are generated, along with `server/seed.sql`. Edit `tools/answers.txt`, then run `tools/build_words.sh`, which
rewrites those two lines in `index.html`. Valid guesses come from the macOS
`/usr/share/dict/words` list (Webster's 2nd, public domain) plus simple
inflections that list omits (plurals, -ed, -es). No NYT lists are used.

## Daily puzzle

- `EPOCH` (2026-09-28 UTC) is puzzle No. 1. The day rolls over at 00:00 UTC for
  everyone.
- `ANSWER = ANSWERS[ORDER[dayIndex % n]]`, where `ORDER` is a fixed seeded
  shuffle (`mulberry32(20260928)`).
- **Don't reorder or remove words in `answers.txt`, and don't change the seed or
  the epoch.** Any of these changes today's word mid-day for people already
  playing. Words added to `answers.txt` are sorted in, so they also reshuffle
  future days. That's fine for future days but will change today's word, so only
  add words right after 00:00 UTC, or accept the disruption.
- `score()` is the two-pass green-then-yellow algorithm, so repeated letters are
  handled correctly.

## Data (artifact db)

- `results/<uid>` is public, and each player writes only their own document.
  Shape: `{ lastDate, lastSolvedDate, today:{date,guesses,solved,ms}, played,
  wins, streak, maxStreak, dist[6], history{date:guesses|0} }`. `applyResult()`
  builds it, and history is capped at 90 days. Names are never stored; they're
  resolved with `user.profiles(ids)` at render time.
- `data/users/<uid>/progress` is private in-progress state
  (`{date, guesses[], startedAt, finishedAt}`), written on each guess. Reloading
  or switching devices resumes the same board.
- The page subscribes once to the whole `results` collection. The Today and
  All-time tables are both derived from that subscription on the client. The
  Today table stays hidden until you finish; only the count of finishers shows.
  All-time requires at least 3 games and ranks by win %, then average guesses.

## Known limits

- The answer is computable in the browser, so a determined player can cheat, and
  scores are self-reported. That's acceptable for friendly competition inside an
  org. Real enforcement would need a server that checks guesses.
- One artifact db holds at most 5,000 documents. At two documents per player,
  that's plenty.
