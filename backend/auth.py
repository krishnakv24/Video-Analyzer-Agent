"""Auth for Frame."""

import hashlib
import secrets
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pwdlib import PasswordHash
from .db import db_connection
from .schemas import Login, PasswordChange

router = APIRouter()
password_hash = PasswordHash.recommended()
COOKIE_NAME = "frame_session"
SESSION_SECONDS = 7 * 24 * 60 * 60


def authenticate(request: Request, csrf: bool = False):
    token = request.cookies.get(COOKIE_NAME, "")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with db_connection() as db:
        row = db.execute("""SELECT u.*, s.csrf_token FROM auth_sessions s
            JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?
            AND s.expires_at > unixepoch() AND u.is_active = 1""", (digest,)).fetchone()
    if row is None:
        raise HTTPException(401, "Sign in required")
    if csrf and not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), row["csrf_token"]):
        raise HTTPException(403, "Invalid CSRF token")
    return row


def owner(request: Request):
    return authenticate(request, request.method not in {"GET", "HEAD", "OPTIONS"})["id"]


async def protect_api(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in {"/api/health", "/api/auth/login", "/api/me", "/api/auth/logout", "/api/auth/change-password"}:
        return await call_next(request)
    try:
        user_id = owner(request)
        parts = path.strip("/").split("/")
        with db_connection() as db:
            if len(parts) >= 3 and parts[1] == "uploads":
                row = db.execute("SELECT user_id FROM uploads WHERE id = ?", (parts[2],)).fetchone()
                if row is None or row["user_id"] != user_id:
                    raise HTTPException(404, "Upload not found")
            if len(parts) >= 3 and parts[1] == "jobs":
                row = db.execute("""SELECT u.user_id FROM jobs j JOIN uploads u ON u.id = j.upload_id
                    WHERE j.id = ?""", (parts[2],)).fetchone()
                if row is None or row["user_id"] != user_id:
                    raise HTTPException(404, "Job not found")
            if len(parts) >= 3 and parts[1] == "sessions":
                row = db.execute("""SELECT u.user_id FROM jobs j JOIN uploads u ON u.id = j.upload_id
                    WHERE j.id = ?""", (parts[2],)).fetchone()
                if row is None or row["user_id"] != user_id:
                    raise HTTPException(404, "Session not found")
        request.state.user_id = user_id
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@router.post("/api/auth/login")
def login(payload: Login, request: Request):
    with db_connection() as db:
        user = db.execute("SELECT * FROM users WHERE username = ?", (payload.username.strip(),)).fetchone()
        if user is None or not user["is_active"] or not password_hash.verify(payload.password, user["password_hash"]):
            raise HTTPException(401, "Invalid username or password")
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM auth_sessions WHERE expires_at <= unixepoch()")
        db.execute("INSERT INTO auth_sessions(token_hash, user_id, csrf_token, expires_at) VALUES (?, ?, ?, unixepoch() + ?)",
                   (hashlib.sha256(token.encode()).hexdigest(), user["id"], csrf_token, SESSION_SECONDS if payload.remember else 12 * 60 * 60))
    response = JSONResponse({"id": user["id"], "username": user["username"], "csrf_token": csrf_token})
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/",
                        max_age=SESSION_SECONDS if payload.remember else None)
    return response


@router.get("/api/me")
def me(request: Request):
    user = authenticate(request)
    return {"id": user["id"], "username": user["username"], "csrf_token": user["csrf_token"]}


@router.post("/api/auth/logout")
def logout(request: Request):
    authenticate(request, True)
    token = request.cookies.get(COOKIE_NAME, "")
    with db_connection() as db:
        db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
    response = JSONResponse({"status": "signed_out"})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.post("/api/auth/change-password")
def change_password(payload: PasswordChange, request: Request):
    user = authenticate(request, True)
    if not password_hash.verify(payload.current_password, user["password_hash"]):
        raise HTTPException(401, "Current password is incorrect")
    with db_connection() as db:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash.hash(payload.new_password), user["id"]))
        db.execute("DELETE FROM auth_sessions WHERE user_id = ? AND token_hash != ?",
                   (user["id"], hashlib.sha256(request.cookies[COOKIE_NAME].encode()).hexdigest()))
    return {"status": "changed"}
