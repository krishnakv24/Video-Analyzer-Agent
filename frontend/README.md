# VideoLens video upload prototype

VideoLens is the browser-facing name. Existing internal names such as `frame_session`, `frameUploadDraft`, and `FRAME_DATA_DIR` remain unchanged so current login, resume, and storage data stay compatible.

Start with the [architecture HLD](../docs/architecture.md) for the Ubuntu, Docker Compose, and host-storage design. The [frontend LLD](../docs/design/frontend-design.md) covers screens, screenshots, and interactions; the [backend LLD](../docs/design/backend-design.md) covers APIs, session mapping, and class diagrams.

For Docker Compose on WSL or Ubuntu, use the [Docker deployment guide](../docs/deployment/docker.md). The container includes Python dependencies and `ffprobe`; it serves this frontend and the backend together and stores data in the configured host directory. No local virtual environment is needed for Docker.

For local development without containers, run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

For Bash on Linux, macOS, or Git Bash on Windows, run `bash setup.sh`. The script creates or reuses `.venv`, installs `requirements.txt`, and prints the activation command for your platform. Run that printed `source .../activate` command in your shell before starting Uvicorn; executing a setup script cannot activate the environment in its parent shell.

To run the API tests, install `requirements.txt`, then run `python -m unittest discover -s tests -v` from the repository root.

Open `http://127.0.0.1:8000/` for the page and `http://127.0.0.1:8000/docs` for the API. The page must be opened through FastAPI to upload files; opening `index.html` directly will not provide the API.

**Test on a phone on the same network:** activate the virtual environment on the computer. If the local server is already using port 8000, stop it and restart from the repository root with:

```shell
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

Connect the phone and computer to the same Wi-Fi network. Find the computer's LAN address with `ipconfig` on Windows (the Wi-Fi adapter's IPv4 address) or `hostname -I` on Ubuntu. On the phone, open **`http://<computer-LAN-IP>:8000`**, for example `http://192.168.1.25:8000`. `localhost` on the phone points to the phone itself; `0.0.0.0` is the server's listening address, not the address to enter in the browser. If access is blocked, allow inbound TCP port 8000 for the private network in the computer's firewall and check that the Wi-Fi network permits devices to communicate. This is a local-network test; it does not require router port forwarding.

The UI and API load from the same address, using relative `/api/...` URLs, so no CORS change is needed. Phone-selected video bytes travel over Wi-Fi to the computer's backend storage. On plain LAN HTTP, secure-context features such as browser notifications and Web Locks may be unavailable; the upload flow can run without them. Use HTTPS for deployment beyond this local test.

**Screen layout:** desktop shows a sidebar; at 900 px wide and below, **Show conversations** expands the navigation below the compact header. Selecting a conversation/draft or New conversation collapses it; Escape also closes it. The list scrolls vertically, and phone upload/chat controls wrap to fit. Normal vertical page scrolling keeps long forms and conversation content reachable; fitting the screen does not mean every control must appear without scrolling.

The compact type scale uses installed system fonts with no external font requests: 14 px interface text, 15 px conversation text with 1.55 line-height, and 16 px editable inputs at default browser sizing. Conversation content is capped at 880 px; workspace titles use 22–26 px, with tighter card spacing and 44 px main controls. These are VideoLens design choices rather than measured Gemini or ChatGPT styling; rem-based text still follows browser font preferences.

The upload regression suite is [tests/frontend_uploads.cjs](../tests/frontend_uploads.cjs). With the app running and Playwright plus Microsoft Edge available, run `node tests/frontend_uploads.cjs`. It passed with mocked API responses for parallel byte transfers, independent prompts, refresh/resume, legacy records, and cross-tab locking. It does not benchmark a 24-hour video or measure Wi-Fi/server throughput. The script header documents optional browser/module paths. The suite also checks failure/reset isolation, fresh-file retry, queued cleanup after reconnect, mobile popup layout, and recovery after a lost session-creation response. [tests/frontend_notifications.cjs](../tests/frontend_notifications.cjs) checks notification deduplication and stale status handling with mocked API responses and browser alerts. [tests/test_upload_discard.py](../tests/test_upload_discard.py) checks backend discard guards and rollback using temporary data.

Run `node tests/frontend_layout.cjs` for the [responsive layout checks](../tests/frontend_layout.cjs) with the same browser prerequisites. Checks passed at widths from 320 to 1,280 px and 844 × 390 landscape, including long text/images, navigation, and the deletion dialog. Resizing preserves the active conversation, unsent question, pending images, and upload progress. These mocked Edge viewport checks do not test physical iOS/Android keyboards or a real phone's LAN connection. The script generates current screenshots with sample/stress-test content: [mobile login](../docs/design/screenshots/responsive-login-mobile.png), [mobile workspace](../docs/design/screenshots/responsive-workspace-mobile.png), and [desktop workspace](../docs/design/screenshots/responsive-workspace-desktop.png).

The project keeps browser and server code separate:

```text
frontend/           HTML, CSS, JavaScript, and browser assets
backend/auth.py     Sign-in, session cookies, and access control
backend/uploads.py  Resumable video upload API
backend/jobs.py     Video sessions and preprocessing
backend/conversation_deletion.py  Conversation deletion and staged media cleanup
backend/images.py   Image upload and protected image delivery
backend/chat.py     Conversation API
backend/video_metadata.py  Local checksum and duration extraction
backend/db.py       SQLite schema and connection management
backend/config.py   Storage paths and size limits
main.py             FastAPI app entry point
manage_users.py     Local account administration command
```

The planned algorithm and agent system will be a **separate, internally reachable FastAPI project**. This repository contains the browser and the user-facing backend only. `backend/video_metadata.py` handles local upload integrity and optional duration extraction; it is not the detection algorithm. The user-facing backend owns accounts, sessions, uploads, and chat. It should send a session ID, selected entities, prompt, and a **shared video object key** to the internal algorithm service. The algorithm service should read the video from shared storage, run detection and agent logic, and return status, text, and result-image object keys. The user-facing backend would then save that response against the session and serve the images to the signed-in user. Do not send a 24-hour video's bytes inside a JSON request or assume that a local path on this server exists in the other project.

The service-to-service request format, authentication, and shared storage location still need to be agreed with the algorithm project's actual API. Agent integration is deferred; long-running video work belongs in a worker rather than an HTTP request.

See [docs/architecture.md](../docs/architecture.md) for the deployment topology and the reserved Multiagent Service boundary.

The browser sends video data in 8 MiB chunks. To continue an interrupted upload, select its **Resume upload** draft in the sidebar, reselect the original video if needed, and resume that draft. Its filename, size, and last-modified time must match the saved selection; these checks do not prove content equality. FastAPI stores file bytes in `data/videos/` and upload/job metadata in `data/frame.sqlite3`. Set `FRAME_DATA_DIR` to move these files to a disk with enough free space. `data/` is ignored by Git. The configured maximum is 250 GiB per video; adjust `MAX_VIDEO_SIZE` in `backend/config.py` for your deployment.

If session creation fails or its response is lost, the page first checks for a saved conversation with that upload ID. A match opens the existing conversation without retransmitting the video. If recovery fails, a **Video upload failed** popup explains the reason and clears only that draft and its file selection. **Choose video again** restores the prompt and filters and asks for a fresh file selection; retry starts a new upload ID. Other uploads continue.

For failed uploads with a known server ID, the browser requests `DELETE /api/uploads/{upload_id}`. The backend refuses to discard any upload linked to a saved conversation. Unsuccessful cleanup remains under `frameUploadCleanup:<userId>:<draftId>` when browser storage is available, retries on reconnect and every 15 seconds while signed in with the page open, and restores after login. A cleanup warning means some quarantined files need administrator attention. Refresh interruptions still restore resumable drafts; a reported upload failure instead uses the fresh-retry flow.

You can start another conversation while a video is still uploading in the current tab. Each upload started from **New conversation** receives a random draft ID and a separate server upload ID, including videos with identical names, sizes, and modification times. Each appears in the sidebar with its own percentage; opening another conversation does not stop it. Different videos advance concurrently while each video's chunks remain sequential. Keep the tab open while transferring.

Draft metadata, filters, instructions, progress, and its upload ID are stored under a per-user `frameUploadDraft:<userId>:<draftId>` key; video bytes and browser `File` objects are not stored there. After refresh, the sidebar restores saved drafts as **Resume upload** rows. Select the intended row before reselecting the file. Successful session creation removes only that draft's saved key. Legacy filename-based resume entries migrate to explicit paused drafts. Where the browser supports Web Locks, the same draft cannot be transferred from two tabs at once; different drafts remain independent. Keep a resumed draft in one tab when Web Locks is unavailable.

After the server saves the complete upload, it creates a unique `session_id` (the job UUID). Each session has exactly one video. The page notifies the user when upload completes. Initial preprocessing then runs on the backend: it streams the saved file to compute a SHA-256 checksum and reads duration when `ffprobe` is installed. The browser polls all tracked preparing jobs, including background conversations, and notifies once when each becomes ready. Upload-saved and preparation-ready alerts are separate events, deduplicated per user/job/event using browser storage and Web Locks when available. Opening or reloading an already-ready conversation stays quiet. Browser notifications require permission and an open page; unsupported notification APIs cannot fail a saved upload. Chat opens when the session is ready, stores follow-up messages on the server, and can answer basic file metadata questions. **Visual detection and AI question answering are not connected yet.** The browser does not process video content; slicing into chunks is only for transfer.

After a session is created, use the **+** button beside the chat input to attach PNG, JPEG, WebP, or GIF images (up to 20 MiB each, 10 per message). Images appear as small previews beside the unsent question. Press **Send** to upload the images and attach them to that specific message. The server validates and saves them in `data/images/<session_id>/`, records the message association in SQLite, and shows them inside the sent chat bubble. Image understanding is not connected yet.

The chat can display images attached to stored messages. Producing assistant images from the separate algorithm service is deferred until that service is integrated.

After a successful video upload, the Analyze control reads **Video uploaded** and stays disabled for that session. **Chat** becomes available when backend preprocessing reaches `ready`; it jumps to the conversation input. An upload failure clears the failed selection and restores the Analyze control; use **Choose video again** in the reason popup to retry. Use **New conversation** to upload a different video into a new session.

To delete a saved conversation, right-click its sidebar row, use its **ellipsis** menu, or focus the conversation button and press **Shift+F10**. Choose **Delete conversation**, then confirm or cancel. Confirmation removes that conversation's video, registered images (including unsent uploads), messages, and session/upload records. Other conversations and accounts remain. Deleting an inactive conversation keeps the current view; deleting the active one opens another saved conversation or New conversation. Preparation must finish before deletion is allowed. The dialog displays **Deleting…** while pending and keeps errors visible for retry. If the success toast warns that some files remain, the conversation is deleted but an administrator must clean up quarantined media; see the [backend recovery contract](../docs/design/backend-design.md#delete-one-conversation).

The account-area icon is **Sign out**, not settings. Signing out keeps saved conversations. An open saved conversation now uses the subtitle “Ask follow-up questions and add reference images to explore your video.”

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

To clear local sessions, uploads, messages, images, and login sessions, stop the API and preview the cleanup with `python cleanup_data.py`. Run `python cleanup_data.py --execute` to apply it. User accounts remain available; add `--include-users` only if you also want to remove every account. The script removes media files referenced by database rows, so take a database and media backup first if the data matters.

This SQLite plus local disk configuration is suitable for one server process and development. Do not run multiple Uvicorn workers against this upload implementation: its upload lock is process-local. For production with multiple servers, store video bytes in object storage through multipart upload and use PostgreSQL for upload records, jobs, users, and detection metadata. Give workers the object key or video ID; do not put 24-hour video bytes in the database or agent prompt.

Docker Compose is also the Ubuntu server deployment path. Keep one application container and one Uvicorn worker, with SQLite, videos, and images in the same host directory mounted at `/data`; see the [deployment details](../docs/architecture.md#5-docker-compose-deployment).
