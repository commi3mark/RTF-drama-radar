#!/usr/bin/env python3
"""
File transcript JSON files into transcripts/YYYY/MM using each video's
publication date, then update transcript-manifest.json paths.

Safe defaults:
- Dry-run unless --apply is supplied.
- Files without a trustworthy publication date are skipped.
- Existing destination files are never overwritten unless identical.
- A JSON migration report is written to logs/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
TRANSCRIPTS_DIR = ROOT / "transcripts"
MANIFEST_PATH = ROOT / "transcript-manifest.json"
LOG_DIR = ROOT / "logs"

DATE_KEYS = (
    "published",
    "published_at",
    "publishedAt",
    "publication_date",
    "publicationDate",
    "upload_date",
    "uploadDate",
    "release_date",
    "releaseDate",
    "date",
)

PATH_KEYS = (
    "path",
    "file",
    "filepath",
    "file_path",
    "transcript",
    "transcript_path",
    "transcriptPath",
    "local_path",
    "localPath",
)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def dump_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_date(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        # Accept Unix timestamps in seconds or milliseconds.
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number)
        except (ValueError, OSError, OverflowError):
            return None

    text = str(value).strip()
    if not text:
        return None

    # Common YouTube yt-dlp form: YYYYMMDD
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d")
        except ValueError:
            return None

    # ISO forms, including trailing Z.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def date_from_json(data: Any) -> datetime | None:
    if not isinstance(data, (dict, list)):
        return None

    for mapping in walk_dicts(data):
        for key in DATE_KEYS:
            if key in mapping:
                parsed = parse_date(mapping[key])
                if parsed:
                    return parsed
    return None


def path_variants(path: Path) -> set[str]:
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        rel = path
    values = {
        str(path),
        path.as_posix(),
        str(rel),
        rel.as_posix(),
    }
    return {v.replace("\\", "/").lstrip("./") for v in values}


def find_manifest_date(manifest: Any, transcript_path: Path) -> datetime | None:
    targets = path_variants(transcript_path)
    filename = transcript_path.name

    for mapping in walk_dicts(manifest):
        referenced = False
        for key in PATH_KEYS:
            value = mapping.get(key)
            if isinstance(value, str):
                normalised = value.replace("\\", "/").lstrip("./")
                if normalised in targets or Path(normalised).name == filename:
                    referenced = True
                    break

        # Also allow a manifest record whose filename/video-id matches the file.
        if not referenced:
            for key in ("filename", "transcript_filename"):
                value = mapping.get(key)
                if isinstance(value, str) and Path(value).name == filename:
                    referenced = True
                    break

        if referenced:
            for key in DATE_KEYS:
                if key in mapping:
                    parsed = parse_date(mapping[key])
                    if parsed:
                        return parsed
    return None


def update_manifest_paths(value: Any, old_rel: str, new_rel: str) -> int:
    changed = 0
    old_norm = old_rel.replace("\\", "/").lstrip("./")
    old_name = Path(old_norm).name

    if isinstance(value, dict):
        for key, child in list(value.items()):
            if isinstance(child, str) and key in PATH_KEYS:
                norm = child.replace("\\", "/").lstrip("./")
                if norm == old_norm:
                    # Preserve slash style only where practical; JSON paths use '/'.
                    value[key] = new_rel
                    changed += 1
                elif Path(norm).name == old_name and norm.startswith("transcripts/"):
                    value[key] = new_rel
                    changed += 1
            else:
                changed += update_manifest_paths(child, old_rel, new_rel)
    elif isinstance(value, list):
        for child in value:
            changed += update_manifest_paths(child, old_rel, new_rel)

    return changed


def transcript_files() -> list[Path]:
    if not TRANSCRIPTS_DIR.exists():
        return []
    return sorted(
        path for path in TRANSCRIPTS_DIR.rglob("*.json")
        if path.is_file()
        and not (
            len(path.relative_to(TRANSCRIPTS_DIR).parts) >= 3
            and re.fullmatch(r"\d{4}", path.relative_to(TRANSCRIPTS_DIR).parts[0])
            and re.fullmatch(r"\d{2}", path.relative_to(TRANSCRIPTS_DIR).parts[1])
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually move files and update the manifest. Default is dry-run.",
    )
    args = parser.parse_args()

    LOG_DIR.mkdir(exist_ok=True)

    manifest: Any = {}
    if MANIFEST_PATH.exists():
        try:
            manifest = load_json(MANIFEST_PATH)
        except Exception as exc:
            print(f"ERROR: Could not read {MANIFEST_PATH.name}: {exc}", file=sys.stderr)
            return 2

    report: dict[str, Any] = {
        "started": datetime.now().astimezone().isoformat(),
        "mode": "apply" if args.apply else "dry-run",
        "moved": [],
        "already_archived": [],
        "skipped_no_publication_date": [],
        "conflicts": [],
        "errors": [],
        "manifest_path_updates": 0,
    }

    for source in transcript_files():
        try:
            data = load_json(source)
        except Exception as exc:
            report["errors"].append({"file": str(source), "error": f"Invalid JSON: {exc}"})
            continue

        published = date_from_json(data) or find_manifest_date(manifest, source)
        if not published:
            report["skipped_no_publication_date"].append(str(source.relative_to(ROOT)))
            continue

        destination_dir = TRANSCRIPTS_DIR / f"{published.year:04d}" / f"{published.month:02d}"
        destination = destination_dir / source.name

        old_rel = source.relative_to(ROOT).as_posix()
        new_rel = destination.relative_to(ROOT).as_posix()

        if source.resolve() == destination.resolve():
            report["already_archived"].append(old_rel)
            continue

        if destination.exists():
            if sha256(source) == sha256(destination):
                if args.apply:
                    source.unlink()
                    report["manifest_path_updates"] += update_manifest_paths(
                        manifest, old_rel, new_rel
                    )
                report["already_archived"].append(
                    {"source": old_rel, "destination": new_rel, "duplicate_removed": args.apply}
                )
            else:
                report["conflicts"].append(
                    {"source": old_rel, "destination": new_rel}
                )
            continue

        if args.apply:
            destination_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            report["manifest_path_updates"] += update_manifest_paths(
                manifest, old_rel, new_rel
            )

        report["moved"].append(
            {
                "source": old_rel,
                "destination": new_rel,
                "published": published.date().isoformat(),
            }
        )

    if args.apply and MANIFEST_PATH.exists() and report["manifest_path_updates"]:
        dump_json(MANIFEST_PATH, manifest)

    report["finished"] = datetime.now().astimezone().isoformat()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = LOG_DIR / f"archive-transcripts-{stamp}.json"
    dump_json(report_path, report)

    print(
        f"{report['mode']}: {len(report['moved'])} move(s), "
        f"{len(report['skipped_no_publication_date'])} skipped without publication date, "
        f"{len(report['conflicts'])} conflict(s)."
    )
    print(f"Report: {report_path}")
    return 1 if report["errors"] or report["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
