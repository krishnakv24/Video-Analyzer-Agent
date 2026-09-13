"""Preview or clear local Frame sessions and uploads.

Run only while the API is stopped. User accounts are retained by default.
"""

import argparse
import sqlite3
from pathlib import Path
from uuid import UUID

from backend.config import DB_PATH, IMAGE_DIR, UPLOAD_DIR


TABLES = ("messages", "images", "jobs", "uploads", "auth_sessions")


def stored_files(db: sqlite3.Connection) -> list[Path]:
    paths = []
    for (upload_id,) in db.execute("SELECT id FROM uploads"):
        try:
            upload_id = str(UUID(upload_id))
        except (ValueError, TypeError):
            continue
        paths.extend((UPLOAD_DIR / f"{upload_id}.part", UPLOAD_DIR / f"{upload_id}.video"))
    for session_id, stored_name in db.execute("SELECT session_id, stored_name FROM images"):
        # Old or manually edited rows must never turn cleanup into an arbitrary file delete.
        path = (IMAGE_DIR / session_id / stored_name).resolve()
        if path.is_relative_to(IMAGE_DIR.resolve()) and path.parent == (IMAGE_DIR / session_id).resolve():
            paths.append(path)
    return [path for path in paths if path.is_file()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or reset local Frame data")
    parser.add_argument("--execute", action="store_true", help="Actually delete the listed data")
    parser.add_argument("--include-users", action="store_true", help="Also delete user accounts")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"No database found at {DB_PATH}")
        return
    with sqlite3.connect(DB_PATH, timeout=1) as db:
        db.execute("PRAGMA busy_timeout = 1000")
        counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
        counts["users"] = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        files = stored_files(db)
        print(f"Database: {DB_PATH}")
        for table in TABLES:
            print(f"  {table}: {counts[table]} rows")
        print(f"  users: {counts['users']} rows ({'DELETE' if args.include_users else 'keep'})")
        print(f"  stored files: {len(files)} ({sum(path.stat().st_size for path in files)} bytes)")
        if not args.execute:
            print("Preview only. Stop the API, then rerun with --execute to delete this data.")
            return

        # An active FastAPI process could create a new upload between this snapshot and deletion.
        try:
            db.execute("BEGIN EXCLUSIVE")
            for table in TABLES:
                db.execute(f"DELETE FROM {table}")
            if args.include_users:
                db.execute("DELETE FROM users")
            db.commit()
        except sqlite3.OperationalError as exc:
            raise SystemExit(f"Could not lock database. Stop the API and retry: {exc}") from exc

    failures = []
    for path in files:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    for directory in IMAGE_DIR.iterdir():
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    if failures:
        raise SystemExit("Database cleared, but some media files could not be removed:\n" + "\n".join(failures))
    print("Cleanup complete.")


if __name__ == "__main__":
    main()
