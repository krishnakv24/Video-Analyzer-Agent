# Frame video upload prototype

The interface screens, screenshots, interaction states, API call chains, and chat response flow are documented in [docs/design/frontend-design.md](../docs/design/frontend-design.md).

Run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

For Bash on Linux, macOS, or Git Bash on Windows, run `bash setup.sh`. The script creates or reuses `.venv`, installs `requirements.txt`, and prints the activation command for your platform. Run that printed `source .../activate` command in your shell before starting Uvicorn; executing a setup script cannot activate the environment in its parent shell.

To run the API tests, install `requirements.txt`, then run `python -m unittest discover -s tests -v` from the repository root.

Open `http://127.0.0.1:8000/` for the page and `http://127.0.0.1:8000/docs` for the API. The page must be opened through FastAPI to upload files; opening `index.html` directly will not provide the API.

The project keeps browser and server code separate:

```text
frontend/           HTML, CSS, JavaScript, and browser assets
backend/auth.py     Sign-in, session cookies, and access control
backend/uploads.py  Resumable video upload API
backend/jobs.py     Video sessions and preprocessing
backend/images.py   Image upload and protected image delivery
backend/chat.py     Conversation API
backend/video_metadata.py  Local checksum and duration extraction
backend/db.py       SQLite schema and connection management
backend/config.py   Storage paths and size limits
main.py             FastAPI app entry point
manage_users.py     Local account administration command
```

The algorithm and agent system is a **separate, internally reachable FastAPI project**. This repository contains the browser and the user-facing backend only. `backend/video_metadata.py` handles local upload integrity and optional duration extraction; it is not the detection algorithm. The user-facing backend owns accounts, sessions, uploads, and chat. It should send a session ID, selected entities, prompt, and a **shared video object key** to the internal algorithm service. The algorithm service should read the video from shared storage, run detection and agent logic, and return status, text, and result-image object keys. The user-facing backend then saves that response against the session and serves the images to the signed-in user. Do not send a 24-hour video's bytes inside a JSON request or assume that a local path on this server exists in the other project.

The service-to-service request format, authentication, and shared storage location still need to be agreed with the algorithm project's actual API. Agent integration is deferred; long-running video work belongs in a worker rather than an HTTP request.

See [docs/architecture.md](../docs/architecture.md) for the separate-service boundary and a proposed internal request format.

The browser sends video data in 8 MiB chunks. If an upload is interrupted, reselect the **same file in the same browser** and press **Analyze video** to continue. FastAPI stores file bytes in `data/videos/` and upload/job metadata in `data/frame.sqlite3`. Set `FRAME_DATA_DIR` to move these files to a disk with enough free space. `data/` is ignored by Git. The configured maximum is 250 GiB per video; adjust `MAX_VIDEO_SIZE` in `backend/config.py` for your deployment.

If the video reaches the server but session creation fails or its response is lost, retry with the same file. The page reuses the completed upload and finds an existing session instead of transferring the video again.

You can start another conversation while a video is still uploading in the current tab. Each in-progress video appears in the sidebar with its own percentage; opening another conversation does not stop it. Uploads for different video IDs can advance concurrently, while chunks for the same video remain serialized. Keep the tab open until those transfers complete. After a refresh, reselect the same file to resume an interrupted upload.

After the server saves the complete upload, it creates a unique `session_id` (the job UUID). Each session has exactly one video. The page notifies the user when upload completes. Initial preprocessing then runs on the backend: it streams the saved file to compute a SHA-256 checksum and reads duration when `ffprobe` is installed. The browser polls job status and sends another notification when preprocessing finishes; browser notifications are available if the user grants permission while the page is open. Chat opens when the session is ready, stores follow-up messages on the server, and can answer basic file metadata questions. **Visual detection and AI question answering are not connected yet.** The browser does not process video content; slicing into chunks is only for transfer.

After a session is created, use the **+** button beside the chat input to attach PNG, JPEG, WebP, or GIF images (up to 20 MiB each, 10 per message). Images appear as small previews beside the unsent question. Press **Send** to upload the images and attach them to that specific message. The server validates and saves them in `data/images/<session_id>/`, records the message association in SQLite, and shows them inside the sent chat bubble. Image understanding is not connected yet.

The chat can display images attached to stored messages. Producing assistant images from the separate algorithm service is deferred until that service is integrated.

After a successful video upload, **Analyze video** stays disabled for that session. **Chat** becomes available when backend preprocessing reaches `ready`; it jumps to the conversation input. If the upload fails before a session is created, **Analyze video** is enabled again so the user can retry. Use **New conversation** to upload a different video into a new session.

Accounts are created by the server administrator. After installing dependencies, run these from the repository root and enter passwords at the prompt (minimum 12 characters):

```powershell
python manage_users.py create alice --admin
python manage_users.py create bob
python manage_users.py list
python manage_users.py reset-password alice
python manage_users.py disable bob
python manage_users.py enable bob
```

The login verifies an Argon2 password hash. A server-side session is held in an HttpOnly cookie; **Remember me** keeps it for seven days, otherwise the cookie expires when the browser closes (the server also expires it after 12 hours). Sign out revokes the session. Users can change their own password through `POST /api/auth/change-password` with `current_password` and `new_password`. The browser loads its conversation list from the backend on every sign-in and refresh. The selected job ID in browser storage is only a display preference. Uploads, jobs, images, and chat are accessible only to the account that owns the upload.

Uploads created before user management remain unassigned and invisible to all accounts. To assign those existing uploads and their linked sessions to one chosen account, run:

```powershell
python manage_users.py claim-existing alice
```

Run that command only after choosing the correct owner. It claims every currently unassigned upload; it does not transfer videos already owned by another account. Back up `data/frame.sqlite3` before any manual data migration. For access outside localhost, serve through HTTPS and add login rate limiting, quotas, and operational backups.

This SQLite plus local disk configuration is suitable for one server process and development. Do not run multiple Uvicorn workers against this upload implementation: its upload lock is process-local. For production with multiple servers, store video bytes in object storage through multipart upload and use PostgreSQL for upload records, jobs, users, and detection metadata. Give workers the object key or video ID; do not put 24-hour video bytes in the database or agent prompt.
