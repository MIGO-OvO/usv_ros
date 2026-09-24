"""Synthetic before/after benchmark; run from repository root.

python tests/benchmark_history_loading.py [baseline-git-ref]
Uses only temporary fixtures; baseline source is read from Git, never checked out.
"""
import ast
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import web_config_server as web
from scripts.lib.sample_recording import storage


def baseline_class(path, name, namespace, ref):
    source = subprocess.check_output(["git", "show", ref + ":" + path], encoding="utf-8")
    module = ast.parse(source)
    node = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == name)
    namespace = dict(namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    return namespace[name]


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    old_manager = baseline_class("scripts/web_config_server.py", "MissionDataManager", vars(web), ref)
    old_storage = baseline_class("scripts/lib/sample_recording/storage.py", "SampleRecordingStorage", vars(storage), ref)
    with tempfile.TemporaryDirectory() as tmp:
        before = old_manager(tmp)
        after = web.MissionDataManager(tmp)
        n = 100000
        mission = {"mission_id": "bench", "name": "benchmark", "end_time": "2026-01-01",
                   "data_points": [{"timestamp": "2026-01-01T00:%02d:%02d" % (i // 60 % 60, i % 60),
                                    "voltage": i % 101, "absorbance": i % 13 / 10, "lat": 30, "lng": 120}
                                   for i in range(n)],
                   "sample_windows": [{"sample_id": "sample"}], "track_points": []}
        path = Path(tmp) / "mission_bench.json"
        path.write_text(json.dumps(mission), encoding="utf-8")
        raw_path = Path(tmp) / "raw/bench/sample.jsonl"
        raw_path.parent.mkdir(parents=True)
        with raw_path.open("w", encoding="utf-8") as file:
            for i in range(n):
                file.write(json.dumps({"timestamp_ms": i, "voltage": i % 101}) + "\n")

        def measure(label, fn):
            counts = {"mission_json_load": 0, "json_parses": 0, "summary_builds": 0}
            def counted(key, original):
                def call(*args, **kwargs):
                    counts[key] += 1
                    return original(*args, **kwargs)
                return call
            summary = counted("summary_builds", web.build_mission_summary)
            with mock.patch.object(json, "load", counted("mission_json_load", json.load)), \
                    mock.patch.object(json, "loads", counted("json_parses", json.loads)), \
                    mock.patch.object(web, "build_mission_summary", summary), \
                    mock.patch.dict(old_manager._with_summary.__globals__, build_mission_summary=summary):
                start = time.perf_counter()
                payload = fn()
                payload_bytes = len(json.dumps(payload).encode("utf-8"))
                elapsed = time.perf_counter() - start
            print(json.dumps({"case": label, "seconds": round(elapsed, 4),
                              "mission_json_load": counts["mission_json_load"],
                              "summary_builds": counts["summary_builds"],
                              "raw_jsonl_parses": counts["json_parses"] - counts["mission_json_load"],
                              "payload_bytes": payload_bytes}))

        def old_first_paint():
            detail = before.get_mission("bench")
            windows = before.get_mission("bench")["sample_windows"]
            sample = before.get_mission("bench")["sample_windows"][0]
            before.get_mission("bench")
            raw = old_storage(tmp).read_raw_series("bench", "sample")
            return [detail, windows, sample, raw]

        print(json.dumps({"points": n, "raw_frames": n, "mission_bytes": path.stat().st_size,
                          "raw_bytes": raw_path.stat().st_size, "baseline": ref}))
        measure("before: task entry (4 API paths)", old_first_paint)
        measure("after: task entry (1 API path)", lambda: after.get_mission_overview("bench"))
        measure("after: warm task entry", lambda: after.get_mission_overview("bench"))
        measure("before: raw only", lambda: old_storage(tmp).read_raw_series("bench", "sample"))
        measure("after: raw only", lambda: storage.SampleRecordingStorage(tmp).read_raw_series("bench", "sample"))


if __name__ == "__main__":
    main()
