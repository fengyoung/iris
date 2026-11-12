#!/usr/bin/env python3
"""Accelerated synthetic offscreen UI test; not a real four-hour soak."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import selectors
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/ui-benchmark.json"
COUNT = 1440
STALL_SECONDS = 30


def emit(kind, **values):
    print(json.dumps({"kind": kind, **values}, ensure_ascii=False), flush=True)


def p95(values):
    return sorted(values)[math.ceil(len(values) * .95) - 1]


def rss():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def worker():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(ROOT / "src"))
    from PySide6 import __version__ as qt_version
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from iris_caption.export import render_markdown, render_srt
    from iris_caption.session import Session
    from iris_caption.ui import MainWindow, _SegmentCard

    started = time.perf_counter()
    source_hash = hashlib.sha256((ROOT / "src/iris_caption/ui.py").read_bytes()).hexdigest()
    app = QApplication([])
    window = MainWindow()
    window.show()

    def drain():
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    drain()
    session = Session(title="合成字幕压力测试")
    baseline = rss()
    appends, updates, samples = [], [], []
    max_cards = 0
    workload_started = time.perf_counter()
    for index in range(COUNT):
        begin = time.perf_counter()
        segment = session.apply_audio({
            "session_id": session.id, "type": "final", "segment_id": f"synthetic-{index:04d}",
            "start_ms": index * 10000, "end_ms": (index + 1) * 10000,
            "text": f"第{index + 1:04d}段合成字幕，今天讨论花园与天气。",
        })
        assert segment is not None
        window.upsert_segment(segment.view())
        drain()
        appends.append((time.perf_counter() - begin) * 1000)
        max_cards = max(max_cards, len(window._cards))
        if index % 10 == 9:
            begin = time.perf_counter()
            corrected = session.apply_correction({
                "session_id": session.id, "type": "correction", "segment_id": segment.id,
                "revision": segment.revision,
                "corrected_text": f"第{index + 1:04d}段合成校正，花园天气晴朗。",
            })
            assert corrected is not None
            window.upsert_segment(corrected.view())
            drain()
            updates.append((time.perf_counter() - begin) * 1000)
        if (index + 1) % 100 == 0 or index + 1 == COUNT:
            sample = {"segments": index + 1,
                      "elapsed_seconds": time.perf_counter() - workload_started,
                      "rss_bytes": rss(), "ui_cards": len(window._cards),
                      "ui_segments": len(window._segments),
                      "append_p95_last100_ms": p95(appends[-100:])}
            samples.append(sample)
            emit("progress", **sample)
        else:
            emit("heartbeat", segments=index + 1)
    workload_elapsed = time.perf_counter() - workload_started
    old = session.segments["synthetic-0000"]
    old = session.apply_correction({
        "session_id": session.id, "type": "correction", "segment_id": old.id,
        "revision": old.revision, "corrected_text": "第0001段合成校正，花园迎来微风。",
    })
    window.upsert_segment(old.view())
    drain()
    emit("heartbeat", phase="export")
    markdown, srt = render_markdown(session), render_srt(session, "both")
    exports = {
        "session_segments": len(session.segments),
        "markdown_segments": sum(line.startswith("## ") for line in markdown.splitlines()),
        "srt_segments": srt.count(" --> "),
        "all_raw_strings_in_markdown": all(s.raw in markdown for s in session.ordered()),
        "all_raw_strings_in_srt": all(s.raw in srt for s in session.ordered()),
        "oldest_correction_exported": old.corrected in markdown and old.corrected in srt,
        "final_timestamp_four_hours": "04:00:00,000" in srt,
        "ui_cards_at_export": len(window._cards), "ui_segments_at_export": len(window._segments),
        "ui_capacity_exceeded_in_run": len(window._cards) < COUNT,
        "capacity_note": "No eviction observed" if len(window._cards) == COUNT else "UI retained fewer cards than session",
        "scope": "production export renderers in memory; no file dialog or disk export",
    }
    assert exports["markdown_segments"] == exports["srt_segments"] == COUNT
    assert all(exports[k] for k in ("all_raw_strings_in_markdown", "all_raw_strings_in_srt",
                                   "oldest_correction_exported", "final_timestamp_four_hours"))
    before_reset = rss()
    window.clear_segments()
    drain()
    reset = {"cards": len(window._cards), "segments": len(window._segments),
             "unseen": len(window._unseen), "dirty": window._dirty,
             "empty_label_visible": window.empty_label.isVisible(),
             "layout_items": window.transcript_layout.count(),
             "live_card_widgets": len(window.findChildren(_SegmentCard))}
    assert reset == {"cards": 0, "segments": 0, "unseen": 0, "dirty": False,
                     "empty_label_visible": True, "layout_items": 2, "live_card_widgets": 0}
    after_reset = rss()
    window.close()
    drain()
    emit("result", report={
        "status": "passed", "mode": "synthetic accelerated offscreen; NOT real four-hour soak",
        "simulated_seconds": COUNT * 10, "finalized_segments": COUNT,
        "periodic_correction_updates": len(updates), "oldest_record_update": True,
        "no_microphone": True, "no_cloud": True, "text": "generated short Chinese strings",
        "python": sys.version, "executable": sys.executable,
        "platform": platform.platform(), "pyside6": qt_version,
        "ui_source_sha256": source_hash,
        "stall_watchdog_seconds": STALL_SECONDS,
        "total_elapsed_seconds": time.perf_counter() - started,
        "append_workload_elapsed_seconds": workload_elapsed,
        "append_call_total_seconds": sum(appends) / 1000,
        "append_p95_last100_ms": p95(appends[-100:]), "append_p95_all_ms": p95(appends),
        "append_max_ms": max(appends), "append_individual_ms": appends,
        "update_p95_ms": p95(updates), "update_individual_ms": updates,
        "timing_scope": "session apply + UI upsert + two Qt event drains including deferred deletion",
        "rss_baseline_bytes": baseline, "rss_before_reset_bytes": before_reset,
        "rss_after_reset_bytes": after_reset,
        "rss_max_sampled_bytes": max([baseline, before_reset, after_reset] + [s["rss_bytes"] for s in samples]),
        "rss_measurement": "worker process RSS high-water mark via resource.getrusage; current RSS unavailable because sandbox blocks ps; cannot infer memory release",
        "max_ui_cards": max_cards, "samples": samples, "export_checks": exports, "reset_checks": reset,
        "limitations": "Accelerated synthetic workload does not test wall-clock leaks, audio, cloud, or native display performance.",
    })


def main():
    process = subprocess.Popen(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--worker"],
        stdout=subprocess.PIPE, text=True, bufsize=1,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    report, latest = None, None
    try:
        while True:
            if not selector.select(STALL_SECONDS):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                report = {"status": "stopped_stall", "stall_watchdog_seconds": STALL_SECONDS,
                          "latest_progress": latest, "mode": "synthetic accelerated; NOT real four-hour soak"}
                break
            line = process.stdout.readline()
            if not line:
                break
            event = json.loads(line)
            latest = event
            if event["kind"] == "progress":
                print(json.dumps(event, ensure_ascii=False), flush=True)
            elif event["kind"] == "result":
                report = event["report"]
        code = process.wait(timeout=5)
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
    if report is None:
        report = {"status": "failed", "worker_returncode": code, "latest_progress": latest}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {
        "append_individual_ms", "update_individual_ms", "samples",
    }}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" and code == 0 else 1


if __name__ == "__main__":
    if "--worker" in sys.argv:
        worker()
    else:
        raise SystemExit(main())
