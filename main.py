"""FastAPI entry point; domain code lives in backend."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.auth import password_hash, protect_api, router as auth_router
from backend.chat import router as chat_router
from backend.config import BASE_DIR, IMAGE_DIR, UPLOAD_DIR
from backend.db import db_connection, initialize_db
from backend.images import router as images_router
from backend.jobs import resume_pending_preprocessing, router as jobs_router
from backend.uploads import router as uploads_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await resume_pending_preprocessing()
    yield


app = FastAPI(title="Frame Video Analyzer", lifespan=lifespan)
app.middleware("http")(protect_api)
for router in (auth_router, uploads_router, jobs_router, images_router, chat_router):
    app.include_router(router)

# Keep the mount last so API and documentation routes take precedence.
app.mount("/", StaticFiles(directory=BASE_DIR / "frontend", html=True), name="frontend")
