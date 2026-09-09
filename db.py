import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def _default_db_path():
    """Return a writable default for CI/local runs while preserving Docker /data."""
    configured = os.getenv("AI_DB_PATH")
    if configured:
        return configured

    # GitHub-hosted runners cannot write to /data. Keep /data as the
    # container default, but automatically use the workspace in Actions.
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        workspace = os.getenv("GITHUB_WORKSPACE", os.getcwd())
        return os.path.join(workspace, ".ci", "ai.db")

    return "/data/ai.db"


DB_PATH = _default_db_path()
_lock = threading.Lock()


def _conn():
    directory = os.path.dirname(DB_PATH)
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except PermissionError:
            # Last-resort fallback for local environments where /data is not writable.
            fallback_root = os.getenv("CI_STORAGE_ROOT") or os.getenv("TMPDIR") or ".ci"
            DB_PATH_LOCAL = str(Path(fallback_root) / "ai.db")
            directory = os.path.dirname(DB_PATH_LOCAL)
            if directory:
                os.makedirs(directory, exist_ok=True)
            conn = sqlite3.connect(DB_PATH_LOCAL, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS conversations (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
          created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
          role TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
        CREATE TABLE IF NOT EXISTS memories (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
          memory TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
        CREATE TABLE IF NOT EXISTS files (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
          mime TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_id);
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          password_hash TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE TABLE IF NOT EXISTS usage (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
          kind TEXT NOT NULL, amount INTEGER NOT NULL, created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_user_time ON usage(user_id, created_at);
        ''')


def create_user(email: str, password_hash: str, name: str):
    uid = uuid.uuid4().hex
    with _lock, _conn() as c:
        c.execute("INSERT INTO users(id,email,name,password_hash,created_at) VALUES (?,?,?,?,?)", (uid, email, name or email.split('@')[0], password_hash, time.time()))
        r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(r)


def get_user_by_email(email: str):
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return dict(r) if r else None


def get_user_by_id(user_id: str):
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(r) if r else None


def record_usage(user_id: str, kind: str, amount: int = 1):
    with _lock, _conn() as c:
        c.execute("INSERT INTO usage(user_id,kind,amount,created_at) VALUES (?,?,?,?)", (user_id, kind[:80], max(1, amount), time.time()))


def usage_summary(user_id: str, since: float = 0):
    with _conn() as c:
        rows = c.execute("SELECT kind,SUM(amount) AS amount FROM usage WHERE user_id=? AND created_at>=? GROUP BY kind", (user_id, since)).fetchall()
        return {r["kind"]: int(r["amount"] or 0) for r in rows}


def create_conversation(user_id: str, title: str):
    cid = uuid.uuid4().hex
    now = time.time()
    with _lock, _conn() as c:
        c.execute("INSERT INTO conversations VALUES (?,?,?,?,?)", (cid, user_id, title[:200], now, now))
    return cid


def conversation_owner(cid: str):
    with _conn() as c:
        r = c.execute("SELECT user_id FROM conversations WHERE id=?", (cid,)).fetchone()
        return r["user_id"] if r else None


def list_conversations(user_id: str):
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT id,title,created_at,updated_at FROM conversations WHERE user_id=? ORDER BY updated_at DESC LIMIT 100", (user_id,))]


def save_message(cid: str, role: str, content: str):
    now = time.time()
    with _lock, _conn() as c:
        c.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES (?,?,?,?)", (cid, role, content[:200000], now))
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))


def get_messages(cid: str, limit: int = 100):
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT role,content,created_at FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (cid, limit))][::-1]


def add_memory(user_id: str, memory: str):
    now = time.time()
    with _lock, _conn() as c:
        c.execute("INSERT INTO memories(user_id,memory,created_at,updated_at) VALUES (?,?,?,?)", (user_id, memory[:4000], now, now))


def list_memories(user_id: str):
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT id,memory,created_at,updated_at FROM memories WHERE user_id=? ORDER BY updated_at DESC LIMIT 100", (user_id,))]


def delete_memory(user_id: str, memory_id: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM memories WHERE user_id=? AND id=?", (user_id, memory_id))
        return c.rowcount > 0


def save_file(user_id: str, name: str, mime: str, content: str):
    fid = uuid.uuid4().hex
    with _lock, _conn() as c:
        c.execute("INSERT INTO files VALUES (?,?,?,?,?,?)", (fid, user_id, name[:255], mime[:120], content[:5000000], time.time()))
    return fid


def list_files(user_id: str):
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT id,name,mime,created_at FROM files WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user_id,))]


def get_file(user_id: str, fid: str):
    with _conn() as c:
        r = c.execute("SELECT * FROM files WHERE user_id=? AND id=?", (user_id, fid)).fetchone()
        return dict(r) if r else None


def delete_file(user_id: str, fid: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM files WHERE user_id=? AND id=?", (user_id, fid))
        return c.rowcount > 0
