from __future__ import annotations
import argparse, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "octopuss" / "pipelines"
STAGES = {
    "prepare": P / "intelligence_preparation.py",
    "commi3": P / "commi3_watch.py",
    "entities": P / "entity_scan.py",
    "deep_entities": P / "deep_entity_build.py",
    "stories": P / "story_scan.py",
    "reports": P / "report_builder.py",
}

def run(names: list[str]) -> int:
    started = time.time(); failures = []
    print("=" * 72); print("OCTOPUSS INTELLIGENCE SYSTEM"); print("=" * 72)
    for i, name in enumerate(names, 1):
        print(f"\n[{i}/{len(names)}] {name.upper().replace('_', ' ')}")
        result = subprocess.run([sys.executable, str(STAGES[name])], cwd=ROOT)
        if result.returncode:
            failures.append(name)
            break
    print("\n" + "=" * 72); print(f"COMPLETE in {time.time() - started:.1f}s")
    if failures:
        print("FAILED STAGES: " + ", ".join(failures)); return 1
    print("All requested OCTOPUSS stages completed."); return 0

def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--commi3-watch", action="store_true")
    group.add_argument("--entity-scan", action="store_true")
    group.add_argument("--deep-entity-build", action="store_true")
    group.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.commi3_watch:
        return run(["prepare", "commi3", "reports"])
    if args.entity_scan:
        return run(["prepare", "entities", "stories", "reports"])
    if args.deep_entity_build:
        return run(["prepare", "entities", "deep_entities", "stories", "reports"])
    return run(["prepare", "commi3", "entities", "deep_entities", "stories", "reports"])

if __name__ == "__main__":
    raise SystemExit(main())
