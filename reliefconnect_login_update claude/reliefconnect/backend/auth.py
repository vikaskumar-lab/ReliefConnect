"""
ReliefConnect Authentication Layer
Salted scrypt password hashing, server-side sessions (HttpOnly cookie),
role definitions and login rate limiting. Python standard library only.
"""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from typing import Any, Dict, Optional

COOKIE_NAME = "rc_session"
SESSION_TTL = 8 * 3600          # seconds a session stays valid
MIN_PASSWORD_LEN = 8
MAX_FAILED_LOGINS = 5           # per IP + email ...
FAILED_LOGIN_WINDOW = 300       # ... within this many seconds
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Roles
ADMIN = "administrator"
COORD = "relief_coordinator"
MANAGER = "shelter_manager"
VOLUNTEER = "volunteer"
CITIZEN = "citizen"
SELF_SERVICE_ROLES = (CITIZEN, VOLUNTEER)   # roles anyone may register as
STAFF = (ADMIN, COORD, MANAGER, VOLUNTEER)
OPS = (ADMIN, COORD)
FIELD_OPS = (ADMIN, COORD, MANAGER)

# Seeded staff accounts that can sign in (password from RC_DEMO_PASSWORD or random)
DEMO_ACCOUNTS = [
    "admin@reliefconnect.org",
    "sarah.j@reliefconnect.org",
    "manager.shl-north-gym@reliefconnect.org",
    "vol1@reliefconnect.org",
]
_DEMO_PASSWORD = os.environ.get("RC_DEMO_PASSWORD") or secrets.token_urlsafe(9)


def required_roles(method: str, path: str):
    """Roles allowed to call an API route; None means any signed-in user."""
    if method == "GET":
        if path in ("/api/volunteers", "/api/allocations", "/api/shelters/capacity-updates"):
            return STAFF
        if path == "/api/dispatches":
            return STAFF
        if path == "/api/audit-trail":
            return FIELD_OPS
        return None
    if method == "POST":
        if path == "/api/simulation/reset":
            return (ADMIN,)
        if path in ("/api/algorithm/run-allocations", "/api/simulation/run-100",
                    "/api/simulation/shelter-failure", "/api/volunteers", "/api/shelters"):
            return OPS
        if path.startswith("/api/requests/") and path.endswith("/verify"):
            return OPS
        if path == "/api/dispatches":
            return (ADMIN, COORD, MANAGER, VOLUNTEER)
        return None
    if method == "PUT":
        return FIELD_OPS
    return OPS


# ---------------------------------------------------------------- passwords
def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    try:
        salt_hex, hash_hex = (stored or "").split("$")
        expected = hash_password(password, bytes.fromhex(salt_hex)).split("$")[1]
    except Exception:
        # Dummy work so unknown accounts cost the same time as wrong passwords
        hash_password(password, b"\x00" * 16)
        return False
    return hmac.compare_digest(expected, hash_hex)


# ------------------------------------------------------------------ tables
def init_auth_tables(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS credentials (
        user_id TEXT PRIMARY KEY REFERENCES users(id),
        password_hash TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS auth_codes (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        code_hash TEXT NOT NULL,
        purpose TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        used_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
    """)
    conn.commit()


def ensure_demo_accounts(conn: sqlite3.Connection):
    """Gives the seeded staff accounts a password so they can sign in."""
    created = []
    for email in DEMO_ACCOUNTS:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not row:
            continue
        has_pw = conn.execute("SELECT 1 FROM credentials WHERE user_id = ?", (row[0],)).fetchone()
        if not has_pw:
            conn.execute("INSERT INTO credentials (user_id, password_hash, created_at) VALUES (?, ?, ?)",
                         (row[0], hash_password(_DEMO_PASSWORD), time.time()))
            created.append(email)
    conn.commit()
    if created:
        print("[ReliefConnect Auth] Staff accounts enabled: " + ", ".join(created))
        if not os.environ.get("RC_DEMO_PASSWORD"):
            print(f"[ReliefConnect Auth] Password for these accounts this run: {_DEMO_PASSWORD}")
            print("[ReliefConnect Auth] Set RC_DEMO_PASSWORD to choose your own.")


# ---------------------------------------------------------------- sessions
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                 (_hash_token(token), user_id, now, now + SESSION_TTL))
    conn.commit()
    return token


def user_for_token(conn: sqlite3.Connection, token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    row = conn.execute("""
        SELECT u.id, u.full_name, u.email, u.phone_number, u.role
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1
    """, (_hash_token(token), time.time())).fetchone()
    if not row:
        return None
    return {"id": row[0], "full_name": row[1], "email": row[2], "phone": row[3], "role": row[4]}


def delete_session(conn: sqlite3.Connection, token: Optional[str]):
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
        conn.commit()


def token_from_cookie_header(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    try:
        jar = SimpleCookie()
        jar.load(header)
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None
    except Exception:
        return None


def session_cookie(token: str, secure: bool) -> str:
    parts = [f"{COOKIE_NAME}={token}", "HttpOnly", "SameSite=Lax", "Path=/", f"Max-Age={SESSION_TTL}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie(secure: bool) -> str:
    parts = [f"{COOKIE_NAME}=", "HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=0"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


# ------------------------------------------------------------ rate limiting
_failures: Dict[str, list] = {}
_lock = threading.Lock()


def too_many_failures(key: str) -> bool:
    cutoff = time.time() - FAILED_LOGIN_WINDOW
    with _lock:
        recent = [t for t in _failures.get(key, []) if t > cutoff]
        _failures[key] = recent
        return len(recent) >= MAX_FAILED_LOGINS


def record_failure(key: str):
    with _lock:
        _failures.setdefault(key, []).append(time.time())


def clear_failures(key: str):
    with _lock:
        _failures.pop(key, None)


# ------------------------------------------------------------ OTP & password reset
def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def create_auth_code(conn: sqlite3.Connection, user_id: str, purpose: str, ttl_seconds: int = 300) -> str:
    code = "".join(str(secrets.randbelow(10)) for _ in range(6))
    now = time.time()
    conn.execute("DELETE FROM auth_codes WHERE user_id = ? AND purpose = ?", (user_id, purpose))
    conn.execute(
        "INSERT INTO auth_codes (id, user_id, code_hash, purpose, created_at, expires_at, used_at) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL)",
        (secrets.token_urlsafe(16), user_id, _hash_code(code), purpose, now, now + ttl_seconds),
    )
    conn.commit()
    return code


def verify_auth_code(conn: sqlite3.Connection, user_id: str, code: str, purpose: str) -> bool:
    row = conn.execute(
        "SELECT code_hash, expires_at, used_at FROM auth_codes "
        "WHERE user_id = ? AND purpose = ?", (user_id, purpose)
    ).fetchone()
    if not row or row["used_at"] is not None or row["expires_at"] < time.time():
        return False
    return hmac.compare_digest(row["code_hash"], _hash_code(code))


def consume_auth_code(conn: sqlite3.Connection, user_id: str, purpose: str):
    conn.execute(
        "UPDATE auth_codes SET used_at = ? WHERE user_id = ? AND purpose = ?",
        (time.time(), user_id, purpose),
    )
    conn.commit()


def update_password(conn: sqlite3.Connection, user_id: str, password: str):
    conn.execute(
        "UPDATE credentials SET password_hash = ?, created_at = ? WHERE user_id = ?",
        (hash_password(password), time.time(), user_id),
    )
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
