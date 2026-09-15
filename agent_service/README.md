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
       -> YuNet face detection + SFace embeddings
       -> SmolVLM-500M captions + MiniLM text embeddings
  -> Join per-session results; persist in MinIO / Chroma
  -> Same GPU Brain: section and overall summaries
  -> Publish evidence and notify that session's completion
```

The proposed Brain is Qwen3-4B-Instruct-2507 through llama.cpp with a validated quantized GPU configuration. CPU and GPU model choices are an evaluation baseline; their fit and throughput have not been measured here.

For People and Cars, qualifying frames containing either category are retained. With Cars-only selection, face processing still runs on every retained car frame. Rejected frames create no downstream evidence; the source remains saved.

The Model Manager loads approved local artifacts once, reuses them across sessions, and evicts only idle CPU models. The Brain stays resident on GPU. Each session keeps its own plan, decoder state, frame IDs, progress and summaries.

## Design documents

| Document | Purpose |
| --- | --- |
| [Architecture and model choices](docs/architecture.md) | Model-labelled flow, Brain call locations, concurrent videos, model loading and storage |
| [Workflow and class diagram](docs/workflows.md) | Stage contracts, model-manager interfaces, bounded scheduling and recovery |

## Implementation status

These are design documents. No agent API, model manager, inference pipeline, MinIO/Chroma integration, or agent Docker release has been implemented here yet. Agent 2, chat and query-image search remain deferred; Agent 1 already includes Brain calls in the proposed design.

The existing web application has separate [packaging and deployment instructions](../README.md). Its image does not include this proposed agent service.
