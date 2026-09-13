# Frontend, backend, and internal algorithm service

This repository contains `frontend/` (browser UI) and `backend/` (public FastAPI service). Keep the detection algorithms and agent in their **own project and deployment**. The browser communicates only with the public backend; the algorithm service has internal network visibility and is called by the backend or its worker.

```text
Browser frontend
    │ upload chunks, chat, session status
    ▼
Public FastAPI backend ─── session ownership, SQLite, video/image storage
    │ session ID + storage key + task metadata
    ▼
Internal algorithm FastAPI service ─── preprocessing, detection, agent
    │ status + answer + result-image keys
    ▼
Public backend ─── saves response and serves authorized results to browser
```

The **public backend** owns users, permissions, uploads, sessions, messages, and browser-visible result URLs. The **algorithm service** owns model code, frame extraction, detection indexes, and agent execution. Both identify a task by the same `session_id`; their databases remain separate. The algorithm service should never use browser cookies for its internal API. Protect that API with a service credential and private network access.

For 24-hour videos, pass a `video_key` that both services can resolve from shared storage. The current prototype writes to `data/videos/` on one machine, so it cannot yet send a usable storage key to a separately deployed service. For a local two-service test, mount a shared directory into both processes and agree on a relative key. For deployment, object storage is a better shared boundary. The browser's existing chunked upload can remain while the storage implementation changes.

Agree on an internal contract before wiring calls. A small starting contract is:

```http
POST /internal/analyses
Authorization: Bearer <service credential>
Content-Type: application/json

{"session_id":"...", "video_key":"videos/...", "entities":["People"], "instructions":"Find this person"}
```

Return `202 Accepted` with an internal task ID. The public backend polls an internal status endpoint or receives an authenticated callback. On completion, return structured result metadata such as `status`, `answer`, `timestamps`, and `image_keys`. The public backend stores the answer and makes result images available through its owner-checked image route. Follow-up chat calls should carry the same `session_id`, new question, and uploaded image keys; the algorithm service can reuse its preprocessing state. Do not include the full video in the request body or agent prompt.

Agent integration is deferred. Once the other project's endpoint names, authentication method, and shared storage are known, add one client module under `backend/` and call it from a worker. Keep endpoint-specific HTTP code out of upload and chat handlers. Heavy analysis should be a durable background job rather than work done inside the request handler.
