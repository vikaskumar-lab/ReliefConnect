
"""ReliefConnect authentication and session management."""

import hashlib
import hmac
import secrets
import time
import sqlite3


# Authentication settings
COOKIE_NAME = "rc_session"
SESSION_TTL = 8 * 60 * 60  # 8 hours
MIN_PASSWORD_LEN = 8

# Application roles
ADMIN = "administrator"
COORDINATOR = "relief_coordinator"
SHELTER_MANAGER = "shelter_manager"
VOLUNTEER = "volunteer"
CITIZEN = "citizen"

ALLOWED_ROLES = {
    ADMIN,
    COORDINATOR,
    SHELTER_MANAGER,
    VOLUNTEER,
    CITIZEN,
}

PUBLIC_REGISTRATION_ROLES = {
    CITIZEN,
    VOLUNTEER,
    COORDINATOR,
    SHELTER_MANAGER,
}


def init_auth_tables(conn):
    """Create authentication tables without deleting existing users."""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS credentials (
            user_id TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_user_id
        ON sessions(user_id)
    """)

    conn.commit()


def hash_password(password):
    """Securely hash a password using scrypt."""

    if not isinstance(password, str):
        raise ValueError("Password must be text.")

    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(
            f"Password must contain at least {MIN_PASSWORD_LEN} characters."
        )

    salt = secrets.token_bytes(16)

    password_hash = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )

    return salt.hex() + "$" + password_hash.hex()


def verify_password(password, stored_hash):
    """Verify a password against its stored scrypt hash."""

    if not isinstance(password, str) or not isinstance(stored_hash, str):
        return False

    try:
        salt_hex, expected_hash_hex = stored_hash.split("$", 1)

        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(expected_hash_hex)

        actual_hash = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )

        return hmac.compare_digest(actual_hash, expected_hash)

    except (ValueError, TypeError):
        return False


def save_password(conn, user_id, password):
    """Create or update a user's password hash."""

    password_hash = hash_password(password)

    conn.execute("""
        INSERT INTO credentials (user_id, password_hash, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET password_hash = excluded.password_hash
    """, (user_id, password_hash, time.time()))

    conn.commit()


def check_password(conn, user_id, password):
    """Check the password belonging to a user."""

    row = conn.execute("""
        SELECT password_hash
        FROM credentials
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    if not row:
        return False

    return verify_password(password, row["password_hash"])


def create_session(conn, user_id):
    """Create a secure random session and store only its hash."""

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    now = time.time()
    expires_at = now + SESSION_TTL

    conn.execute("""
        INSERT INTO sessions (
            token_hash, user_id, created_at, expires_at
        )
        VALUES (?, ?, ?, ?)
    """, (token_hash, user_id, now, expires_at))

    conn.commit()

    # Return the original token only to the server for the cookie.
    return token


def get_user_by_token(conn, token):
    """Return an active user when the session token is valid."""

    if not token:
        return None

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.time()

    row = conn.execute("""
        SELECT
            users.id,
            users.full_name,
            users.email,
            users.role,
            users.is_active,
            sessions.expires_at
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token_hash = ?
          AND sessions.expires_at > ?
          AND users.is_active = 1
    """, (token_hash, now)).fetchone()

    if row is None:
        return None

    return dict(row)


def delete_session(conn, token):
    """Delete a session during logout."""

    if not token:
        return

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    conn.execute("""
        DELETE FROM sessions
        WHERE token_hash = ?
    """, (token_hash,))

    conn.commit()


def delete_user_sessions(conn, user_id):
    """Revoke every session belonging to a user."""

    conn.execute("""
        DELETE FROM sessions
        WHERE user_id = ?
    """, (user_id,))

    conn.commit()


def get_token_from_cookie(cookie_header):
    """Extract the ReliefConnect session token from a Cookie header."""

    if not cookie_header:
        return None

    for item in cookie_header.split(";"):
        name, separator, value = item.strip().partition("=")

        if separator and name == COOKIE_NAME:
            return value.strip() or None

    return None


def session_cookie_header(token, secure=False):
    """Build the HTTP Set-Cookie header for login."""

    parts = [
        f"{COOKIE_NAME}={token}",
        "Path=/",
        f"Max-Age={SESSION_TTL}",
        "HttpOnly",
        "SameSite=Lax",
    ]

    if secure:
        parts.append("Secure")

    return "; ".join(parts)


def clear_session_cookie_header(secure=False):
    """Build the HTTP Set-Cookie header for logout."""

    parts = [
        f"{COOKIE_NAME}=",
        "Path=/",
        "Max-Age=0",
        "HttpOnly",
        "SameSite=Lax",
    ]

    if secure:
        parts.append("Secure")

    return "; ".join(parts)


def role_is_allowed(user, allowed_roles):
    """Check whether an authenticated user has an allowed role."""

    if not user:
        return False

    return user.get("role") in allowed_roles
