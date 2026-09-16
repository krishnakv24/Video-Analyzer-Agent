# Agent 1: preparation, shared Brain, and model workers

**Status: design only; not implemented.** Multiple video sessions can progress concurrently through one agent service. Agent 1 calls the GPU Brain for preparation planning and summaries. Chat and Agent 2 remain deferred. The user's tested ONNX face detector and feature extractor are selected for CPU execution; the GPU remains reserved for the Brain.

**Filter first:** face processing, captioning, indexing, and visual summaries use only retained frames. Faces are mandatory on every retained frame. The initial Brain planning call receives metadata and user selections, not video frames.

## 1. Complete flow with model names

The named models are the proposed evaluation baseline. Runtime compatibility, accuracy, memory, and throughput must pass validation before a release.

```mermaid
flowchart TD
    A["Video A / Session A"] --> Q["FastAPI: durable job queue"]
    B["Video B / Session B"] --> Q
    C["More videos / separate sessions"] --> Q
    Q --> S["Fair scheduler: admit up to N active jobs"]
    S --> P["BRAIN CALL 1: preparation plan<br/>Qwen3-4B-Instruct-2507 / GPU"]
    P --> V["Validate plan against policy and model registry"]
    V --> M["Model Manager: acquire or load approved CPU models"]
    M --> D["FFmpeg: stream each video's candidates"]
    D --> O["Optional OpenCV MOG2 motion gate<br/>YOLOX-Nano: selected-entity filter / CPU"]
    O -->|Rejected| X["Release frame; continue filtering"]
    O --> R["Retained frames with session and timestamp"]
    R --> F["User's tested ONNX face detector + feature extractor / CPU<br/>Mandatory branch for every retained frame"]
    R --> T["SmolVLM-500M captions / CPU"]
    T --> E["all-MiniLM-L6-v2 text embeddings / CPU"]
    F --> J["Join by session, evidence and frame ID"]
    E --> J
    J --> W["MinIO + Chroma: persist each session's evidence"]
    W --> K["After all chunks commit:<br/>BRAIN CALL 2: section and overall summaries<br/>Same Qwen3-4B GPU instance"]
    K --> Z["Publish per-session manifest and notify completion"]
    classDef brain fill:#ede9fe,stroke:#7c3aed,stroke-width:2px,color:#1f2937
    classDef manager fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1f2937
    classDef vision fill:#dcfce7,stroke:#15803d,color:#1f2937
    class P,K brain
    class M manager
    class O,F,T,E vision
```

The middle pipeline streams bounded chunks independently for each session; it does not load entire videos into RAM. Each accepted frame reaches both branches. Rejected frames end only their own processing. Empty chunks still checkpoint; a video with no retained frames skips summary inference and publishes an explicit empty result.

Purple boxes are Brain calls, blue is model loading/reuse, and green is CPU inference. Both Brain boxes refer to **one shared Brain runtime**. Neither the model nor its conversation state is copied for every uploaded video. Model Manager acquisition also runs at each stage when a model lease is needed; the diagram shows its first use.

## 2. Proposed models and runtimes

| Step | Baseline model or tool | Runtime / device | Role |
| --- | --- | --- | --- |
| Planning and summaries | **Qwen/Qwen3-4B-Instruct-2507**, validated GGUF `Q4_K_M` export | llama.cpp with CUDA / GPU | Propose an approved preparation plan; summarize retained evidence. One shared Brain. |
| Decode and timestamps | **FFmpeg / libavcodec** | Native C/C++ / CPU | Decode candidates incrementally; preserve source timestamps. No AI model. |
| Optional motion gate | **OpenCV MOG2** | OpenCV C++ / CPU | Motion/change filtering for configured footage profiles; separate background state per video. |
| Entity filter | **YOLOX-Nano**, `yolox_nano.onnx` | ONNX Runtime C++ / CPU | People, cars and other supported selected classes. |
| Face detection | **User's tested face detector, ONNX export** | ONNX Runtime `CPUExecutionProvider` / CPU | Reuse the existing detector and run the mandatory face branch on every retained frame. Exact artifact path/version is recorded when integrating the supplied ONNX file. |
| Face features | **User's tested face feature extractor, ONNX export** | ONNX Runtime `CPUExecutionProvider` / CPU | Preserve the tested feature-vector and preprocessing contract. Do not replace it with a different face model. |
| Frame captions | **HuggingFaceTB/SmolVLM-500M-Instruct**, compatible GGUF plus projector | llama.cpp multimodal / CPU | Caption each retained frame; use a pinned model/projector pair. |
| Caption vectors | **sentence-transformers/all-MiniLM-L6-v2**, validated ONNX export | ONNX Runtime C++ / CPU | Embed captions into a separate text collection. |
| Storage and orchestration | **MinIO, Chroma, SQLite, FastAPI, LangGraph** | Host/container services | Storage, jobs, workflow and APIs; these are not AI models. |

The Brain is a text instruction model, not the visual captioner. Its official card lists local llama.cpp support. The proposed GPU profile starts evaluation with one generation slot and a bounded 4,096-token context, including output allowance. This is a target for the previously reported 6 GB GPU, **not a verified fit or latency claim**. Model quantization, KV cache, runtime buffers and available VRAM must be measured. [Qwen model card](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)

YOLOX provides a Nano ONNX artifact for the proposed CPU entity filter. Validate detector recall on the actual CCTV footage, since frames missed here are unavailable to later stages. [YOLOX ONNX](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/demo/ONNXRuntime/README.md)

### Existing face models: ONNX reuse

The user confirmed that ONNX versions of the tested face models are available and selected **CPU execution**. These models replace the previous face-model proposals. Preserve their weights, preprocessing, crop/alignment rules, feature dimensions, normalization and matching thresholds; do not introduce a new face architecture or untested quantization.

The inspected [source repository](https://github.com/krishnakv24/pedestrian_analysis_triton_inference_server/tree/7d466cd03d9296f75a430d9ec11291857dabebfe/model_repository), commit `7d466cd03d9296f75a430d9ec11291857dabebfe`, contains TensorRT artifacts. The confirmed ONNX exports have not been retrieved in this checkout. Their filenames, hashes, input/output names and runtime compatibility must come from those ONNX files and the user's tested preprocessing/feature code, not inferred from the TensorRT configuration. No model has been downloaded or run here.

Configure the face adapter with `CPUExecutionProvider` explicitly and report incompatible operators as an integration failure; do not silently switch to CUDA. Model Manager shares these CPU instances across sessions. The mandatory face branch must cover every retained frame, including Cars-only selections; if reusing person-crop processing, verify that its crop policy preserves the required coverage. Store actual feature vectors under a distinct face index with the tested model/version metadata.

SmolVLM supports image descriptions and is listed by llama.cpp. Its CPU runtime must disable both language-layer and multimodal-projector GPU offload; use a CPU-only worker build with explicit `-ngl 0 --no-mmproj-offload`. Keep its input-image and output-token budgets bounded. Its captions are candidates for evaluation, not guaranteed observations. [SmolVLM model card](https://huggingface.co/HuggingFaceTB/SmolVLM-500M-Instruct), [official GGUF artifacts](https://huggingface.co/ggml-org/SmolVLM-500M-Instruct-GGUF), [multimodal runtime](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)

MiniLM generates 384-dimensional text vectors. Preserve its tokenizer, attention-mask-aware mean pooling, and normalization in the native adapter. Keep captions within its 256-wordpiece limit or split with frame-linked references instead of silently truncating. This baseline uses English captions. [MiniLM model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)

These choices replace the earlier unspecified model roles. Pin artifact hashes, export/quantization settings, runtime revisions, preprocessing, licenses, and quality thresholds in the registry. Model weights are packaged or provisioned before service use; the application does not download arbitrary models during a request.

## 3. Exactly where the Brain is called

| Call | Caller and input | Result and enforcement |
| --- | --- | --- |
| **1. Preparation planning** | LangGraph `plan_preparation` node calls `BrainClient.plan()` after durable job acceptance and source validation. Input: metadata, `entities`, `instructions`, profile limits, approved capability/model IDs. | Structured proposal containing approved model aliases and caption/summary instructions. `PlanValidator` enforces selections, mandatory face/caption/text stages, device limits and supported capabilities. Persist the resolved plan and hash before processing. |
| **2. Section summaries** | `summarize_sections` calls `BrainClient.summarize()` on bounded chronological groups of retained captions, detector metadata, timestamps and evidence IDs after their writes commit. | Evidence-linked section summaries. Validate returned references and output schema; preserve source records. |
| **3. Overall summary** | `summarize_video` calls the **same Brain** on section summaries and their evidence references. Use intermediate levels if the input exceeds the context budget. | Overall summary linked to the sections and retained evidence; save before publication. |

The flow diagram groups calls 2 and 3 as the summary phase. They can require many bounded inference requests for a 24-hour video. Never send the full video, discarded frames, raw embeddings, or all captions in one unbounded prompt.

The orchestrator calls the private Brain runtime through `BrainClient`; the browser and existing backend call only the agent FastAPI. Plan and summary requests have their own scoped inputs. Reusing weights does not mean sharing prompts, KV-cache conversations, or results between users.

The graph has fixed stage boundaries. The Brain can propose choices from the configured catalog; it cannot create tools, install models, execute commands, drop mandatory stages, change ownership, or bypass the filter. Invalid proposals receive a bounded repair attempt and then a visible failure. A committed plan is reused on retry rather than asking the Brain to choose a different pipeline mid-job.

For People and Cars, the proposed CPU catalog resolves to the **same YOLOX model** with an OR class mask, plus the user's tested ONNX face pipeline, SmolVLM and MiniLM. There is no separate person-model and car-model copy. New specialized capabilities require an approved registry entry and adapter before a Brain proposal can use them.

## 4. Multiple videos at the same time

| Shared resource | Per-session state |
| --- | --- |
| One agent FastAPI and durable scheduler | One finalized video, preparation job, entity selection and immutable plan |
| One loaded runtime per approved model/revision/device configuration | Separate decoder cursor, MOG2 background state, graph checkpoint and worker lease |
| Fair CPU-model queues and one GPU Brain generation slot initially | Bounded frame queues, branch outcomes, progress, cancellation and errors |
| One batched storage writer initially | Separate object keys, index metadata, summaries and completion events |

Proposed `MAX_ACTIVE_PREPARATIONS=2` is an initial test setting, configurable for the host. Admission also checks measured RAM, decoder buffers, model workspaces and queue budgets. Additional jobs remain `queued`; running jobs can wait for a shared model without losing progress. On a smaller host, reduce admission to one. Reducing active videos does not remove the resident models' fixed memory costs.

Each admitted video has one decoder owner, so frames remain ordered within that video. Model workers take small bounded requests from sessions in round-robin order. A 24-hour video cannot monopolize the caption queue by submitting its entire frame list first. Queues have per-session and global byte/item limits, with backpressure to the relevant decoder.

Start with one worker per CPU model key and one Brain generation slot. Face and caption work may overlap within the CPU/RAM budget. Shared model calls from A and B can be interleaved. Batch only when the pinned runtime supports it, keeping item identity explicit. Concurrent sessions do not imply unlimited parallel inference or unchanged per-video latency.

Do not instantiate models inside each LangGraph graph or FastAPI request. A shared broker owns runtime processes/instances and CPU permits. Bound native decoder/OpenCV/ONNX/llama.cpp threads under the container's CPU allocation and retain capacity for uploads, polling, cancellation and storage. [ONNX Runtime thread management](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)

Every task/result carries `owner_id`, `session_id`, `job_id`, `evidence_id`, `frame_id` when applicable, pinned model identity, and the current attempt token. Join by these identifiers, never by arrival order. Session A finishing or cancelling must not unload a model still being used by B.

## 5. Dynamic model loading

```mermaid
flowchart TD
    A["Stage requests an approved model key"] --> B["Model Manager checks registry and budget"]
    B --> C{"Already loaded and healthy?"}
    C -->|Yes| D["Acquire usage lease; reuse runtime"]
    C -->|No| E{"Same model already loading?"}
    E -->|Yes| F["Wait for the existing load"]
    E -->|No| G["Reserve peak memory; evict idle models if needed"]
    G --> H["Load local artifact once and warm up"]
    H -->|Success| D
    H -->|Failure| X["Release reservation; report load failure"]
    F -->|Ready| D
    F -->|Failed| X
    D --> I["Run bounded inference for this session"]
    I --> J["Release usage lease after inference stops"]
    J --> K["Cache while useful; unload only when idle"]
```

- **Registry:** maps a logical model ID to an approved local artifact hash/revision, runtime, precision, device, compatible preprocessing/projector, capabilities, and memory/workspace budget. The validated plan resolves aliases once and persists exact identities.
- **Load once:** key instances by the complete runtime configuration. An atomic per-key lock shares one load future across simultaneous requests from A and B. Readiness follows hash/compatibility checks and warm-up, not merely opening a file.
- **Reserve before loading:** account for resident models, peak load memory, active requests, decoder/queue buffers and service headroom. If no safe capacity exists, wait within a bound or return a resource error. Never start an unbounded number of loads.
- **Reuse safely:** an inference lease/refcount prevents unloading while a call is queued for that instance or executing. Release model leases and CPU execution permits after each bounded native call, before waiting on storage or another model. Completion and lease-release handling must remain runnable even when input queues are full.
- **Unload only idle models:** after no active leases, apply an idle timeout or least-recently-used eviction when memory is needed. Keep common CPU models warm when possible. Under pressure, pause decoders and drain work before evicting; do not reload all models for each frame.
- **Brain residency:** pin a warmed Brain on GPU while the selected vision models, including the user's ONNX face models, run on CPU. CPU-model eviction cannot take its reserved GPU memory. All active sessions share the Brain's bounded inference queue.
- **Failure/restart:** persist desired model identities and job state, not process pointers. Recover loads on demand, replay idempotent stage tasks, and reject stale results. A failed load affects requesting work and is visible; no silent model substitution.
- **Version changes:** new jobs may use a new approved profile. In-flight jobs retain their exact versions; if old and new runtimes cannot coexist in memory, schedule them separately instead of silently replacing one.

For this baseline, dynamic loading primarily saves idle resources and shares models across sessions. It does not require choosing a new model for every frame. GPU fallback to a cloud provider is not silently activated; a later explicit provider configuration can implement that boundary.

The selected CPU path loads the user's ONNX models into shared ONNX Runtime instances. It does not require the source repository's Triton/TensorRT deployment. The existing model files are provisioned separately, then pinned and loaded through the same Model Manager lifecycle.

## 6. Filtering and retained evidence

The existing UI sends `entities` and `instructions` after upload. Supported options are `People`, `Cars`, `Motorcycles`, `Bicycles`, and `Animals`; the list is nonempty. Brain planning preserves this selection.

The proposed source profile uses 900-second checkpoint chunks and up to 6 candidate frames per second, subject to evaluation. Optional motion gating belongs to the fixed profile; the Brain cannot weaken filtering to meet a speed target. Every retained frame must match at least one selected supported class. Periodic candidates still pass the same gate.

`Animals` maps to the detector's explicit supported list: bird, cat, dog, horse, sheep, cow, elephant, bear, zebra and giraffe. It does not cover arbitrary animal species. [YOLOX class list](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/yolox/data/datasets/coco_classes.py)

With Cars-only selection, every retained car frame still enters the user's tested ONNX face-detection branch, with the tested feature extractor used for usable faces. A face-only frame rejected by the filter remains excluded. `no_faces` is a valid completed frame outcome; a failed required model is a capability failure, not a fabricated embedding or successful no-face result.

### Cars, animals, and other selected objects

The category detector and the face pipeline have different outputs. YOLOX supplies selected object labels, boxes and confidence; face models supply face detections and, once verified, face-specific embeddings. For each retained frame, save object detection IDs, original timestamp, class, score and original-image box alongside the retained image. Captions and summaries use these retained observations.

| UI selection | Detector mapping | Preparation output |
| --- | --- | --- |
| People | `person` | Person boxes/timestamps; mandatory face branch still runs on the retained frame. |
| Cars | `car` | Car boxes/timestamps and frame captions. Bus/truck are separate classes unless explicitly added to the UI mapping. |
| Motorcycles / Bicycles | `motorcycle` / `bicycle` | Category-specific boxes/timestamps and captions. |
| Animals | Explicit supported animal classes listed above | Species/category label where supported, boxes/timestamps and captions. |

For Cars + Animals, keep a qualifying frame when either selected category is detected. Reuse accepted detections for retained-frame metadata instead of inventing features from face models. Any later object crop must come from a retained image; this rule never authorizes source-video rescans.

The supplied repository also has `person_detection`, described as YOLOv8n, with output `[84,8400]`. Its client currently reads only class-score index 0. This is consistent with a four-box-coordinate plus 80-class detector, so it is a candidate for broader category reuse **after confirming the engine's labels and testing selected-class decoding and class-aware NMS**. The configuration shape alone does not certify the weights' class capabilities. Its current TensorRT artifact also needs GPU; the existing CPU YOLOX proposal remains until that route is chosen. [Detector configuration](https://github.com/krishnakv24/pedestrian_analysis_triton_inference_server/blob/7d466cd03d9296f75a430d9ec11291857dabebfe/model_repository/person_detection/config.pbtxt)

### Future matching: direct face lookup and object candidate search

This is the agreed retrieval design for later integration; Agent 2 and chat are not implemented in this phase.

| Reference image | Planned search path |
| --- | --- |
| Face crop | Extract a query vector with the same tested ONNX face model and preprocessing, then search the authorized session's published face vectors directly. No summary gate. Apply the evaluated quality and matching thresholds. |
| Car, animal or other supported object | Describe the reference image, retrieve likely retained frames, then compare object crops within those candidates using a separate evaluated object matcher. Face embeddings are not object embeddings. |

For a car image and the question "When does this car appear?":

1. Run the reference image and question through the shared CPU **SmolVLM** worker to propose visible attributes such as `category: car`, `color: red`, and `body_type: SUV`. Unknown or uncertain attributes remain unset. This is a proposed use requiring evaluation, not a guaranteed attribute recognizer. The text-only Qwen Brain can interpret the attributes and question; it does not receive the image.
2. Search timestamped frame captions and section summaries, together with structured entity occurrence records. Combine semantic candidates with category observations; use color/type as ranking hints, not mandatory exclusion rules. A summary may omit a visible vehicle, and lighting may change its apparent color.
3. Resolve candidates to retained `frame_id` values and detection boxes. Compare the candidate vehicle crops against the reference using an approved CPU object-appearance matcher. Its model choice and thresholds require evaluation before implementation. A summary hit only identifies a candidate; even visual similarity may leave multiple plausible vehicles.
4. Return the supported matching candidates with original video timestamps and retained-image evidence. If candidates are missing or weak, widen the search to other retained category observations and then remaining retained frames within a configured query budget. Never decode rejected frames or rescan the original video. Report incomplete search coverage when the budget is exhausted.

Agent 1 must persist an entity occurrence lookup containing `owner_id`, `session_id`, `evidence_id`, `detection_id`, `frame_id`, original timestamp, category, box and detector score. Frame captions and section summaries must link back to these frames; observed time ranges do not establish continuous presence between frames. Keep category/time lookup in agent SQLite metadata and semantic caption retrieval in Chroma, always scoped to the authorized session and published evidence version. Chroma supports metadata filters for these scoped queries. [Chroma metadata filtering](https://docs.trychroma.com/docs/querying-collections/metadata-filtering)

Object features need not be computed for every frame during preparation. Compute them after candidate selection and cache them by session/evidence, frame, box, model revision and preprocessing contract; keep query caches separate from immutable preparation evidence and include them in session cleanup. The reference image uses its own scoped content/model cache key.

This can reduce matching work when the shortlist is much smaller than the retained-frame set. Measure total query latency, including reference-image inference and retrieval, against direct matching with cached object features. There is no measured speedup yet. Searches only cover retained evidence; an unselected category or an empty result cannot establish absence from the original video.

Only source validation, decoding and filtering receive the video path. Downstream workers accept registered `RetainedFrameRef` records; the Brain receives metadata or retained-derived text. Source hashes are verified/computed by streaming. Preserve original timestamps and frame ordinals, including variable-rate footage.

## 7. Storage, completion and recovery

```text
User -> session_id -> one finalized source video
  -> agent job_id + immutable validated plan
      -> evidence_id
          -> frame_id + timestamp
              -> retained image + entity detections
              -> caption + text vector
              -> face outcomes + usable face crops/vectors
          -> section summaries + overall summary + manifest
```

| Persistent location | Contents |
| --- | --- |
| Backend host data directory | Existing account/session database, original videos and user image uploads |
| Agent read-only `/media/videos` | Source video mount; backend database is not shared |
| Agent persistent `/state` | Jobs, plans, leases, checkpoints, accepted frame IDs, scoped entity occurrence lookup, pending writes and registry publication |
| Agent read-only `/models` | Provisioned model artifacts, projector files and versioned manifests |
| MinIO host-backed storage | Retained images/crops and summaries under `video-chatbot/<session_id>/<evidence_id>/...` |
| Chroma host-backed storage | Separate caption-text and face-vector collections, scoped by owner/session/evidence and embedding version |

The single-host Docker target is the existing web container plus the agent container and private MinIO/Chroma services. The agent supervises orchestration, shared CPU model instances and the resident Brain runtime. The user's ONNX face models use CPU; only the Brain uses CUDA. Model processes are managed centrally, not per session.

A session becomes ready only after all chunks commit, `captioned_frames == face_processed_frames == retained_frames`, required object/vector writes succeed, summaries pass validation, and the immutable manifest is published. No retained frames produces `summary_key: null` and `no_retained_frames`; it is not proof of absence in the original video.

Use durable jobs, request idempotency, leases/fencing, chunk checkpoints and pending-write reconciliation. MinIO, Chroma and SQLite do not share a transaction. Publish the authoritative evidence registry last; consumers cannot expose unfinished versions as complete. Source/model/plan changes cannot silently rewrite published evidence.

Cancellation targets one job, stops its outstanding work, and releases its leases only after calls stop. It leaves shared models and other jobs running. Deletion tombstones the session, quiesces its work and removes its scoped outputs; backend source deletion waits for agent cleanup acknowledgement. Completion notifications are deduplicated by session/evidence.

Host mounts survive container replacement; consistent backups are still required for host disk loss. The current backend performs metadata preparation only. [Backend jobs](../../backend/jobs.py) and [conversation deletion](../../backend/conversation_deletion.py) require integration, and packaging does not yet deploy this design.

## 8. Acceptance before implementation is called complete

Verify two videos make independent progress with shared model instances; concurrent requests load each model once; model waits respect queue/memory limits; cancelling A does not interrupt B; rejected frames never reach downstream stages; faces remain mandatory; invalid Brain plans fail safely; restart preserves pinned plans and frame IDs; and required-stage/model/storage failures cannot publish readiness.

Evaluate entity recall, face quality, caption/summary grounding, queue fairness, load/eviction behavior, RAM/VRAM peaks and processing time on representative long videos. There is no measured 24-hour-video throughput claim yet.

Before enabling the later image-query path, verify that an omitted summary mention or uncertain reference color cannot suppress all category candidates; widening stays within retained evidence and the query budget; and concurrent queries never retrieve another owner's session or an unpublished evidence version.

See the [detailed workflow and class diagram](workflows.md). All named classes and model configurations in these documents are proposed; no agent inference is implemented yet. Detailed API contracts are deferred.
