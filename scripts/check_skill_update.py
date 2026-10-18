#!/usr/bin/env python3
"""Invocation-time update check, bundled into each released skill. Stdlib only."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
import urllib.request
import uuid
import zipfile

REPOSITORY = "jljliu/zhengyangjiayuan_skills"
BASE_URL = f"https://proxy.199510000.xyz/github.com/{REPOSITORY}/releases/latest/download/"
CHECK_INTERVAL = 24 * 60 * 60
MAX_DOWNLOAD = 50 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
METADATA = ".release.json"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write_json(path, value):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def download(url, limit):
    request = urllib.request.Request(
        url, headers={"User-Agent": "Zhengyang-Skills/1", "Cache-Control": "no-cache"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Download exceeds size limit")
    return data


def digest(data):
    return hashlib.sha256(data).hexdigest()


def acquire_lock(path):
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def file_map(directory):
    result = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlink cannot be updated automatically: {path.name}")
        if not path.is_file() or path.name == ".DS_Store" or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(directory).as_posix()
        if relative != METADATA:
            result[relative] = digest(path.read_bytes())
    return result


def validate_metadata(value, name):
    if value.get("schema_version") != 1 or value.get("repository") != REPOSITORY:
        raise ValueError("Unknown release metadata")
    if value.get("skill") != name or not re.fullmatch(r"[0-9a-f]{40}", value.get("commit_sha", "")):
        raise ValueError("Release identity does not match the skill")
    if not isinstance(value.get("files"), dict) or "SKILL.md" not in value["files"]:
        raise ValueError("Release file checksums are missing")


def unpack(archive, staging, name):
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if sum(entry.file_size for entry in entries) > MAX_EXPANDED:
            raise ValueError("Expanded archive exceeds size limit")
        seen = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (
                path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                or not path.parts or path.parts[0] != name
                or stat.S_ISLNK(entry.external_attr >> 16)
                or entry.filename.rstrip("/") != path.as_posix()
                or any(":" in part or part.endswith((".", " ")) for part in path.parts)
            ):
                raise ValueError("Unsafe archive path or symlink")
            key = entry.filename.casefold().rstrip("/")
            if key in seen:
                raise ValueError("Duplicate archive entry")
            seen.add(key)
            target = staging.joinpath(*path.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(entry))
    return staging / name


def replace_skill(skill, staged, backup_directory):
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup = backup_directory / f"{skill.name}-{uuid.uuid4().hex}.zip"
    shutil.make_archive(str(backup.with_suffix("")), "zip", root_dir=skill.parent, base_dir=skill.name)
    previous = backup_directory / f".previous-{uuid.uuid4().hex}"
    os.replace(skill, previous)
    try:
        os.replace(staged, skill)
    except Exception:
        try:
            os.replace(previous, skill)
        except OSError as error:
            raise RuntimeError(f"Cannot restore installation; original files remain at {previous}; backup: {backup}") from error
        raise
    shutil.rmtree(previous, ignore_errors=True)
    return backup


def check_update(skill_directory, force=False, fetch=download, clock=time.time):
    skill = Path(skill_directory).resolve()
    metadata = read_json(skill / METADATA)
    if not metadata:
        return {"status": "source_or_legacy", "detail": "No release metadata; existing files unchanged"}
    name = skill.name
    validate_metadata(metadata, name)
    state_directory = skill.parent / ".zhengyang-skill-updates"
    state_directory.mkdir(exist_ok=True)
    state_path = state_directory / f"{name}.json"
    lock = acquire_lock(state_directory / f"{name}.lock")
    if lock is None:
        return {"status": "busy", "detail": "Another check is running; retry on a later invocation"}
    state = {}
    attempted = False
    try:
        # Re-read after acquiring the lock in case another invocation just updated it.
        metadata = read_json(skill / METADATA)
        validate_metadata(metadata, name)
        state = read_json(state_path)
        now = clock()
        last = max(state.get("last_check_at", 0), state.get("last_updated_at", 0))
        if not force and last and 0 <= now - last <= CHECK_INTERVAL:
            return {"status": "not_due", "commit_sha": metadata["commit_sha"], "last_result": state.get("last_result")}
        attempted = True
        manifest = json.loads(fetch(BASE_URL + f"manifest.json?check={int(now)}", 1024 * 1024))
        if manifest.get("schema_version") != 1 or manifest.get("repository") != REPOSITORY:
            raise ValueError("Unknown manifest")
        sha = manifest.get("commit_sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid release commit SHA")
        asset = manifest.get("skills", {}).get(name)
        if not isinstance(asset, dict):
            raise ValueError("This skill is absent from the manifest; existing installation retained")
        if asset.get("filename") != f"{name}-{sha}.zip" or not re.fullmatch(r"[0-9a-f]{64}", asset.get("sha256", "")):
            raise ValueError("Invalid skill archive identity or checksum")
        if sha == metadata["commit_sha"]:
            result = {"status": "unchanged", "commit_sha": sha}
        elif file_map(skill) != metadata["files"]:
            result = {"status": "local_changes", "detail": "Local changes preserved; automatic replacement skipped"}
        else:
            data = fetch(BASE_URL + asset["filename"], MAX_DOWNLOAD)
            if digest(data) != asset["sha256"]:
                raise ValueError("Archive checksum mismatch")
            with tempfile.TemporaryDirectory(prefix="staging-", dir=state_directory) as temporary:
                temporary = Path(temporary)
                archive = temporary / "download.zip"
                archive.write_bytes(data)
                staged = unpack(archive, temporary / "unpacked", name)
                replacement = read_json(staged / METADATA)
                validate_metadata(replacement, name)
                if replacement["commit_sha"] != sha or file_map(staged) != replacement["files"]:
                    raise ValueError("Archive contents do not match release metadata")
                # Check again before replacing, in case a user edited files during download.
                if file_map(skill) != metadata["files"]:
                    raise ValueError("Local files changed during download; update cancelled")
                backup = replace_skill(skill, staged, state_directory / "backups")
            state["last_updated_at"] = clock()
            result = {"status": "updated", "commit_sha": sha, "backup": str(backup), "reload": str(skill / "SKILL.md")}
        state.update(last_check_at=clock(), last_result=result["status"])
        state.pop("last_error", None)
        try:
            write_json(state_path, state)
        except OSError as error:
            result["warning"] = f"Check completed, but the local check timestamp could not be saved: {error}"
        return result
    except Exception as error:
        if attempted:
            state.update(last_check_at=clock(), last_result="failed", last_error=str(error))
            try:
                write_json(state_path, state)
            except OSError:
                pass
        return {"status": "failed", "detail": str(error)}
    finally:
        # The OS releases the lock on close or process exit; interrupted runs leave no stale lock.
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--force", action="store_true", help="Explicitly check again before the 24-hour interval expires")
    args = parser.parse_args()
    try:
        result = check_update(args.skill_dir, force=args.force)
    except Exception as error:
        result = {"status": "failed", "detail": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
