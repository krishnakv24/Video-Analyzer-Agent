"""Script to download CPU-optimized ONNX models for Agent 1 Tools into AI_Models/Tools_Models."""

from pathlib import Path
import ssl
import time
import urllib.request

BASE_DIR = Path(__file__).parent / "AI_Models" / "Tools_Models"

MODELS = [
    {
        "folder": "object_detection",
        "name": "yolox_nano.onnx",
        "url": "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx",
        "desc": "YOLOX-Nano Object Detector (CPU)",
    },
    {
        "folder": "face_detection",
        "name": "rfb_320_face_detector.onnx",
        "url": "https://raw.githubusercontent.com/Linzaer/Ultra-Light-Fast-Generic-Face-Detector-1MB/master/models/onnx/version-RFB-320.onnx",
        "desc": "UltraFace RFB-320 Face Detector (CPU)",
    },
    {
        "folder": "face_features",
        "name": "mobilefacenet_features.onnx",
        "url": "https://huggingface.co/deepghs/insightface/resolve/main/buffalo_s/w600k_mbf.onnx",
        "desc": "MobileFaceNet Face Feature Extractor (CPU)",
    },
    {
        "folder": "text_embeddings",
        "name": "all_minilm_l6_v2.onnx",
        "url": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx",
        "desc": "all-MiniLM-L6-v2 Text Embeddings (CPU)",
    },
    {
        "folder": "text_embeddings",
        "name": "minilm_tokenizer.json",
        "url": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/raw/main/tokenizer.json",
        "desc": "MiniLM Tokenizer Config",
    },
    {
        "folder": "text_embeddings",
        "name": "minilm_vocab.txt",
        "url": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/raw/main/vocab.txt",
        "desc": "MiniLM Tokenizer Vocab",
    },
]


def download_all():
    ctx = ssl.create_default_context()
    headers = {"User-Agent": "Mozilla/5.0"}

    print(f"Base folder: {BASE_DIR.resolve()}\n")

    for item in MODELS:
        folder_path = BASE_DIR / item["folder"]
        folder_path.mkdir(parents=True, exist_ok=True)
        target_path = folder_path / item["name"]

        if target_path.exists() and target_path.stat().st_size > 0:
            size_mb = target_path.stat().st_size / (1024 * 1024)
            print(f"[EXISTS] {item['folder']}/{item['name']} ({size_mb:.2f} MB)")
            continue

        print(f"[DOWNLOADING] {item['desc']} -> {item['folder']}/{item['name']}...")
        t0 = time.time()
        req = urllib.request.Request(item["url"], headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=120) as resp, open(target_path, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)

        elapsed = time.time() - t0
        size_mb = target_path.stat().st_size / (1024 * 1024)
        print(f"[COMPLETED] {item['folder']}/{item['name']}: {size_mb:.2f} MB in {elapsed:.1f}s\n")

    print("\nAll required tools models are downloaded and organized!")


if __name__ == "__main__":
    download_all()
