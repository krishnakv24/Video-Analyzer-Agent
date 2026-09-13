"""Db for Frame."""

import sqlite3
from contextlib import contextmanager
from .config import DB_PATH


@contextmanager
def db_connection():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_db():
    with db_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf_token TEXT NOT NULL,
            expires_at INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, size INTEGER NOT NULL,
            offset INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        if "user_id" not in {row[1] for row in db.execute("PRAGMA table_info(uploads)")}:
            db.execute("ALTER TABLE uploads ADD COLUMN user_id TEXT REFERENCES users(id)")
        db.execute("CREATE INDEX IF NOT EXISTS uploads_owner ON uploads(user_id)")
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, upload_id TEXT NOT NULL, status TEXT NOT NULL,
            entities TEXT NOT NULL, instructions TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(upload_id) REFERENCES uploads(id)
        )""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        if "metadata" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN metadata TEXT")
        if "error" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN error TEXT")
        db.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )""")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_video_per_session ON jobs(upload_id)")
        db.execute("""CREATE TABLE IF NOT EXISTS images (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, filename TEXT NOT NULL,
            stored_name TEXT NOT NULL, media_type TEXT NOT NULL, size INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(session_id) REFERENCES jobs(id)
        )""")
        image_columns = {row[1] for row in db.execute("PRAGMA table_info(images)")}
        if "message_id" not in image_columns:
            db.execute("ALTER TABLE images ADD COLUMN message_id INTEGER REFERENCES messages(id)")


initialize_db()
