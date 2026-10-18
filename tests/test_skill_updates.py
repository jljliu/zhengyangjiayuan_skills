import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


updater = load_module("skill_updater", ROOT / "scripts/check_skill_update.py")
packager = load_module("skill_packager", ROOT / "scripts/package_skills.py")
OLD_SHA, NEW_SHA = "1" * 40, "2" * 40
SKILL = "zhengyang-video-copywriter"


class SkillUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "scripts/check_skill_update.py").write_bytes((ROOT / "scripts/check_skill_update.py").read_bytes())
        for name in [SKILL, "zhengyang-video-copy-reviewer"]:
            directory = self.source / name
            directory.mkdir()
            (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Test skill\n---\nOld content\n")
        self.old = self.root / "old"
        self.old_manifest = packager.build_release(self.source, self.old, OLD_SHA)
        self.installed = self.root / "installed"
        self.installed.mkdir()
        with zipfile.ZipFile(self.old / "skills.zip") as bundle:
            bundle.extractall(self.installed)
        self.skill = self.installed / SKILL
        (self.source / SKILL / "SKILL.md").write_text("New content\n")
        self.new = self.root / "new"
        self.manifest = packager.build_release(self.source, self.new, NEW_SHA)
        self.requests = []
        self.now = 200000.0

    @property
    def state_path(self):
        return self.installed / ".zhengyang-skill-updates" / f"{SKILL}.json"

    def fetch(self, url, limit):
        self.requests.append(url)
        filename = url.rsplit("/", 1)[-1].split("?", 1)[0]
        return (self.new / filename).read_bytes()

    def run_check(self, fetch=None, force=False):
        return updater.check_update(self.skill, fetch=fetch or self.fetch, clock=lambda: self.now, force=force)

    def seed_state(self, **values):
        self.state_path.parent.mkdir(exist_ok=True)
        updater.write_json(self.state_path, values)

    def test_interval_skips_network_until_more_than_24_hours(self):
        self.seed_state(last_check_at=self.now - 86400)
        self.assertEqual(self.run_check()["status"], "not_due")
        self.assertEqual(self.requests, [])
        self.now += 1
        self.assertEqual(self.run_check()["status"], "updated")
        self.assertEqual(len(self.requests), 2)

    def test_most_recent_update_or_check_controls_interval(self):
        self.seed_state(last_check_at=self.now - 90000, last_updated_at=self.now - 1)
        self.assertEqual(self.run_check()["status"], "not_due")
        self.assertEqual(self.requests, [])

    def test_unchanged_sha_only_downloads_manifest_and_records_check(self):
        (self.new / "manifest.json").write_text(json.dumps(self.old_manifest))
        self.assertEqual(self.run_check()["status"], "unchanged")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(updater.read_json(self.state_path)["last_check_at"], self.now)

    def test_update_replaces_current_skill_keeps_backup_and_other_skill(self):
        result = self.run_check()
        self.assertEqual(result["status"], "updated")
        self.assertEqual((self.skill / "SKILL.md").read_text(), "New content\n")
        self.assertEqual(updater.read_json(self.skill / ".release.json")["commit_sha"], NEW_SHA)
        other = self.installed / "zhengyang-video-copy-reviewer/.release.json"
        self.assertEqual(updater.read_json(other)["commit_sha"], OLD_SHA)
        with zipfile.ZipFile(result["backup"]) as backup:
            self.assertIn("Old content", backup.read(f"{SKILL}/SKILL.md").decode())
        self.assertEqual(result["reload"], str(self.skill / "SKILL.md"))
        self.requests.clear()
        self.assertEqual(self.run_check()["status"], "not_due")
        self.assertEqual(self.requests, [])

    def test_failed_check_retains_installation_and_is_throttled(self):
        def offline(url, limit):
            self.requests.append(url)
            raise OSError("Offline")
        original = (self.skill / "SKILL.md").read_bytes()
        self.assertEqual(self.run_check(fetch=offline)["status"], "failed")
        self.assertEqual((self.skill / "SKILL.md").read_bytes(), original)
        self.assertEqual(self.run_check(fetch=offline)["status"], "not_due")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.run_check(fetch=offline, force=True)["status"], "failed")
        self.assertEqual(len(self.requests), 2)

    def test_local_edits_or_additions_are_not_overwritten(self):
        (self.skill / "notes.txt").write_text("Local notes")
        self.assertEqual(self.run_check()["status"], "local_changes")
        self.assertEqual((self.skill / "notes.txt").read_text(), "Local notes")
        self.assertEqual(len(self.requests), 1)

    def test_wrong_checksum_does_not_change_installed_version(self):
        filename = self.manifest["skills"][SKILL]["filename"]
        (self.new / filename).write_bytes(b"Not the published ZIP")
        result = self.run_check()
        self.assertEqual(result["status"], "failed")
        self.assertIn("checksum", result["detail"])
        self.assertEqual(updater.read_json(self.skill / ".release.json")["commit_sha"], OLD_SHA)

    def test_traversal_is_rejected_even_with_matching_archive_checksum(self):
        filename = self.manifest["skills"][SKILL]["filename"]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr(f"{SKILL}/../../escaped.txt", "Do not write")
        data = buffer.getvalue()
        (self.new / filename).write_bytes(data)
        self.manifest["skills"][SKILL]["sha256"] = hashlib.sha256(data).hexdigest()
        (self.new / "manifest.json").write_text(json.dumps(self.manifest))
        self.assertEqual(self.run_check()["status"], "failed")
        self.assertFalse((self.root / "escaped.txt").exists())
        self.assertIn("Old content", (self.skill / "SKILL.md").read_text())

    def test_install_failure_rolls_back_previous_directory(self):
        real_replace = updater.os.replace
        def fail_install(source, destination):
            if Path(source).name == SKILL and Path(source).parent.name == "unpacked" and Path(destination) == self.skill:
                raise OSError("Simulated replacement failure")
            return real_replace(source, destination)
        with patch.object(updater.os, "replace", side_effect=fail_install):
            self.assertEqual(self.run_check()["status"], "failed")
        self.assertIn("Old content", (self.skill / "SKILL.md").read_text())

    def test_source_checkout_without_release_metadata_is_not_modified(self):
        (self.skill / ".release.json").unlink()
        self.assertEqual(self.run_check()["status"], "source_or_legacy")
        self.assertEqual(self.requests, [])
        self.assertFalse(self.state_path.exists())

    def test_original_files_survive_even_if_filesystem_blocks_rollback(self):
        real_replace = updater.os.replace
        def block_destination(source, destination):
            if Path(destination) == self.skill:
                raise OSError("Simulated destination permission failure")
            return real_replace(source, destination)
        with patch.object(updater.os, "replace", side_effect=block_destination):
            result = self.run_check()
        self.assertEqual(result["status"], "failed")
        self.assertIn("original files remain", result["detail"])
        originals = list((self.state_path.parent / "backups").glob(".previous-*/SKILL.md"))
        self.assertEqual(len(originals), 1)
        self.assertIn("Old content", originals[0].read_text())

    def test_concurrent_check_is_skipped_and_lock_can_be_reused(self):
        self.state_path.parent.mkdir(exist_ok=True)
        lock = updater.acquire_lock(self.state_path.with_suffix(".lock"))
        try:
            self.assertEqual(self.run_check()["status"], "busy")
            self.assertEqual(self.requests, [])
        finally:
            lock.close()
        self.assertEqual(self.run_check()["status"], "updated")

    def test_packages_are_self_contained_checksummed_and_deterministic(self):
        another = self.root / "same-release"
        repeated = packager.build_release(self.source, another, NEW_SHA)
        self.assertEqual(self.manifest["bundle"], repeated["bundle"])
        self.assertEqual(self.manifest["skills"], repeated["skills"])
        for name, asset in self.manifest["skills"].items():
            data = (self.new / asset["filename"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), asset["sha256"])
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                self.assertIn(f"{name}/scripts/check_skill_update.py", bundle.namelist())
                metadata = json.loads(bundle.read(f"{name}/.release.json"))
                for path, checksum in metadata["files"].items():
                    self.assertEqual(hashlib.sha256(bundle.read(f"{name}/{path}")).hexdigest(), checksum)


if __name__ == "__main__":
    unittest.main()
