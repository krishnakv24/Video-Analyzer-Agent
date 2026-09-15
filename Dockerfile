ARG BASE_IMAGE=videolens-base:1.0.0
FROM ${BASE_IMAGE}

ARG BASE_IMAGE
LABEL org.videolens.image-kind="application" \
    org.videolens.base-image="${BASE_IMAGE}"

WORKDIR /app

# Dependencies already exist in the loaded base. Only current application code
# and its startup configuration are added during release packaging.
COPY main.py manage_users.py cleanup_data.py ./
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Compose can override this identity to match the host data directory's owner.
USER 1000:1000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3); assert json.load(r)['status'] == 'ok'"]

# Upload locks and background preparation currently require one backend process.
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
