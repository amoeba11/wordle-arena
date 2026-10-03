"""Local reference server for Wordle Arena (Python 3 stdlib + SQLite).

Implements server/SPEC.md so the page's server mode can be tested without Node
or Postgres. Production runs on Replit (Node + Express + Postgres).

    python3 server/devserver.py            # http://localhost:8787
    WA_DAY=12 python3 server/devserver.py  # pretend it's day 12
    WA_ROUND_MS=20000 ...                  # shorter race rounds for testing

Dev-only conveniences (not part of the spec): an `X-WA-Day: <n>` request header
overrides the day, so contest days can be stepped through without a restart.
Rate limits and the profanity filter are omitted; the Secure cookie flag is
dropped because localhost is plain HTTP; in-process pub/sub stands in for
Postgres LISTEN/NOTIFY.
"""
import hashlib, http.cookies, json, os, queue, random, re, secrets, sqlite3, threading, time, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
EPOCH_MS = int(datetime(2026, 9, 28, tzinfo=timezone.utc).timestamp() * 1000)
DAY_MS = 86400000
NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{2,20}$")
TITLE_RE = re.compile(r"^[^\x00-\x1f<>]{1,40}$")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
COUNTDOWN_MS = 3000
ROUND_MS = int(os.environ.get("WA_ROUND_MS", 300000))
ONLINE_MS = 45000

LOCK = threading.RLock()
db = sqlite3.connect(os.path.join(HERE, "dev.sqlite3"), check_same_thread=False, isolation_level=None)
db.executescript("""
CREATE TABLE IF NOT EXISTS answers (day_index INTEGER PRIMARY KEY, word TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS valid_words (word TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS players (id TEXT PRIMARY KEY, name TEXT NOT NULL, recovery_code_hash TEXT UNIQUE, created_at INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS players_name_ci ON players (lower(name));
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, player_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS games (player_id TEXT, day_index INTEGER, guesses TEXT NOT NULL DEFAULT '[]',
  started_at INTEGER, finished_at INTEGER, solved INTEGER, PRIMARY KEY (player_id, day_index));

CREATE TABLE IF NOT EXISTS contests (id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, host_id TEXT NOT NULL,
  start_day INTEGER NOT NULL, days INTEGER NOT NULL, created_at INTEGER);
CREATE TABLE IF NOT EXISTS contest_members (contest_id TEXT, player_id TEXT, joined_at INTEGER, PRIMARY KEY (contest_id, player_id));
CREATE TABLE IF NOT EXISTS contest_puzzles (contest_id TEXT, n INTEGER, word TEXT NOT NULL, PRIMARY KEY (contest_id, n));
CREATE TABLE IF NOT EXISTS contest_games (contest_id TEXT, n INTEGER, player_id TEXT, guesses TEXT NOT NULL DEFAULT '[]',
  started_at INTEGER, finished_at INTEGER, solved INTEGER, PRIMARY KEY (contest_id, n, player_id));

CREATE TABLE IF NOT EXISTS races (id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, host_id TEXT NOT NULL,
  created_at INTEGER, last_active_at INTEGER);
CREATE TABLE IF NOT EXISTS race_members (race_id TEXT, player_id TEXT, joined_at INTEGER, last_seen INTEGER, PRIMARY KEY (race_id, player_id));
CREATE TABLE IF NOT EXISTS race_rounds (race_id TEXT, n INTEGER, word TEXT NOT NULL, starts_at INTEGER, ends_at INTEGER, ended_at INTEGER,
  PRIMARY KEY (race_id, n));
CREATE TABLE IF NOT EXISTS race_games (race_id TEXT, n INTEGER, player_id TEXT, guesses TEXT NOT NULL DEFAULT '[]',
  finished_at INTEGER, solved INTEGER, PRIMARY KEY (race_id, n, player_id));
""")
if db.execute("SELECT count(*) FROM answers").fetchone()[0] == 0:
    sql = open(os.path.join(HERE, "seed.sql")).read()
    sql = sql.replace("BEGIN;", "").replace("COMMIT;", "").replace("TRUNCATE answers;", "").replace("TRUNCATE valid_words;", "")
    db.executescript("BEGIN;" + sql + "COMMIT;")
N_ANSWERS = db.execute("SELECT count(*) FROM answers").fetchone()[0]


def sha(s): return hashlib.sha256(s.encode()).hexdigest()
def now_ms(): return int(time.time() * 1000)
def env_day():
    if os.environ.get("WA_DAY"): return int(os.environ["WA_DAY"])
    return (now_ms() - EPOCH_MS) // DAY_MS
def day_date(d): return datetime.fromtimestamp((EPOCH_MS + d * DAY_MS) / 1000, timezone.utc).strftime("%Y-%m-%d")
def date_day(s):
    try: return (int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000) - EPOCH_MS) // DAY_MS
    except (TypeError, ValueError): return None
def answer_for(d): return db.execute("SELECT word FROM answers WHERE day_index=?", (d % N_ANSWERS,)).fetchone()[0]
def color(pid): return f"hsl({int.from_bytes(hashlib.sha256(pid.encode()).digest()[:4], 'big') % 360} 55% 50%)"
def new_code():
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}", sha(raw)
def invite_code(): return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
def points(solved, n): return 7 - n if solved else 0


def score(guess, answer):
    res, left = ["absent"] * 5, {}
    for i in range(5):
        if guess[i] == answer[i]: res[i] = "correct"
        else: left[answer[i]] = left.get(answer[i], 0) + 1
    for i in range(5):
        if res[i] != "correct" and left.get(guess[i], 0) > 0:
            res[i] = "present"; left[guess[i]] -= 1
    return res


def check_word(w):
    """Shared validation for every mode. Returns an error message or None."""
    if len(w) < 5: return "Not enough letters."
    if len(w) != 5 or not db.execute("SELECT 1 FROM valid_words WHERE word=?", (w,)).fetchone(): return "Not in word list."
    return None


def advance(words, w, ans):
    """Shared scoring step: append the guess and report (words, pattern, solved, finished)."""
    words = words + [w]; solved = w == ans
    return words, score(w, ans), solved, solved or len(words) == 6


def special_words(d, k, exclude=()):
    """k random answers that aren't the daily word from 30 days back to 400 days ahead, nor in `exclude`."""
    blocked = {(d + off) % N_ANSWERS for off in range(-30, 401)}
    pool = [w for i, w in db.execute("SELECT day_index, word FROM answers").fetchall() if i not in blocked and w not in exclude]
    return random.SystemRandom().sample(pool, k)


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


# ---------- race events: in-process stand-in for LISTEN/NOTIFY ----------
subscribers = {}          # race_id -> set of (queue, player_id)
def notify(race_id):
    for q, _ in list(subscribers.get(race_id, ())): q.put("changed")
def online_ids(race_id):
    live = {pid for _, pid in subscribers.get(race_id, ()) if pid}
    cutoff = now_ms() - ONLINE_MS
    live |= {r[0] for r in db.execute("SELECT player_id FROM race_members WHERE race_id=? AND last_seen>?", (race_id, cutoff))}
    return live


def end_round_if_due(race_id):
    r = db.execute("SELECT n, ends_at, ended_at FROM race_rounds WHERE race_id=? ORDER BY n DESC LIMIT 1", (race_id,)).fetchone()
    if not r or r[2]: return
    open_games = db.execute("SELECT count(*) FROM race_games WHERE race_id=? AND n=? AND finished_at IS NULL", (race_id, r[0])).fetchone()[0]
    if now_ms() >= r[1] or open_games == 0:
        db.execute("UPDATE race_rounds SET ended_at=? WHERE race_id=? AND n=?", (min(now_ms(), r[1]), race_id, r[0]))
        notify(race_id)


def round_ticker():
    while True:
        time.sleep(1)
        with LOCK:
            for (rid,) in db.execute("SELECT race_id FROM race_rounds WHERE ended_at IS NULL").fetchall(): end_round_if_due(rid)
            db.execute("DELETE FROM races WHERE last_active_at < ?", (now_ms() - DAY_MS,))


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def send(self, status, body, cookie=None, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie: self.send_header("Set-Cookie", f"wa_token={cookie}; HttpOnly; SameSite=Lax; Path=/; Max-Age=34560000")
        self.end_headers(); self.wfile.write(data)

    def err(self, status, msg): self.send(status, {"error": msg})

    def today(self):
        h = self.headers.get("X-WA-Day")
        return int(h) if h and h.lstrip("-").isdigit() else env_day()

    def player(self):
        c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if "wa_token" not in c: return None
        return db.execute("SELECT p.id, p.name FROM sessions s JOIN players p ON p.id=s.player_id WHERE s.token_hash=?", (sha(c["wa_token"].value),)).fetchone()

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

    def page(self):
        html = open(os.path.join(HERE, "..", "index.html"), encoding="utf-8").read()
        html = re.sub(r'^const ANSWERS_RAW = ".*";$', 'const ANSWERS_RAW = "";', html, count=1, flags=re.M)
        return self.send(200, html.encode(), ctype="text/html; charset=utf-8")

    # ---------------- routing ----------------
    def do_GET(self):
        path = self.path.split("?")[0]
        if re.fullmatch(r"/r/[A-Z0-9]{6}/?|/c/[A-Z0-9]{6}/?|/|/index\.html", path): return self.page()
        m = re.fullmatch(r"/api/races/([A-Z0-9]{6})/events", path)
        if m: return self.race_events(m.group(1))
        with LOCK: return self.get_api(path)

    def do_POST(self):
        with LOCK: return self.post_api(self.path.split("?")[0])

    def get_api(self, path):
        d = self.today(); p = self.player()
        if path == "/api/game":
            out = {"day": d, "puzzle": d + 1, "player": {"name": p[1]} if p else None, "guesses": [], "patterns": [],
                   "startedAt": None, "finishedAt": None, "solved": None}
            if p and (g := self.game_row(p[0], d)):
                ans = answer_for(d); words = json.loads(g[0])
                out.update(guesses=words, patterns=[score(w, ans) for w in words], startedAt=g[1], finishedAt=g[2],
                           solved=None if g[3] is None else bool(g[3]))
                if g[2]: out["answer"] = ans
            return self.send(200, out)
        if path == "/api/me":
            if not p: return self.err(401, "Pick a nickname first.")
            return self.send(200, stats(p[0], d))
        if path == "/api/standings": return self.daily_standings(p, d)
        m = re.fullmatch(r"/api/contests/([A-Z0-9]{6})", path)
        if m: return self.contest_view(m.group(1), p, d)
        m = re.fullmatch(r"/api/races/([A-Z0-9]{6})", path)
        if m: return self.race_view(m.group(1), p)
        self.err(404, "Not found.")

    def post_api(self, path):
        d = self.today(); p = self.player(); b = self.body()
        if path == "/api/player":
            if p: return self.err(409, "You already have a nickname.")
            name = str(b.get("name", "")).strip()
            if not NAME_RE.match(name): return self.err(400, "Nicknames are 2–20 letters, numbers, spaces, - or _.")
            if db.execute("SELECT 1 FROM players WHERE lower(name)=lower(?)", (name,)).fetchone():
                return self.err(409, "That nickname is taken. Try another.")
            pid = str(uuid.uuid4()); code, code_hash = new_code()
            db.execute("INSERT INTO players VALUES (?,?,?,?)", (pid, name, code_hash, now_ms()))
            return self.send(200, {"name": name, "recoveryCode": code}, cookie=self.new_session(pid))
        if path == "/api/recover":
            raw = re.sub(r"[^A-Z0-9]", "", str(b.get("code", "")).upper())
            row = db.execute("SELECT id, name FROM players WHERE recovery_code_hash=?", (sha(raw),)).fetchone()
            if not row: return self.err(404, "That code doesn't match any player.")
            return self.send(200, {"name": row[1]}, cookie=self.new_session(row[0]))
        if not p: return self.err(401, "Pick a nickname first.")
        if path == "/api/recovery-code":
            code, code_hash = new_code()
            db.execute("UPDATE players SET recovery_code_hash=? WHERE id=?", (code_hash, p[0]))
            return self.send(200, {"recoveryCode": code})
        if path == "/api/guess": return self.daily_guess(p, d, str(b.get("word", "")).lower())
        if path == "/api/contests": return self.contest_create(p, d, b)
        if path == "/api/races": return self.race_create(p, b)
        m = re.fullmatch(r"/api/contests/([A-Z0-9]{6})/(join|leave|guess)", path)
        if m: return self.contest_action(m.group(1), m.group(2), p, d, b)
        m = re.fullmatch(r"/api/races/([A-Z0-9]{6})/(join|start|guess)", path)
        if m: return self.race_action(m.group(1), m.group(2), p, b)
        self.err(404, "Not found.")

    # ---------------- daily ----------------
    def daily_guess(self, p, d, w):
        if (e := check_word(w)): return self.err(400, e)
        g = self.game_row(p[0], d)
        if g and g[2]: return self.err(409, "You've already finished today's puzzle.")
        started = (g[1] if g else None) or now_ms()
        words, pattern, solved, finished = advance(json.loads(g[0]) if g else [], w, answer_for(d))
        fin = now_ms() if finished else None
        db.execute("INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?)", (p[0], d, json.dumps(words), started, fin, (1 if solved else 0) if finished else None))
        out = {"pattern": pattern, "finished": finished, "solved": solved if finished else None, "startedAt": started, "finishedAt": fin}
        if finished: out["answer"] = answer_for(d)
        return self.send(200, out)

    def daily_standings(self, p, d):
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

    # ---------------- contests ----------------
    def contest_create(self, p, d, b):
        name = str(b.get("name", "")).strip()
        if not TITLE_RE.match(name): return self.err(400, "Give the contest a name of up to 40 characters.")
        start = date_day(b.get("startDate"))
        if start is None or start < d or start > d + 30: return self.err(400, "Pick a start date between today and 30 days from now.")
        days = b.get("days")
        if not isinstance(days, int) or not 1 <= days <= 14: return self.err(400, "Contests run for 1 to 14 days.")
        open_count = db.execute("SELECT count(*) FROM contests WHERE host_id=? AND start_day+days>?", (p[0], d)).fetchone()[0]
        if open_count >= 5: return self.err(409, "You can host up to 5 contests at a time.")
        cid, code = str(uuid.uuid4()), invite_code()
        db.execute("INSERT INTO contests VALUES (?,?,?,?,?,?,?)", (cid, code, name, p[0], start, days, now_ms()))
        db.executemany("INSERT INTO contest_puzzles VALUES (?,?,?)", [(cid, i, w) for i, w in enumerate(special_words(d, days))])
        db.execute("INSERT INTO contest_members VALUES (?,?,?)", (cid, p[0], now_ms()))
        return self.send(200, {"code": code})

    def contest_row(self, code):
        return db.execute("SELECT id, name, host_id, start_day, days FROM contests WHERE code=?", (code,)).fetchone()

    def contest_view(self, code, p, d):
        c = self.contest_row(code)
        if not c: return self.err(404, "This contest link doesn't exist.")
        cid, name, host_id, start, days = c
        idx = d - start
        status = "upcoming" if idx < 0 else "over" if idx >= days else "live"
        pid = p[0] if p else None
        member = bool(pid and db.execute("SELECT 1 FROM contest_members WHERE contest_id=? AND player_id=?", (cid, pid)).fetchone())
        host = db.execute("SELECT name FROM players WHERE id=?", (host_id,)).fetchone()
        games = {(r[0], r[1]): r for r in db.execute(
            "SELECT player_id, n, guesses, started_at, finished_at, solved FROM contest_games WHERE contest_id=? AND finished_at IS NOT NULL", (cid,))}
        i_finished_today = status == "live" and (pid, idx) in games
        rows = []
        for mid, mname in db.execute("SELECT m.player_id, p.name FROM contest_members m JOIN players p ON p.id=m.player_id WHERE m.contest_id=?", (cid,)):
            strip, total, ms = [], 0, 0
            for n in range(days):
                g = games.get((mid, n))
                if (status == "upcoming") or (status == "live" and n > idx): strip.append(None); continue
                if status == "live" and n == idx and mid != pid and not i_finished_today: strip.append(None); continue
                if g:
                    pts = points(g[5], len(json.loads(g[2]))); strip.append(pts); total += pts
                    if g[5]: ms += g[4] - g[3]
                elif status == "live" and n == idx: strip.append(None)       # still playing today
                else: strip.append(0)                                         # missed day
            rows.append({"id": mid, "name": mname, "color": color(mid), "points": total, "ms": ms, "days": strip, "me": mid == pid})
        rows.sort(key=lambda r: (-r["points"], r["ms"], r["name"].lower()))
        game = None
        if member and status == "live":
            word = db.execute("SELECT word FROM contest_puzzles WHERE contest_id=? AND n=?", (cid, idx)).fetchone()[0]
            g = db.execute("SELECT guesses, started_at, finished_at, solved FROM contest_games WHERE contest_id=? AND n=? AND player_id=?", (cid, idx, pid)).fetchone()
            words = json.loads(g[0]) if g else []
            game = {"n": idx, "guesses": words, "patterns": [score(w, word) for w in words], "startedAt": g[1] if g else None,
                    "finishedAt": g[2] if g else None, "solved": None if not g or g[3] is None else bool(g[3])}
            if g and g[2]: game["answer"] = word
        return self.send(200, {"code": code, "name": name, "host": host[0] if host else "", "isHost": pid == host_id,
                               "startDate": day_date(start), "days": days, "day": idx, "status": status, "member": member,
                               "now": now_ms(), "nextUnlock": EPOCH_MS + (d + 1) * DAY_MS, "game": game, "rows": rows})

    def contest_action(self, code, action, p, d, b):
        c = self.contest_row(code)
        if not c: return self.err(404, "This contest link doesn't exist.")
        cid, _, host_id, start, days = c
        member = db.execute("SELECT 1 FROM contest_members WHERE contest_id=? AND player_id=?", (cid, p[0])).fetchone()
        if action == "join":
            if d - start >= days: return self.err(409, "This contest is over.")
            if not member:
                if db.execute("SELECT count(*) FROM contest_members WHERE contest_id=?", (cid,)).fetchone()[0] >= 200:
                    return self.err(409, "This contest is full.")
                db.execute("INSERT INTO contest_members VALUES (?,?,?)", (cid, p[0], now_ms()))
            return self.send(200, {"ok": True})
        if action == "leave":
            if p[0] == host_id: return self.err(409, "The host can't leave their own contest.")
            db.execute("DELETE FROM contest_members WHERE contest_id=? AND player_id=?", (cid, p[0]))
            return self.send(200, {"ok": True})
        # guess
        if not member: return self.err(403, "Join this contest to play.")
        idx = d - start
        if idx < 0: return self.err(409, "This contest hasn't started yet.")
        if idx >= days: return self.err(409, "This contest is over.")
        w = str(b.get("word", "")).lower()
        if (e := check_word(w)): return self.err(400, e)
        word = db.execute("SELECT word FROM contest_puzzles WHERE contest_id=? AND n=?", (cid, idx)).fetchone()[0]
        g = db.execute("SELECT guesses, started_at, finished_at FROM contest_games WHERE contest_id=? AND n=? AND player_id=?", (cid, idx, p[0])).fetchone()
        if g and g[2]: return self.err(409, "You've already finished today's puzzle.")
        started = (g[1] if g else None) or now_ms()
        words, pattern, solved, finished = advance(json.loads(g[0]) if g else [], w, word)
        fin = now_ms() if finished else None
        db.execute("INSERT OR REPLACE INTO contest_games VALUES (?,?,?,?,?,?,?)",
                   (cid, idx, p[0], json.dumps(words), started, fin, (1 if solved else 0) if finished else None))
        out = {"pattern": pattern, "finished": finished, "solved": solved if finished else None, "startedAt": started, "finishedAt": fin}
        if finished: out["answer"] = word
        return self.send(200, out)

    # ---------------- races ----------------
    def race_create(self, p, b):
        name = str(b.get("name", "")).strip()
        if not TITLE_RE.match(name): return self.err(400, "Give the room a name of up to 40 characters.")
        rid, code = str(uuid.uuid4()), invite_code()
        db.execute("INSERT INTO races VALUES (?,?,?,?,?,?)", (rid, code, name, p[0], now_ms(), now_ms()))
        db.execute("INSERT INTO race_members VALUES (?,?,?,?)", (rid, p[0], now_ms(), now_ms()))
        return self.send(200, {"code": code})

    def race_row(self, code):
        return db.execute("SELECT id, name, host_id FROM races WHERE code=?", (code,)).fetchone()

    def race_view(self, code, p):
        r = self.race_row(code)
        if not r: return self.err(404, "This race room doesn't exist or has expired.")
        rid, name, host_id = r
        end_round_if_due(rid)
        pid = p[0] if p else None
        online = online_ids(rid)
        totals = {}
        for gpid, gw, fin, sv, st in db.execute(
                "SELECT g.player_id, g.guesses, g.finished_at, g.solved, r.starts_at FROM race_games g JOIN race_rounds r ON r.race_id=g.race_id AND r.n=g.n WHERE g.race_id=? AND r.ended_at IS NOT NULL", (rid,)):
            t = totals.setdefault(gpid, [0, 0])
            t[0] += points(sv, len(json.loads(gw)))
            if sv: t[1] += fin - st
        members = [{"id": mid, "name": mname, "color": color(mid), "online": mid in online, "me": mid == pid,
                    "host": mid == host_id, "points": totals.get(mid, [0, 0])[0], "ms": totals.get(mid, [0, 0])[1]}
                   for mid, mname in db.execute("SELECT m.player_id, p.name FROM race_members m JOIN players p ON p.id=m.player_id WHERE m.race_id=? ORDER BY m.joined_at", (rid,))]
        members.sort(key=lambda m: (-m["points"], m["ms"]))
        rnd = db.execute("SELECT n, word, starts_at, ends_at, ended_at FROM race_rounds WHERE race_id=? ORDER BY n DESC LIMIT 1", (rid,)).fetchone()
        round_out = None
        if rnd:
            n, word, st, en, ended = rnd
            players, mine = [], None
            for gpid, gname, gw, fin, sv in db.execute(
                    "SELECT g.player_id, p.name, g.guesses, g.finished_at, g.solved FROM race_games g JOIN players p ON p.id=g.player_id WHERE g.race_id=? AND g.n=?", (rid, n)):
                words = json.loads(gw); pats = [score(w, word) for w in words]
                players.append({"id": gpid, "name": gname, "color": color(gpid), "patterns": pats, "finished": fin is not None,
                                "solved": bool(sv) if fin else None, "ms": fin - st if fin else None,
                                "points": points(sv, len(words)) if fin else None, "me": gpid == pid})
                if gpid == pid: mine = {"guesses": words, "patterns": pats, "finished": fin is not None, "solved": bool(sv) if fin else None, "finishedAt": fin}
            players.sort(key=lambda x: (not x["finished"], -(x["points"] or 0), x["ms"] or 1e15))
            round_out = {"n": n, "startsAt": st, "endsAt": en, "ended": ended is not None, "players": players, "mine": mine}
            if ended is not None: round_out["answer"] = word
        member = any(m["me"] for m in members)
        return self.send(200, {"code": code, "name": name, "isHost": pid == host_id, "member": member, "now": now_ms(),
                               "members": members, "round": round_out})

    def race_action(self, code, action, p, b):
        r = self.race_row(code)
        if not r: return self.err(404, "This race room doesn't exist or has expired.")
        rid, _, host_id = r
        db.execute("UPDATE races SET last_active_at=? WHERE id=?", (now_ms(), rid))
        if action == "join":
            db.execute("INSERT OR IGNORE INTO race_members VALUES (?,?,?,?)", (rid, p[0], now_ms(), now_ms()))
            notify(rid); return self.send(200, {"ok": True})
        member = db.execute("SELECT 1 FROM race_members WHERE race_id=? AND player_id=?", (rid, p[0])).fetchone()
        if not member: return self.err(403, "Join this room first.")
        end_round_if_due(rid)
        last = db.execute("SELECT n, word, starts_at, ends_at, ended_at FROM race_rounds WHERE race_id=? ORDER BY n DESC LIMIT 1", (rid,)).fetchone()
        if action == "start":
            if p[0] != host_id: return self.err(403, "Only the host can start a round.")
            if last and last[4] is None: return self.err(409, "A round is already running.")
            used = {w for (w,) in db.execute("SELECT word FROM race_rounds WHERE race_id=?", (rid,))}
            n = (last[0] + 1) if last else 1
            st = now_ms() + COUNTDOWN_MS
            db.execute("INSERT INTO race_rounds VALUES (?,?,?,?,?,NULL)", (rid, n, special_words(env_day(), 1, used)[0], st, st + ROUND_MS))
            players = online_ids(rid) | {p[0]}
            db.executemany("INSERT INTO race_games (race_id, n, player_id) VALUES (?,?,?)", [(rid, n, x) for x in players])
            notify(rid); return self.send(200, {"n": n, "startsAt": st})
        # guess
        if not last: return self.err(409, "The round hasn't started yet.")
        n, word, st, en, ended = last
        if ended is not None: return self.err(409, "This round is over.")
        if now_ms() < st: return self.err(409, "The round hasn't started yet.")
        g = db.execute("SELECT guesses, finished_at FROM race_games WHERE race_id=? AND n=? AND player_id=?", (rid, n, p[0])).fetchone()
        if not g: return self.err(403, "Wait for the next round to join in.")
        if g[1]: return self.err(409, "You've finished this round.")
        w = str(b.get("word", "")).lower()
        if (e := check_word(w)): return self.err(400, e)
        words, pattern, solved, finished = advance(json.loads(g[0]), w, word)
        fin = now_ms() if finished else None
        db.execute("UPDATE race_games SET guesses=?, finished_at=?, solved=? WHERE race_id=? AND n=? AND player_id=?",
                   (json.dumps(words), fin, (1 if solved else 0) if finished else None, rid, n, p[0]))
        end_round_if_due(rid)
        notify(rid)
        out = {"pattern": pattern, "finished": finished, "solved": solved if finished else None, "startedAt": st, "finishedAt": fin}
        if finished: out["answer"] = word
        return self.send(200, out)

    def race_events(self, code):
        with LOCK:
            r = self.race_row(code)
            p = self.player()
        if not r: return self.err(404, "This race room doesn't exist or has expired.")
        rid = r[0]; pid = p[0] if p else None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        q = queue.Queue(); entry = (q, pid)
        subscribers.setdefault(rid, set()).add(entry)
        notify(rid)                                  # someone came online
        try:
            self.wfile.write(b"event: changed\ndata: 1\n\n"); self.wfile.flush()
            last_seen = 0
            while True:
                try: msg = q.get(timeout=20); self.wfile.write(f"event: {msg}\ndata: 1\n\n".encode())
                except queue.Empty: self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                if pid and now_ms() - last_seen > 30000:
                    with LOCK: db.execute("UPDATE race_members SET last_seen=? WHERE race_id=? AND player_id=?", (now_ms(), rid, pid))
                    last_seen = now_ms()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            subscribers.get(rid, set()).discard(entry)
            if pid:
                with LOCK: db.execute("UPDATE race_members SET last_seen=0 WHERE race_id=? AND player_id=?", (rid, pid))
            notify(rid)
            self.close_connection = True


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8787))
    threading.Thread(target=round_ticker, daemon=True).start()
    print(f"Wordle Arena dev server on http://localhost:{port} (day {env_day()}, answer list {N_ANSWERS})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
