"""Config for Frame."""

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FRAME_DATA_DIR", BASE_DIR / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "videos"
IMAGE_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "frame.sqlite3"
CHUNK_SIZE = 8 * 1024 * 1024
MAX_VIDEO_SIZE = 250 * 1024 * 1024 * 1024
MAX_IMAGE_SIZE = 20 * 1024 * 1024
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
