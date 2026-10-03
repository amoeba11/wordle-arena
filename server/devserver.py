"""Local reference server for Wordle Arena (Python 3 stdlib + SQLite).

Implements server/SPEC.md so the page's server mode can be tested without Node
or Postgres. Production runs on Replit (Node + Express + Postgres).

    python3 server/devserver.py            # http://localhost:8787
    WA_DAY=12 python3 server/devserver.py  # pretend it's day 12

Rate limits and the profanity filter are omitted here; the Secure cookie flag
is dropped because localhost is plain HTTP.
"""
import hashlib, http.cookies, json, os, re, secrets, sqlite3, time, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
EPOCH_MS = int(datetime(2026, 9, 28, tzinfo=timezone.utc).timestamp() * 1000)
DAY_MS = 86400000
NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{2,20}$")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"

db = sqlite3.connect(os.path.join(HERE, "dev.sqlite3"), check_same_thread=False, isolation_level=None)
db.executescript("""
CREATE TABLE IF NOT EXISTS answers (day_index INTEGER PRIMARY KEY, word TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS valid_words (word TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS players (id TEXT PRIMARY KEY, name TEXT NOT NULL, recovery_code_hash TEXT UNIQUE, created_at INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS players_name_ci ON players (lower(name));
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, player_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS games (player_id TEXT, day_index INTEGER, guesses TEXT NOT NULL DEFAULT '[]',
  started_at INTEGER, finished_at INTEGER, solved INTEGER, PRIMARY KEY (player_id, day_index));
""")
if db.execute("SELECT count(*) FROM answers").fetchone()[0] == 0:
    sql = open(os.path.join(HERE, "seed.sql")).read()
    sql = sql.replace("BEGIN;", "").replace("COMMIT;", "").replace("TRUNCATE answers;", "").replace("TRUNCATE valid_words;", "")
    db.executescript("BEGIN;" + sql + "COMMIT;")
N_ANSWERS = db.execute("SELECT count(*) FROM answers").fetchone()[0]


def sha(s): return hashlib.sha256(s.encode()).hexdigest()
def now_ms(): return int(time.time() * 1000)
def today():
    if os.environ.get("WA_DAY"): return int(os.environ["WA_DAY"])
    return (now_ms() - EPOCH_MS) // DAY_MS
def day_date(d): return datetime.fromtimestamp((EPOCH_MS + d * DAY_MS) / 1000, timezone.utc).strftime("%Y-%m-%d")
def answer_for(d): return db.execute("SELECT word FROM answers WHERE day_index=?", (d % N_ANSWERS,)).fetchone()[0]
def color(pid): return f"hsl({int.from_bytes(hashlib.sha256(pid.encode()).digest()[:4], 'big') % 360} 55% 50%)"
def new_code():
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}", sha(raw)


def score(guess, answer):
    res, left = ["absent"] * 5, {}
    for i in range(5):
        if guess[i] == answer[i]: res[i] = "correct"
        else: left[answer[i]] = left.get(answer[i], 0) + 1
    for i in range(5):
        if res[i] != "correct" and left.get(guess[i], 0) > 0:
            res[i] = "present"; left[guess[i]] -= 1
    return res


def streak_of(solved_days, d):
    s = set(solved_days); start = d if d in s else d - 1; n = 0
    while start in s: n += 1; start -= 1
    return n


def stats(pid, d):
    rows = db.execute("SELECT day_index, guesses, started_at, finished_at, solved FROM games WHERE player_id=? AND finished_at IS NOT NULL ORDER BY day_index", (pid,)).fetchall()
    dist = [0] * 6; solved_days = []; best = run = 0; prev = None; today_row = None
    for day, g, st, fin, sv in rows:
        n = len(json.loads(g))
        if sv:
            dist[n - 1] += 1; solved_days.append(day)
            run = run + 1 if prev == day - 1 else 1; prev = day; best = max(best, run)
        else: run = 0; prev = None
        if day == d: today_row = {"date": day_date(day), "guesses": n, "solved": bool(sv), "ms": fin - st}
    wins = len(solved_days)
    return {"played": len(rows), "wins": wins, "streak": streak_of(solved_days, d), "maxStreak": best, "dist": dist,
            "today": today_row, "lastDate": day_date(rows[-1][0]) if rows else None,
            "lastSolvedDate": day_date(solved_days[-1]) if solved_days else None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, status, body, cookie=None, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        if cookie: self.send_header("Set-Cookie", f"wa_token={cookie}; HttpOnly; SameSite=Lax; Path=/; Max-Age=34560000")
        self.end_headers(); self.wfile.write(data)

    def err(self, status, msg): self.send(status, {"error": msg})

    def player(self):
        c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if "wa_token" not in c: return None
        row = db.execute("SELECT p.id, p.name FROM sessions s JOIN players p ON p.id=s.player_id WHERE s.token_hash=?", (sha(c["wa_token"].value),)).fetchone()
        return row

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try: return json.loads(self.rfile.read(n) or b"{}")
        except Exception: return {}

    def new_session(self, pid):
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO sessions VALUES (?,?)", (sha(token), pid))
        return token

    def game_row(self, pid, d):
        return db.execute("SELECT guesses, started_at, finished_at, solved FROM games WHERE player_id=? AND day_index=?", (pid, d)).fetchone()

    def do_GET(self):
        d = today(); p = self.player()
        if self.path in ("/", "/index.html"):
            html = open(os.path.join(HERE, "..", "index.html"), encoding="utf-8").read()
            html = re.sub(r'^const ANSWERS_RAW = ".*";$', 'const ANSWERS_RAW = "";', html, count=1, flags=re.M)
            return self.send(200, html.encode(), ctype="text/html; charset=utf-8")
        if self.path == "/api/game":
            out = {"day": d, "puzzle": d + 1, "player": {"name": p[1]} if p else None, "guesses": [], "patterns": [],
                   "startedAt": None, "finishedAt": None, "solved": None}
            if p and (g := self.game_row(p[0], d)):
                ans = answer_for(d); words = json.loads(g[0])
                out.update(guesses=words, patterns=[score(w, ans) for w in words], startedAt=g[1], finishedAt=g[2],
                           solved=None if g[3] is None else bool(g[3]))
                if g[2]: out["answer"] = ans
            return self.send(200, out)
        if self.path == "/api/me":
            if not p: return self.err(401, "Pick a nickname first.")
            return self.send(200, stats(p[0], d))
        if self.path == "/api/standings":
            fin = db.execute("SELECT g.player_id, p.name, g.guesses, g.started_at, g.finished_at, g.solved FROM games g JOIN players p ON p.id=g.player_id WHERE g.day_index=? AND g.finished_at IS NOT NULL", (d,)).fetchall()
            mine = p and any(r[0] == p[0] for r in fin)
            today_rows = None
            if mine:
                today_rows = sorted(({"id": r[0], "name": r[1], "color": color(r[0]), "solved": bool(r[5]), "guesses": len(json.loads(r[2])),
                                      "ms": r[4] - r[3], "me": r[0] == p[0]} for r in fin),
                                    key=lambda x: (not x["solved"], x["guesses"], x["ms"]))[:100]
            all_rows = []
            for pid, name in db.execute("SELECT id, name FROM players").fetchall():
                s = stats(pid, d)
                if s["played"] < 3: continue
                avg = sum(n * (i + 1) for i, n in enumerate(s["dist"])) / s["wins"] if s["wins"] else None
                all_rows.append({"id": pid, "name": name, "color": color(pid), "played": s["played"], "winPct": 100 * s["wins"] / s["played"],
                                 "avg": avg, "streak": s["streak"], "me": bool(p and pid == p[0])})
            all_rows.sort(key=lambda x: (-x["winPct"], x["avg"] if x["avg"] is not None else 99, -x["played"]))
            return self.send(200, {"count": len(fin), "today": today_rows, "all": all_rows[:100]})
        self.err(404, "Not found.")

    def do_POST(self):
        d = today(); p = self.player(); b = self.body()
        if self.path == "/api/player":
            if p: return self.err(409, "You already have a nickname.")
            name = str(b.get("name", "")).strip()
            if not NAME_RE.match(name): return self.err(400, "Nicknames are 2–20 letters, numbers, spaces, - or _.")
            if db.execute("SELECT 1 FROM players WHERE lower(name)=lower(?)", (name,)).fetchone():
                return self.err(409, "That nickname is taken. Try another.")
            pid = str(uuid.uuid4()); code, code_hash = new_code()
            db.execute("INSERT INTO players VALUES (?,?,?,?)", (pid, name, code_hash, now_ms()))
            return self.send(200, {"name": name, "recoveryCode": code}, cookie=self.new_session(pid))
        if self.path == "/api/recover":
            raw = re.sub(r"[^A-Z0-9]", "", str(b.get("code", "")).upper())
            row = db.execute("SELECT id, name FROM players WHERE recovery_code_hash=?", (sha(raw),)).fetchone()
            if not row: return self.err(404, "That code doesn't match any player.")
            return self.send(200, {"name": row[1]}, cookie=self.new_session(row[0]))
        if self.path == "/api/recovery-code":
            if not p: return self.err(401, "Pick a nickname first.")
            code, code_hash = new_code()
            db.execute("UPDATE players SET recovery_code_hash=? WHERE id=?", (code_hash, p[0]))
            return self.send(200, {"recoveryCode": code})
        if self.path == "/api/guess":
            if not p: return self.err(401, "Pick a nickname first.")
            w = str(b.get("word", "")).lower()
            if len(w) < 5: return self.err(400, "Not enough letters.")
            if len(w) != 5 or not db.execute("SELECT 1 FROM valid_words WHERE word=?", (w,)).fetchone():
                return self.err(400, "Not in word list.")
            db.execute("BEGIN IMMEDIATE")
            try:
                g = self.game_row(p[0], d)
                words = json.loads(g[0]) if g else []
                if g and g[2]: db.execute("ROLLBACK"); return self.err(409, "You've already finished today's puzzle.")
                started = (g[1] if g else None) or now_ms()
                words.append(w); ans = answer_for(d)
                solved = w == ans; finished = solved or len(words) == 6
                fin = now_ms() if finished else None
                db.execute("INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?)",
                           (p[0], d, json.dumps(words), started, fin, (1 if solved else 0) if finished else None))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK"); raise
            out = {"pattern": score(w, ans), "finished": finished, "solved": solved if finished else None, "startedAt": started, "finishedAt": fin}
            if finished: out["answer"] = ans
            return self.send(200, out)
        self.err(404, "Not found.")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8787))
    print(f"Wordle Arena dev server on http://localhost:{port} (day {today()}, answer list {N_ANSWERS})")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
