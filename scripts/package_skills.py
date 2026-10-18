#!/usr/bin/env python3
"""Build self-contained release ZIPs and a publish-last manifest. Stdlib only."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

REPOSITORY = "jljliu/zhengyangjiayuan_skills"


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_archive(destination, files):
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, data)


def build_release(root, output, commit_sha, repository=REPOSITORY):
    root, output = Path(root).resolve(), Path(output).resolve()
    if repository != REPOSITORY or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise ValueError("Expected the Zhengyang repository and a full lowercase Git commit SHA")
    skills = sorted(path for path in root.iterdir() if path.is_dir() and (path / "SKILL.md").is_file())
    if not skills:
        raise ValueError("No skill directories found")
    output.mkdir(parents=True, exist_ok=True)
    helper = (root / "scripts/check_skill_update.py").read_bytes()
    manifest = {
        "schema_version": 1, "repository": repository, "commit_sha": commit_sha,
        "published_at": datetime.now(timezone.utc).isoformat(), "skills": {},
    }
    combined = {}
    for skill in skills:
        files = {}
        for path in sorted(skill.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Symlinks are not supported in skill packages: {path}")
            relative = path.relative_to(skill)
            if any(part.startswith(".git") or part in {"__pycache__", "__MACOSX"} for part in relative.parts):
                continue
            if not path.is_file() or path.name.startswith(".DS_Store") or path.name == ".release.json" or path.suffix == ".pyc":
                continue
            files[relative.as_posix()] = path.read_bytes()
        files["scripts/check_skill_update.py"] = helper
        files[".release.json"] = json_bytes({
            "schema_version": 1, "repository": repository, "skill": skill.name,
            "commit_sha": commit_sha, "files": {name: sha256(data) for name, data in files.items()},
        })
        archive_files = {f"{skill.name}/{name}": data for name, data in files.items()}
        filename = f"{skill.name}-{commit_sha}.zip"
        destination = output / filename
        write_archive(destination, archive_files)
        shutil.copyfile(destination, output / f"{skill.name}.zip")
        manifest["skills"][skill.name] = {"filename": filename, "sha256": sha256(destination.read_bytes())}
        combined.update(archive_files)
    filename = f"skills-{commit_sha}.zip"
    write_archive(output / filename, combined)
    shutil.copyfile(output / filename, output / "skills.zip")
    manifest["bundle"] = {"filename": filename, "sha256": sha256((output / filename).read_bytes())}
    (output / "manifest.json").write_bytes(json_bytes(manifest))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repository", default=REPOSITORY)
    args = parser.parse_args()
    manifest = build_release(args.root, args.output, args.sha, args.repository)
    print(f"Packaged {len(manifest['skills'])} skills at {manifest['commit_sha']}; publish manifest.json after all ZIPs.")


if __name__ == "__main__":
    main()
