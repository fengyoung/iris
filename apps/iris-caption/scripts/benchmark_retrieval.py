"""Download a pinned public model explicitly, then benchmark offline retrieval.

Run with the app venv. Downloads only required safetensors/tokenizer configs to
output/models; inference uses local_files_only and disabled socket connections.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
MODEL = OUTPUT / "models" / f"paraphrase-multilingual-MiniLM-L12-v2-{REVISION[:12]}"
FILES = ["1_Pooling/config.json", "config.json", "config_sentence_transformers.json",
         "model.safetensors", "modules.json", "sentence_bert_config.json",
         "sentencepiece.bpe.model", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"]


def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "iris-caption-benchmark"}),
                                  timeout=60)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("HF_HOME", "TRANSFORMERS_CACHE", "TORCH_HOME", "XDG_CACHE_HOME"):
        os.environ[name] = str(OUTPUT / "models" / "runtime-cache" / name.lower())
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    report = {"model_id": MODEL_ID, "revision": REVISION, "model_path": str(MODEL),
              "python": sys.version, "files": [], "status": "started"}
    try:
        with fetch(f"https://huggingface.co/api/models/{MODEL_ID}/revision/{REVISION}") as response:
            metadata = json.load(response)
        assert metadata["sha"] == REVISION
        report["parameter_count"] = metadata["safetensors"]["total"]
        with fetch(f"https://huggingface.co/api/models/{MODEL_ID}/tree/{REVISION}?recursive=true") as response:
            manifest = {entry["path"]: entry for entry in json.load(response) if entry["type"] == "file"}
        for name in FILES:
            target = MODEL / name
            target.parent.mkdir(parents=True, exist_ok=True)
            downloaded = target
            if not target.exists() or target.stat().st_size != manifest[name]["size"]:
                downloaded = target.with_name(target.name + ".partial")
                print(f"Downloading {name}", flush=True)
                import subprocess
                subprocess.run(["curl", "-fLsS", "--continue-at", "-", "--connect-timeout", "15", "--max-time", "180",
                                "--retry", "1", "--output", str(downloaded),
                                f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}"], check=True)
            with downloaded.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            assert downloaded.stat().st_size == manifest[name]["size"], f"Size mismatch: {name}"
            if "lfs" in manifest[name]:
                assert digest == manifest[name]["lfs"]["oid"], f"SHA256 mismatch: {name}"
            if downloaded != target:
                assert downloaded.resolve().parent == target.resolve().parent
                assert target.resolve().is_relative_to(MODEL.resolve())
                downloaded.replace(target)
            report["files"].append({"name": name, "bytes": target.stat().st_size, "sha256": digest})
        report["download_bytes"] = sum(item["bytes"] for item in report["files"])
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        sys.path.insert(0, str(ROOT / "src"))
        report["materials_source_sha256"] = hashlib.sha256(
            (ROOT / "src" / "iris_caption" / "materials.py").read_bytes()).hexdigest()
        report["hardware"] = {"cpu": "Apple M3", "memory_bytes": 17179869184}
        from iris_caption.materials import MaterialIndex

        def reject_network(*_args, **_kwargs):
            raise RuntimeError("Network disabled during local inference benchmark")

        socket.socket.connect = reject_network
        socket.create_connection = reject_network
        start = time.perf_counter()
        index = MaterialIndex(str(MODEL))
        report["load_seconds"] = time.perf_counter() - start
        report["semantic_available"] = index.semantic_available
        report["backend_status"] = index.status
        report["peak_rss_after_load_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2
        documents = [("设备验收", "移动电话出厂检测发现屏幕亮点和电池故障，需要拦截不合格产品并安排复检。"),
                     ("餐饮安排", "明天午餐提供蔬菜、米饭和水果，食堂十一点开始营业。"),
                     ("会议计划", "研发团队下周讨论季度目标和项目排期，负责人准备进度报告。"),
                     ("交通通知", "地铁线路周末检修，建议乘客选择公交车出行。")]
        lexical = MaterialIndex()
        for name, text in documents:
            index.add_text(name, text)
            lexical.add_text(name, text)
        query = "手机质检异常"
        start = time.perf_counter()
        hybrid = index.snapshot().search(query)
        report["first_search_seconds"] = time.perf_counter() - start
        start = time.perf_counter()
        index.snapshot().search(query)
        report["cached_search_seconds"] = time.perf_counter() - start
        report.update(query=query, documents=documents, hybrid_results=hybrid,
                      lexical_results=lexical.search(query),
                      relevant_document_top1=bool(hybrid and hybrid[0].startswith("[设备验收]")))
        # Report pure semantic scores as well: Chinese character overlap can
        # influence the hybrid ranking and must not stand in for semantic evidence.
        query_vector = index._embedding(query)
        report["semantic_cosines"] = sorted([
            {"name": name, "score": sum(a*b for a, b in zip(query_vector, index._embedding(text)))}
            for name, text in documents], key=lambda item: item["score"], reverse=True)
        report["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2
        report["inference_network_disabled"] = True
        report["status"] = "completed" if index.semantic_available else "degraded"
        report["limitations"] = "Synthetic four-document smoke test; not a quality or M1/8GB performance benchmark. macOS ru_maxrss is bytes; peak includes download/import work."
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
    (OUTPUT / "retrieval-benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "files"}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
