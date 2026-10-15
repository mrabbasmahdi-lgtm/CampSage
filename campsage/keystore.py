"""Password verification and encrypted per-student API-key storage."""

from __future__ import annotations

import base64
import hmac
import os
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


PASSWORD_SALT_BYTES = 16
KEY_SALT_BYTES = 16
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1


def _derive(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(password.encode())


def hash_password(password: str) -> str:
    salt = os.urandom(PASSWORD_SALT_BYTES)
    digest = _derive(password, salt)
    return f"scrypt${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt_text, digest_text = encoded.split("$", 2)
        actual = _derive(password, base64.urlsafe_b64decode(salt_text))
        expected = base64.urlsafe_b64decode(digest_text)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def encryption_key_from_password(password: str, salt: bytes) -> bytes:
    return base64.urlsafe_b64encode(_derive(password, salt))


class KeyStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS student_keys (
                    student_id TEXT PRIMARY KEY,
                    encryption_salt BLOB NOT NULL,
                    encrypted_api_key BLOB NOT NULL,
                    connected_at TEXT NOT NULL
                )"""
            )

    def has_key(self, student_id: str) -> bool:
        with sqlite3.connect(self.database_path) as conn:
            return conn.execute("SELECT 1 FROM student_keys WHERE student_id = ?", (student_id,)).fetchone() is not None

    def prepare_encryption_key(self, student_id: str, password: str) -> bytes:
        """Derive the student's encryption key without retaining the password."""
        with sqlite3.connect(self.database_path) as conn:
            row = conn.execute("SELECT encryption_salt FROM student_keys WHERE student_id = ?", (student_id,)).fetchone()
            if row:
                salt = row[0]
            else:
                salt = os.urandom(KEY_SALT_BYTES)
                # A placeholder ciphertext is not written. The salt is held in a short-lived
                # session handoff until connect_key writes the first encrypted value.
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS student_key_salts (student_id TEXT PRIMARY KEY, salt BLOB NOT NULL)"
                )
                conn.execute("INSERT OR IGNORE INTO student_key_salts(student_id, salt) VALUES (?, ?)", (student_id, salt))
        return encryption_key_from_password(password, salt)

    def connect_key(self, student_id: str, encryption_key: bytes, api_key: str) -> None:
        if not api_key or len(api_key) > 512:
            raise ValueError("API key is empty or too long")
        encrypted = Fernet(encryption_key).encrypt(api_key.encode())
        with sqlite3.connect(self.database_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS student_key_salts (student_id TEXT PRIMARY KEY, salt BLOB NOT NULL)")
            row = conn.execute("SELECT salt FROM student_key_salts WHERE student_id = ?", (student_id,)).fetchone()
            if row:
                salt = row[0]
            else:
                existing = conn.execute("SELECT encryption_salt FROM student_keys WHERE student_id = ?", (student_id,)).fetchone()
                salt = existing[0] if existing else os.urandom(KEY_SALT_BYTES)
            conn.execute("INSERT OR IGNORE INTO student_key_salts(student_id, salt) VALUES (?, ?)", (student_id, salt))
            conn.execute(
                """INSERT INTO student_keys(student_id, encryption_salt, encrypted_api_key, connected_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(student_id) DO UPDATE SET
                     encryption_salt=excluded.encryption_salt,
                     encrypted_api_key=excluded.encrypted_api_key,
                     connected_at=excluded.connected_at""",
                (student_id, salt, encrypted),
            )

    def decrypt_key(self, student_id: str, encryption_key: bytes) -> str | None:
        with sqlite3.connect(self.database_path) as conn:
            row = conn.execute(
                "SELECT encryption_salt, encrypted_api_key FROM student_keys WHERE student_id = ?", (student_id,)
            ).fetchone()
        if not row:
            return None
        try:
            return Fernet(encryption_key).decrypt(row[1]).decode()
        except (InvalidToken, UnicodeDecodeError):
            return None

    def raw_row(self, student_id: str) -> tuple | None:
        """Test-only inspection helper; callers must never return this over HTTP."""
        with sqlite3.connect(self.database_path) as conn:
            return conn.execute("SELECT * FROM student_keys WHERE student_id = ?", (student_id,)).fetchone()
