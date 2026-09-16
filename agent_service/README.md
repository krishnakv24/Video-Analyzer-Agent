# Agent service

This folder contains the **Agent 1 preparation design** for a separate service container. Multiple video sessions use shared model workers and one GPU Brain; the existing web backend calls one agent FastAPI interface.

**Filtering comes first. Visual processing uses only retained frames, and face processing is mandatory on every retained frame.**

## Processing flow

```text
Video A / Session A + Video B / Session B + further videos
  -> Durable queue and bounded, fair scheduler
  -> GPU Brain: propose preparation plan from metadata and selections
  -> Validate plan; Model Manager loads/reuses approved CPU models
  -> FFmpeg + optional MOG2 + YOLOX-Nano selected-entity filtering
  -> Retained frames only
       -> User's tested ONNX face detector + feature extractor / CPU
       -> SmolVLM-500M captions + MiniLM text embeddings
  -> Join per-session results; persist in MinIO / Chroma
  -> Same GPU Brain: section and overall summaries
  -> Publish evidence and notify that session's completion
```

The proposed Brain is Qwen3-4B-Instruct-2507 through llama.cpp on GPU. The user confirmed ONNX versions of their [tested face models](https://github.com/krishnakv24/pedestrian_analysis_triton_inference_server/tree/7d466cd03d9296f75a430d9ec11291857dabebfe/model_repository); use those with ONNX Runtime on CPU. Exact ONNX artifacts and their tested input/output contracts will be recorded during integration. See the [face-model reuse decision](docs/architecture.md#existing-face-models-onnx-reuse).

For People and Cars, qualifying frames containing either category are retained. With Cars-only selection, face processing still runs on every retained car frame. Rejected frames create no downstream evidence; the source remains saved.

The Model Manager loads approved CPU models once, reuses them across sessions, and evicts only idle eligible models. The GPU remains reserved for the Brain. Each session keeps its own plan, decoder state, frame IDs, progress and summaries.

For later image queries, faces use direct lookup against the session's precomputed face vectors. For cars and animals, a vision-language model describes the reference image; captions, timestamped summaries and entity records shortlist retained frames before object-crop matching. Attributes help retrieve candidates but do not prove an exact match. See the [future matching policy](docs/architecture.md#future-matching-direct-face-lookup-and-object-candidate-search); query execution remains deferred.

## Design documents

| Document | Purpose |
| --- | --- |
| [Architecture and model choices](docs/architecture.md) | Model-labelled flow, Brain call locations, concurrent videos, model loading and storage |
| [Workflow and class diagram](docs/workflows.md) | Stage contracts, model-manager interfaces, bounded scheduling and recovery |

## Implementation status

These are design documents. No agent API, model manager, inference pipeline, MinIO/Chroma integration, or agent Docker release has been implemented here yet. Agent 2, chat and query-image search remain deferred; Agent 1 already includes Brain calls in the proposed design.

The existing web application has separate [packaging and deployment instructions](../README.md). Its image does not include this proposed agent service.
