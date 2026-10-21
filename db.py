import os
import re
import sqlite3
import time

from config import DB_PATH, MAIN_ADMIN_ID

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
_db = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA journal_mode=WAL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_name TEXT,
    first_seen INTEGER,
    last_seen  INTEGER,
    starts     INTEGER DEFAULT 0,
    banned     INTEGER DEFAULT 0,
    ban_reason TEXT
);
CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS connections (
    id           TEXT PRIMARY KEY,
    owner_id     INTEGER,
    user_chat_id INTEGER,
    enabled      INTEGER,
    can_reply    INTEGER,
    can_delete   INTEGER,
    date         INTEGER
);
CREATE TABLE IF NOT EXISTS settings (
    owner_id        INTEGER PRIMARY KEY,
    ar_mode         TEXT    DEFAULT 'off',
    ar_text         TEXT    DEFAULT 'Здравствуйте! Сейчас я не в сети, отвечу, как только освобожусь 🙌',
    ar_offline_min  INTEGER DEFAULT 10,
    ar_cooldown_min INTEGER DEFAULT 60,
    antiscam        INTEGER DEFAULT 1,
    notify_deleted  INTEGER DEFAULT 1,
    notify_edited   INTEGER DEFAULT 1,
    last_active     INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    conn_id    TEXT,
    chat_id    INTEGER,
    message_id INTEGER,
    from_id    INTEGER,
    from_name  TEXT,
    text       TEXT,
    media_type TEXT,
    file_id    TEXT,
    date       INTEGER,
    PRIMARY KEY (conn_id, chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date);
CREATE TABLE IF NOT EXISTS mutes (
    owner_id INTEGER,
    chat_id  INTEGER,
    reason   TEXT,
    date     INTEGER,
    PRIMARY KEY (owner_id, chat_id)
);
CREATE TABLE IF NOT EXISTS ar_log (
    owner_id INTEGER,
    chat_id  INTEGER,
    ts       INTEGER,
    PRIMARY KEY (owner_id, chat_id)
);
CREATE TABLE IF NOT EXISTS scam_words (word TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS events (type TEXT, owner_id INTEGER, ts INTEGER);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type, ts);
"""

DEFAULT_SCAM_WORDS = [
    "скинь код", "скинуть код", "дай код", "отправь код", "пришли код", "продиктуй код",
    "код из смс", "код из телеграм", "код от телеграм", "код от тг", "код подтверждения",
    "код для входа", "код авторизации", "облачный пароль", "скинь пароль",
    "продай нфт", "продай nft", "купи нфт", "купи nft", "нфт подар", "nft подар",
    "крипт", "usdt", "биткоин", "bitcoin", "btc", "эфириум", "ton кошел", "сид фраз",
    "seed фраз", "seed phrase", "кошелек", "кошелёк", "wallet",
    "инвестиц", "пассивный доход", "быстрый заработок", "удвою", "x2 депозит",
    "бесплатный премиум", "бесплатные звезды", "бесплатные звёзды", "free stars",
    "перейди по ссылке", "подтверди аккаунт", "верифицируй", "твой аккаунт заблокир",
]

_scam_cache: list[str] | None = None


def now() -> int:
    return int(time.time())


def q(sql, params=()):
    return _db.execute(sql, params).fetchall()


def q1(sql, params=()):
    return _db.execute(sql, params).fetchone()


def ex(sql, params=()):
    return _db.execute(sql, params)


def init():
    _db.executescript(SCHEMA)
    if not q1("SELECT 1 FROM scam_words LIMIT 1"):
        _db.executemany("INSERT OR IGNORE INTO scam_words(word) VALUES (?)",
                        [(w,) for w in DEFAULT_SCAM_WORDS])


# ---------- пользователи ----------

def touch_user(u):
    t = now()
    ex("""INSERT INTO users(user_id, username, first_name, first_seen, last_seen) VALUES (?,?,?,?,?)
          ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
              first_name=excluded.first_name, last_seen=excluded.last_seen""",
       (u.id, u.username, u.full_name, t, t))


def inc_starts(uid: int):
    ex("UPDATE users SET starts = starts + 1 WHERE user_id = ?", (uid,))


def get_user(uid: int):
    return q1("SELECT * FROM users WHERE user_id = ?", (uid,))


def is_banned(uid: int) -> bool:
    r = q1("SELECT banned FROM users WHERE user_id = ?", (uid,))
    return bool(r and r["banned"])


def set_ban(uid: int, banned: bool, reason: str | None = None):
    t = now()
    ex("""INSERT INTO users(user_id, first_seen, last_seen, banned, ban_reason) VALUES (?,?,?,?,?)
          ON CONFLICT(user_id) DO UPDATE SET banned=excluded.banned, ban_reason=excluded.ban_reason""",
       (uid, t, t, int(banned), reason if banned else None))


def banned_list():
    return q("SELECT * FROM users WHERE banned = 1 ORDER BY last_seen DESC")


def top_starts(limit=20):
    return q("SELECT * FROM users ORDER BY starts DESC, last_seen DESC LIMIT ?", (limit,))


def recent_users(limit=20):
    return q("SELECT * FROM users ORDER BY first_seen DESC LIMIT ?", (limit,))


def all_users():
    return q("SELECT * FROM users ORDER BY first_seen")


def broadcast_ids():
    return [r["user_id"] for r in q("SELECT user_id FROM users WHERE banned = 0")]


# ---------- админы ----------

def is_admin(uid: int) -> bool:
    return uid == MAIN_ADMIN_ID or q1("SELECT 1 FROM admins WHERE user_id = ?", (uid,)) is not None


def add_admin(uid: int):
    ex("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (uid,))


def del_admin(uid: int):
    ex("DELETE FROM admins WHERE user_id = ?", (uid,))


def list_admins():
    return [MAIN_ADMIN_ID] + [r["user_id"] for r in q("SELECT user_id FROM admins")]


# ---------- бизнес-подключения ----------

def save_connection(bc):
    r = bc.rights
    can_reply = bool(r.can_reply) if r else bool(bc.can_reply)
    can_delete = bool(r and r.can_delete_all_messages)
    ex("""INSERT OR REPLACE INTO connections(id, owner_id, user_chat_id, enabled, can_reply, can_delete, date)
          VALUES (?,?,?,?,?,?,?)""",
       (bc.id, bc.user.id, bc.user_chat_id, int(bc.is_enabled), int(can_reply), int(can_delete), now()))


def get_connection(conn_id: str):
    r = q1("SELECT * FROM connections WHERE id = ?", (conn_id,))
    return dict(r) if r else None


def owner_connections(owner_id: int):
    return q("SELECT * FROM connections WHERE owner_id = ? ORDER BY date DESC", (owner_id,))


# ---------- настройки владельца ----------

SETTING_KEYS = {"ar_mode", "ar_text", "ar_offline_min", "ar_cooldown_min",
                "antiscam", "notify_deleted", "notify_edited"}


def get_settings(owner_id: int) -> dict:
    ex("INSERT OR IGNORE INTO settings(owner_id) VALUES (?)", (owner_id,))
    return dict(q1("SELECT * FROM settings WHERE owner_id = ?", (owner_id,)))


def set_setting(owner_id: int, key: str, value):
    if key not in SETTING_KEYS:
        raise ValueError(key)
    get_settings(owner_id)
    ex(f"UPDATE settings SET {key} = ? WHERE owner_id = ?", (value, owner_id))


def toggle_setting(owner_id: int, key: str) -> int:
    value = 0 if get_settings(owner_id)[key] else 1
    set_setting(owner_id, key, value)
    return value


def set_last_active(owner_id: int):
    get_settings(owner_id)
    ex("UPDATE settings SET last_active = ? WHERE owner_id = ?", (now(), owner_id))


# ---------- кэш сообщений ----------

def cache_message(conn_id, chat_id, message_id, from_id, from_name, text, media_type, file_id):
    ex("""INSERT OR REPLACE INTO messages(conn_id, chat_id, message_id, from_id, from_name, text, media_type, file_id, date)
          VALUES (?,?,?,?,?,?,?,?,?)""",
       (conn_id, chat_id, message_id, from_id, from_name, text, media_type, file_id, now()))


def get_cached(conn_id, chat_id, message_id):
    return q1("SELECT * FROM messages WHERE conn_id = ? AND chat_id = ? AND message_id = ?",
              (conn_id, chat_id, message_id))


def cleanup_messages(days: int) -> int:
    return ex("DELETE FROM messages WHERE date < ?", (now() - days * 86400,)).rowcount


# ---------- мут ----------

def mute(owner_id, chat_id, reason="manual"):
    ex("INSERT OR REPLACE INTO mutes(owner_id, chat_id, reason, date) VALUES (?,?,?,?)",
       (owner_id, chat_id, reason, now()))


def unmute(owner_id, chat_id) -> bool:
    return ex("DELETE FROM mutes WHERE owner_id = ? AND chat_id = ?", (owner_id, chat_id)).rowcount > 0


def is_muted(owner_id, chat_id) -> bool:
    return q1("SELECT 1 FROM mutes WHERE owner_id = ? AND chat_id = ?", (owner_id, chat_id)) is not None


def list_mutes(owner_id):
    return q("SELECT * FROM mutes WHERE owner_id = ? ORDER BY date DESC", (owner_id,))


# ---------- автоответ ----------

def ar_try(owner_id, chat_id, cooldown_sec) -> bool:
    """True, если в этот чат можно отправить автоответ (и сразу помечает отправку)."""
    r = q1("SELECT ts FROM ar_log WHERE owner_id = ? AND chat_id = ?", (owner_id, chat_id))
    if r and now() - r["ts"] < cooldown_sec:
        return False
    ex("INSERT OR REPLACE INTO ar_log(owner_id, chat_id, ts) VALUES (?,?,?)", (owner_id, chat_id, now()))
    return True


# ---------- антискам ----------

def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text)


def scam_words() -> list[str]:
    global _scam_cache
    if _scam_cache is None:
        _scam_cache = [r["word"] for r in q("SELECT word FROM scam_words ORDER BY word")]
    return _scam_cache


def add_scam_word(word: str) -> bool:
    global _scam_cache
    _scam_cache = None
    return ex("INSERT OR IGNORE INTO scam_words(word) VALUES (?)", (normalize(word).strip(),)).rowcount > 0


def del_scam_word(word: str) -> bool:
    global _scam_cache
    _scam_cache = None
    return ex("DELETE FROM scam_words WHERE word = ?", (normalize(word).strip(),)).rowcount > 0


def find_scam(text: str) -> str | None:
    t = normalize(text)
    for w in scam_words():
        if normalize(w) in t:
            return w
    return None


# ---------- события / статистика ----------

def log_event(type_: str, owner_id: int | None = None):
    ex("INSERT INTO events(type, owner_id, ts) VALUES (?,?,?)", (type_, owner_id, now()))


def count_events(type_: str, since: int = 0, owner_id: int | None = None) -> int:
    if owner_id is None:
        return q1("SELECT COUNT(*) c FROM events WHERE type = ? AND ts >= ?", (type_, since))["c"]
    return q1("SELECT COUNT(*) c FROM events WHERE type = ? AND ts >= ? AND owner_id = ?",
              (type_, since, owner_id))["c"]


def stats() -> dict:
    t = now()
    one = lambda sql, p=(): q1(sql, p)[0] or 0  # noqa: E731
    return {
        "users": one("SELECT COUNT(*) FROM users"),
        "new_24h": one("SELECT COUNT(*) FROM users WHERE first_seen >= ?", (t - 86400,)),
        "new_7d": one("SELECT COUNT(*) FROM users WHERE first_seen >= ?", (t - 7 * 86400,)),
        "active_24h": one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (t - 86400,)),
        "starts": one("SELECT SUM(starts) FROM users"),
        "banned": one("SELECT COUNT(*) FROM users WHERE banned = 1"),
        "conn_active": one("SELECT COUNT(*) FROM connections WHERE enabled = 1"),
        "conn_total": one("SELECT COUNT(*) FROM connections"),
        "cached": one("SELECT COUNT(*) FROM messages"),
        "mutes": one("SELECT COUNT(*) FROM mutes"),
        "deleted": count_events("deleted"),
        "edited": count_events("edited"),
        "scam": count_events("scam"),
        "muted_deleted": count_events("muted_deleted"),
        "autoreply": count_events("autoreply"),
        "deleted_24h": count_events("deleted", t - 86400),
        "scam_24h": count_events("scam", t - 86400),
    }
