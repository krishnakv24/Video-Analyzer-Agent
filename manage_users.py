"""Local administrator commands. Passwords are entered without shell arguments."""

import argparse
import getpass
import sqlite3
from uuid import uuid4

from backend.auth import password_hash
from backend.db import db_connection


def password():
    value = getpass.getpass("Password (at least 12 characters): ")
    if len(value) < 12:
        raise SystemExit("Password must be at least 12 characters")
    if value != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    return value


def main():
    parser = argparse.ArgumentParser(description="Manage Frame accounts")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("username")
    create.add_argument("--admin", action="store_true")
    commands.add_parser("list")
    for name in ("disable", "enable", "reset-password", "claim-existing"):
        commands.add_parser(name).add_argument("username")
    args = parser.parse_args()
    if args.command == "create":
        username = args.username.strip()
        if not username or len(username) > 80:
            raise SystemExit("Username must contain 1–80 characters")
        digest = password_hash.hash(password())
        try:
            with db_connection() as db:
                db.execute("INSERT INTO users(id, username, password_hash, is_admin) VALUES (?, ?, ?, ?)",
                           (str(uuid4()), username, digest, int(args.admin)))
        except sqlite3.IntegrityError:
            raise SystemExit("Username already exists") from None
        print(f"Created {username}")
        return
    with db_connection() as db:
        if args.command == "list":
            for row in db.execute("SELECT username, is_admin, is_active FROM users ORDER BY username"):
                print(f"{row['username']}  admin={bool(row['is_admin'])}  active={bool(row['is_active'])}")
            return
        user = db.execute("SELECT id FROM users WHERE username = ?", (args.username,)).fetchone()
        if user is None:
            raise SystemExit("User not found")
        if args.command == "reset-password":
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash.hash(password()), user["id"]))
            db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user["id"],))
        elif args.command == "disable":
            db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user["id"],))
            db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user["id"],))
        elif args.command == "enable":
            db.execute("UPDATE users SET is_active = 1 WHERE id = ?", (user["id"],))
        elif args.command == "claim-existing":
            count = db.execute("UPDATE uploads SET user_id = ? WHERE user_id IS NULL", (user["id"],)).rowcount
            print(f"Assigned {count} previously unowned uploads to {args.username}")
            return
    print(f"{args.command} completed for {args.username}")


if __name__ == "__main__":
    main()
