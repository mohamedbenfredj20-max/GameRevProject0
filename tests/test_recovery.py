"""
End-to-end tests for automatic Google Drive session recovery (setup.py) and backup.py.

IMPORTANT: no real rclone / Google Drive is used. tests/mock_rclone.py stands in for rclone and maps
`gdrive:` to a local folder; failure modes (network down, expired token, ...) are injected via env vars.
Run:  python -m unittest discover -s tests -v
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

BACKUP_DIR = os.path.join("gdrive", "GameRevBackup")


class Sandbox:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="gamerev_test_")
        self.root = os.path.join(self.tmp, "remote")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.root)
        os.makedirs(self.bin)
        shutil.copy(os.path.join(HERE, "mock_rclone.py"), os.path.join(self.bin, "rclone"))
        os.chmod(os.path.join(self.bin, "rclone"), 0o755)
        self.n = 0

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- sessions (= one RDP machine = one project checkout with its own workspace)
    def session(self):
        self.n += 1
        d = os.path.join(self.tmp, f"rdp{self.n}")
        os.makedirs(d)
        for f in ("setup.py", "backup.py"):
            shutil.copy(os.path.join(PROJECT, f), d)
        return d

    def env(self, **extra):
        e = dict(os.environ)
        e.update({"PATH": self.bin + os.pathsep + e.get("PATH", ""), "MOCK_RCLONE_ROOT": self.root,
                  "GAMEREV_RETRY_DELAY": "0", "PYTHONIOENCODING": "utf-8"})
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def run(self, session, script, *args, **env_extra):
        env = self.env(**{k: v for k, v in env_extra.items() if k != "PATH"})
        if "PATH" in env_extra:
            env["PATH"] = env_extra["PATH"]
        return subprocess.run([sys.executable, script, *args], cwd=session, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=300)

    def setup(self, session, **env_extra):
        return self.run(session, "setup.py", "--skip-tools", "--skip-game-download", **env_extra)

    def backup(self, session, **env_extra):
        return self.run(session, "backup.py", **env_extra)

    # --- remote helpers
    @property
    def remote_dir(self):
        return os.path.join(self.root, BACKUP_DIR)

    def remote_files(self):
        return sorted(os.listdir(self.remote_dir)) if os.path.isdir(self.remote_dir) else []

    def manifests(self):
        return [f for f in self.remote_files() if f.endswith(".manifest.json")]

    def archive_for(self, manifest_name):
        return os.path.join(self.remote_dir, manifest_name.replace(".manifest.json", ".zip"))


def write(session, rel, data):
    p = os.path.join(session, "workspace", *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def read(session, rel):
    with open(os.path.join(session, "workspace", *rel.split("/")), "rb") as f:
        return f.read()


def exists(session, rel):
    return os.path.exists(os.path.join(session, "workspace", *rel.split("/")))


def tree(session, area):
    out, root = {}, os.path.join(session, "workspace", area)
    for d, _, fs in os.walk(root):
        for f in fs:
            full = os.path.join(d, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root).replace(os.sep, "/")] = fh.read()
    return out


def summary(out):
    s = {}
    for k in ("Google Drive", "Latest backup", "Backup verification", "Input files restored",
              "Output files restored", "Recovery status"):
        m = re.search(rf"^{k}: (.*)$", out, re.M)
        s[k] = m.group(1).strip() if m else None
    return s


SAMPLE = {
    "input/game.apk": b"PK-fake-apk" * 500,
    "input/assets.zip": b"fake-assets" * 300,
    "output/04_notes/findings.md": "# findings\nكشف 1\n",
    "output/02_il2cpp/dump.cs": "class Foo {}\n" * 50,
    "output/03_cdn_assets/a/b/c.json": '{"x": 1}',
}


def populate(session, extra=None):
    for k, v in SAMPLE.items():
        write(session, k, v)
    for k, v in (extra or {}).items():
        write(session, k, v)


def make_backup(sb, session):
    r = sb.backup(session)
    assert r.returncode == 0, r.stdout + r.stderr
    return sb.manifests()[-1]


def forge_backup(sb, ts, members, manifest_files, symlink_names=()):
    """Write a *self-consistent* (valid SHA-256, valid ZIP) archive+manifest that contains hostile members."""
    name = f"gamerev_backup_{ts}.zip"
    path = os.path.join(sb.remote_dir, name)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(path, "w") as zf:
            for n, data in members:
                zi = zipfile.ZipInfo(n, date_time=(2026, 1, 1, 0, 0, 0))
                if n in symlink_names:
                    zi.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(zi, data)
    files = [{"path": p, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()} for p, d in manifest_files]
    counts = {a: {"files": 0, "bytes": 0} for a in ("input", "output")}
    for f in files:
        a = f["path"].split("/")[0]
        counts[a]["files"] += 1
        counts[a]["bytes"] += f["size"]
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    m = {"format": "gamerev-backup", "version": 1, "timestamp": ts,
         "created_utc": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:{ts[13:15]}Z",
         "archive": {"name": name, "size": os.path.getsize(path), "sha256": sha},
         "areas": counts, "files": files, "skipped": []}
    with open(os.path.join(sb.remote_dir, f"gamerev_backup_{ts}.manifest.json"), "w") as f:
        json.dump(m, f)


class Base(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def assertStepsInOrder(self, out):
        pos = [out.index(f"[{n:>2}/15]") for n in range(1, 16)]
        self.assertEqual(pos, sorted(pos), "steps printed out of order")

    def source_session(self, extra=None):
        a = self.sb.session()
        populate(a, extra)
        return a


# ===================================================================== 1
class T1_FreshWorkspace(Base):
    def test_fresh_rdp_recovery(self):
        a = self.source_session({"output/libil2cpp.so": b"x", "output/skip.apk": b"y"})
        mname = make_backup(self.sb, a)
        manifest = json.load(open(os.path.join(self.sb.remote_dir, mname)))
        self.assertEqual(manifest["areas"]["input"]["files"], 2)
        self.assertEqual(manifest["areas"]["output"]["files"], 3)  # excluded names/ext not archived

        b = self.sb.session()  # brand-new "RDP machine"
        r = self.sb.setup(b)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = summary(r.stdout)
        self.assertEqual(s["Google Drive"], "CONNECTED")
        self.assertEqual(s["Backup verification"], "PASSED")
        self.assertEqual(s["Input files restored"], "2")
        self.assertEqual(s["Output files restored"], "3")
        self.assertEqual(s["Recovery status"], "SUCCESS")
        self.assertIn("GAME REV PROJECT — SESSION RECOVERY", r.stdout)
        self.assertStepsInOrder(r.stdout)
        self.assertEqual(tree(b, "input"), tree(a, "input"))
        self.assertEqual(tree(b, "output"), {k: v for k, v in tree(a, "output").items()
                                             if k not in ("libil2cpp.so", "skip.apk")})
        self.assertFalse(exists(b, ".recovery_tmp"), "temp recovery files must be cleaned after success")
        self.assertFalse(exists(b, "recovery_conflicts"))
        self.assertEqual(self.sb.manifests(), [mname], "remote backup must be preserved")

    def test_archive_integrity_and_hash(self):
        a = self.source_session()
        m = make_backup(self.sb, a)
        arch = self.sb.archive_for(m)
        manifest = json.load(open(os.path.join(self.sb.remote_dir, m)))
        self.assertEqual(hashlib.sha256(open(arch, "rb").read()).hexdigest(), manifest["archive"]["sha256"])
        with zipfile.ZipFile(arch) as zf:
            self.assertIsNone(zf.testzip())
            self.assertEqual(sorted(zf.namelist()), sorted(f["path"] for f in manifest["files"]))
        self.assertRegex(m, r"^gamerev_backup_\d{8}T\d{6}Z\.manifest\.json$")


# ===================================================================== 2 + 8
class T2_ExistingFilesAndConflicts(Base):
    def test_existing_files_preserved_and_conflicts_kept(self):
        a = self.source_session()
        make_backup(self.sb, a)
        b = self.sb.session()
        write(b, "input/game.apk", SAMPLE["input/game.apk"])           # identical
        write(b, "output/04_notes/findings.md", "LOCAL EDIT - must survive\n")  # conflicting
        write(b, "output/mine.txt", "local only")                       # not in backup
        r = self.sb.setup(b)
        self.assertEqual(r.returncode, 1, r.stdout)
        s = summary(r.stdout)
        self.assertEqual(s["Recovery status"], "PARTIAL")
        self.assertEqual(s["Input files restored"], "2")    # identical + new
        self.assertEqual(s["Output files restored"], "2")   # 3 in backup, 1 conflict kept local
        self.assertEqual(read(b, "output/04_notes/findings.md"), b"LOCAL EDIT - must survive\n")
        self.assertEqual(read(b, "output/mine.txt"), b"local only")
        stash = [os.path.join(d, f) for d, _, fs in os.walk(os.path.join(b, "workspace", "recovery_conflicts"))
                 for f in fs if f == "findings.md"]
        self.assertEqual(len(stash), 1)
        self.assertEqual(open(stash[0], "rb").read(), SAMPLE["output/04_notes/findings.md"].encode())
        self.assertIn("kept untouched", r.stdout)

        # idempotent: running again must not duplicate stashes nor touch local files
        r2 = self.sb.setup(b)
        self.assertEqual(summary(r2.stdout)["Recovery status"], "PARTIAL")
        names = [f for d, _, fs in os.walk(os.path.join(b, "workspace", "recovery_conflicts")) for f in fs]
        self.assertEqual(sorted(names), ["CONFLICTS.json", "findings.md"])
        self.assertEqual(read(b, "output/04_notes/findings.md"), b"LOCAL EDIT - must survive\n")

    def test_local_directory_where_backup_has_file(self):
        a = self.source_session()
        make_backup(self.sb, a)
        b = self.sb.session()
        os.makedirs(os.path.join(b, "workspace", "input", "game.apk"))  # a directory in the way
        write(b, "input/game.apk/inner.txt", "keep me")
        r = self.sb.setup(b)
        self.assertEqual(summary(r.stdout)["Recovery status"], "PARTIAL")
        self.assertEqual(read(b, "input/game.apk/inner.txt"), b"keep me")

    def test_symlinked_parent_cannot_escape_workspace(self):
        a = self.source_session()
        make_backup(self.sb, a)
        b = self.sb.session()
        outside = os.path.join(self.sb.tmp, "outside")
        os.makedirs(outside)
        os.makedirs(os.path.join(b, "workspace", "output"))
        os.symlink(outside, os.path.join(b, "workspace", "output", "04_notes"))
        r = self.sb.setup(b)
        self.assertEqual(os.listdir(outside), [], "restore must not write through a symlink out of the workspace")
        self.assertEqual(summary(r.stdout)["Recovery status"], "PARTIAL")


class T8_DuplicatesAndHostileArchives(Base):
    def _good_and_hostile(self, hostile_members, hostile_manifest_files, symlinks=()):
        a = self.source_session()
        make_backup(self.sb, a)
        forge_backup(self.sb, "29990101T000000Z", hostile_members, hostile_manifest_files, symlinks)
        b = self.sb.session()
        r = self.sb.setup(b)
        return a, b, r

    def assert_fell_back(self, a, b, r, reason_regex):
        s = summary(r.stdout)
        self.assertEqual(s["Recovery status"], "PARTIAL", r.stdout)
        self.assertEqual(s["Backup verification"], "PASSED")
        self.assertRegex(r.stdout, reason_regex)
        self.assertRegex(r.stdout, r"Skipped backup 29990101T000000Z")
        self.assertEqual(tree(b, "input"), tree(a, "input"))  # older valid backup restored
        for bad in ("evil.txt", "evil2.txt", "abs_evil.txt", "evil3"):
            self.assertFalse(os.path.exists(os.path.join(b, bad)))
            self.assertFalse(os.path.exists(os.path.join(b, "workspace", bad)))
            self.assertFalse(os.path.exists(os.path.join(self.sb.tmp, bad)))

    def test_path_traversal_member(self):
        ok = ("input/a.txt", b"A")
        for evil in ("../evil.txt", "input/../../evil2.txt", "/abs_evil.txt", "input\\..\\evil3"):
            with self.subTest(evil=evil):
                self.setUp()
                a, b, r = self._good_and_hostile([ok, (evil, b"EVIL")], [ok])
                self.assert_fell_back(a, b, r, r"unsafe path in archive")
                self.sb.cleanup()

    def test_duplicate_member_names(self):
        a, b, r = self._good_and_hostile([("input/a.txt", b"one"), ("input/a.txt", b"two")],
                                         [("input/a.txt", b"one")])
        self.assert_fell_back(a, b, r, r"duplicate member names")

    def test_symlink_member(self):
        a, b, r = self._good_and_hostile([("input/a.txt", b"/etc/passwd")], [("input/a.txt", b"/etc/passwd")],
                                         symlinks=("input/a.txt",))
        self.assert_fell_back(a, b, r, r"symlink member")

    def test_member_not_in_manifest(self):
        a, b, r = self._good_and_hostile([("input/a.txt", b"A"), ("input/extra.txt", b"X")], [("input/a.txt", b"A")])
        self.assert_fell_back(a, b, r, r"archive contents differ from manifest")

    def test_unsafe_path_in_manifest(self):
        a, b, r = self._good_and_hostile([("input/a.txt", b"A")], [("input/../../evil.txt", b"A")])
        self.assert_fell_back(a, b, r, r"invalid manifest")

    def test_case_variant_filenames_both_restored(self):
        a = self.sb.session()
        write(a, "output/Notes.md", "upper")
        write(a, "output/notes.md", "lower")
        write(a, "input/game.apk", "x")
        make_backup(self.sb, a)
        b = self.sb.session()
        r = self.sb.setup(b)
        self.assertEqual(summary(r.stdout)["Output files restored"], "2")
        self.assertEqual(tree(b, "output"), tree(a, "output"))


# ===================================================================== 3 + 7
class T3_CorruptBackups(Base):
    def _two_backups(self):
        a = self.source_session()
        make_backup(self.sb, a)
        write(a, "output/04_notes/findings.md", "NEWER CONTENT")
        make_backup(self.sb, a)
        return a

    def test_corrupted_newest_falls_back_to_previous(self):
        a = self._two_backups()
        m = self.sb.manifests()
        self.assertEqual(len(m), 2)
        arch = self.sb.archive_for(m[-1])
        data = open(arch, "rb").read()
        open(arch, "wb").write(data[: len(data) // 2])  # truncated upload
        b = self.sb.session()
        r = self.sb.setup(b)
        s = summary(r.stdout)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(s["Recovery status"], "PARTIAL")
        self.assertEqual(s["Backup verification"], "PASSED")
        self.assertIn(f"Skipped backup {m[-1].split('_')[2].split('.')[0]}", r.stdout)
        self.assertEqual(read(b, "output/04_notes/findings.md"), SAMPLE["output/04_notes/findings.md"].encode())
        self.assertIn("OLDER backup was restored", r.stdout)

    def test_bitflip_inside_archive(self):
        self._two_backups()
        arch = self.sb.archive_for(self.sb.manifests()[-1])
        data = bytearray(open(arch, "rb").read())
        data[len(data) // 3] ^= 0xFF
        open(arch, "wb").write(bytes(data))
        r = self.sb.setup(self.sb.session())
        self.assertEqual(summary(r.stdout)["Recovery status"], "PARTIAL")
        self.assertIn("SHA-256 mismatch", r.stdout)

    def test_zip_crc_corruption_with_matching_sha(self):
        """Archive bytes corrupted but manifest re-signed with the new SHA: ZIP integrity must still catch it."""
        self._two_backups()
        m = self.sb.manifests()[-1]
        arch = self.sb.archive_for(m)
        data = bytearray(open(arch, "rb").read())
        data[40] ^= 0xFF  # inside first member's data
        open(arch, "wb").write(bytes(data))
        mp = os.path.join(self.sb.remote_dir, m)
        man = json.load(open(mp))
        man["archive"]["sha256"] = hashlib.sha256(bytes(data)).hexdigest()
        json.dump(man, open(mp, "w"))
        r = self.sb.setup(self.sb.session())
        self.assertEqual(summary(r.stdout)["Recovery status"], "PARTIAL")
        self.assertRegex(r.stdout, r"ZIP integrity failure|ZIP corrupt|extraction failed|SHA-256 mismatch for")

    def test_all_backups_corrupted_is_failure_not_first_run(self):
        self._two_backups()
        for m in self.sb.manifests():
            open(self.sb.archive_for(m), "wb").write(b"garbage")
        b = self.sb.session()
        r = self.sb.setup(b)
        s = summary(r.stdout)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(s["Recovery status"], "FAILED")
        self.assertEqual(s["Backup verification"], "FAILED")
        self.assertNotIn("FIRST RUN", r.stdout)
        self.assertIn("DO NOT start analysis", r.stdout)
        self.assertEqual(tree(b, "input"), {})
        self.assertTrue(exists(b, ".recovery_tmp"), "temp files are kept when recovery fails")

    def test_sha256_mismatch_in_manifest(self):
        self._two_backups()
        mp = os.path.join(self.sb.remote_dir, self.sb.manifests()[-1])
        man = json.load(open(mp))
        man["archive"]["sha256"] = "0" * 64
        json.dump(man, open(mp, "w"))
        b = self.sb.session()
        r = self.sb.setup(b)
        self.assertIn("SHA-256 mismatch between archive and manifest", r.stdout)
        self.assertEqual(summary(r.stdout)["Recovery status"], "PARTIAL")
        self.assertEqual(read(b, "output/04_notes/findings.md"), SAMPLE["output/04_notes/findings.md"].encode())

    def test_per_file_sha_mismatch_in_manifest(self):
        self._two_backups()
        mp = os.path.join(self.sb.remote_dir, self.sb.manifests()[-1])
        man = json.load(open(mp))
        man["files"][0]["sha256"] = "f" * 64
        json.dump(man, open(mp, "w"))
        r = self.sb.setup(self.sb.session())
        self.assertRegex(r.stdout, r"SHA-256 mismatch for 'input/")
        self.assertEqual(summary(r.stdout)["Recovery status"], "PARTIAL")

    def test_single_backup_with_sha_mismatch_fails(self):
        a = self.source_session()
        m = make_backup(self.sb, a)
        mp = os.path.join(self.sb.remote_dir, m)
        man = json.load(open(mp))
        man["archive"]["sha256"] = "1" * 64
        json.dump(man, open(mp, "w"))
        r = self.sb.setup(self.sb.session())
        self.assertEqual(summary(r.stdout)["Recovery status"], "FAILED")
        self.assertEqual(r.returncode, 2)

    def test_corrupt_manifest_json(self):
        self._two_backups()
        open(os.path.join(self.sb.remote_dir, self.sb.manifests()[-1]), "w").write("{not json")
        r = self.sb.setup(self.sb.session())
        self.assertEqual(summary(r.stdout)["Recovery status"], "PARTIAL")
        self.assertIn("manifest unreadable", r.stdout)


# ===================================================================== 4 + 5
class T4_DriveUnavailable(Base):
    def test_network_down(self):
        a = self.source_session()
        make_backup(self.sb, a)
        b = self.sb.session()
        r = self.sb.setup(b, MOCK_RCLONE_MODE="network")
        s = summary(r.stdout)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(s["Google Drive"], "FAILED")
        self.assertEqual(s["Recovery status"], "FAILED")
        self.assertEqual(s["Backup verification"], "NOT PERFORMED")  # nothing was verified: do not claim PASSED/FAILED
        self.assertIn("استرجاع الجلسة لم ينجح", r.stdout)
        self.assertNotIn("اكتمل التجهيز.", r.stdout)
        self.assertIn("تعذر الوصول إلى Google Drive", r.stdout)
        self.assertIn("لا تبدأ التحليل", r.stdout)
        self.assertNotIn("FIRST RUN", r.stdout)
        self.assertNotIn("SUCCESS", r.stdout)
        self.assertEqual(tree(b, "input"), {})
        self.assertEqual(tree(b, "output"), {})
        self.assertIn("إعادة المحاولة", r.stdout)  # connection check retried

    def test_drive_drops_mid_download(self):
        """Connection fine at listing, then fails during download -> FAILED, not 'try older backup'."""
        a = self.source_session()
        make_backup(self.sb, a)
        b = self.sb.session()
        # make the mock fail only on copyto by pointing it at a wrapper
        wrapper = os.path.join(self.sb.bin, "rclone")
        real = os.path.join(self.sb.bin, "rclone_real.py")
        os.rename(wrapper, real)
        open(wrapper, "w").write(f'#!/bin/sh\nfor a in "$@"; do [ "$a" = copyto ] && '
                                 f'export MOCK_RCLONE_MODE=network; done\nexec {sys.executable} {real} "$@"\n')
        os.chmod(wrapper, 0o755)
        r = self.sb.setup(b)
        s = summary(r.stdout)
        self.assertEqual(s["Google Drive"], "FAILED")
        self.assertEqual(s["Recovery status"], "FAILED")
        self.assertEqual(tree(b, "input"), {})


class T5_MissingAuthentication(Base):
    def _assert_failed(self, r):
        s = summary(r.stdout)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertEqual(s["Google Drive"], "FAILED")
        self.assertEqual(s["Recovery status"], "FAILED")
        self.assertIn("rclone.conf", r.stdout)  # security reminder not to commit secrets
        self.assertNotIn("FIRST RUN", r.stdout)

    def test_expired_or_missing_token(self):
        r = self.sb.setup(self.sb.session(), MOCK_RCLONE_MODE="auth")
        self._assert_failed(r)
        self.assertIn("rclone config reconnect gdrive:", r.stdout)
        self.assertIn("لا يُفترض أنها محفوظة", r.stdout)

    def test_remote_not_configured(self):
        r = self.sb.setup(self.sb.session(), MOCK_RCLONE_REMOTES="otherremote")
        self._assert_failed(r)
        self.assertIn("rclone authorize", r.stdout)
        self.assertIn("'gdrive'", r.stdout)

    def test_no_remotes_at_all(self):
        r = self.sb.setup(self.sb.session(), MOCK_RCLONE_REMOTES="")
        self._assert_failed(r)

    def test_rclone_not_installed(self):
        empty = os.path.join(self.sb.tmp, "emptybin")
        os.makedirs(empty)
        r = self.sb.setup(self.sb.session(), PATH=empty)
        self._assert_failed(r)
        self.assertIn("rclone غير مثبت", r.stdout)
        self.assertIn("https://rclone.org/install/", r.stdout)

    def test_secrets_are_gitignored(self):
        gi = open(os.path.join(PROJECT, ".gitignore")).read().split()
        self.assertIn("rclone.conf", gi)


# ===================================================================== 6
class T6_EmptyBackupHistory(Base):
    def _assert_first_run(self, r, b):
        s = summary(r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(s["Google Drive"], "CONNECTED")
        self.assertTrue(s["Recovery status"].startswith("SUCCESS"))
        self.assertTrue(s["Latest backup"].startswith("NONE"))
        self.assertIn("FIRST RUN", r.stdout)
        self.assertNotIn("Problem:", r.stdout)
        self.assertTrue(os.path.isdir(os.path.join(b, "workspace", "input")))
        self.assertTrue(os.path.isdir(os.path.join(b, "workspace", "output")))
        self.assertStepsInOrder(r.stdout)

    def test_backup_folder_does_not_exist(self):
        b = self.sb.session()
        self._assert_first_run(self.sb.setup(b), b)

    def test_backup_folder_exists_but_empty(self):
        os.makedirs(self.sb.remote_dir)
        b = self.sb.session()
        self._assert_first_run(self.sb.setup(b), b)

    def test_unrelated_files_only_is_still_first_run(self):
        os.makedirs(self.sb.remote_dir)
        open(os.path.join(self.sb.remote_dir, "notes.txt"), "w").write("hi")
        b = self.sb.session()
        self._assert_first_run(self.sb.setup(b), b)

    def test_orphan_archive_without_manifest_is_not_first_run(self):
        os.makedirs(self.sb.remote_dir)
        open(os.path.join(self.sb.remote_dir, "gamerev_backup_20260101T000000Z.zip"), "wb").write(b"half")
        r = self.sb.setup(self.sb.session())
        self.assertEqual(summary(r.stdout)["Recovery status"], "FAILED")
        self.assertIn("incomplete upload", r.stdout)
        self.assertNotIn("FIRST RUN", r.stdout)

    def test_legacy_outputs_backup_zip_is_flagged_not_silently_ignored(self):
        os.makedirs(self.sb.remote_dir)
        open(os.path.join(self.sb.remote_dir, "outputs_backup.zip"), "wb").write(b"old")
        r = self.sb.setup(self.sb.session())
        self.assertEqual(summary(r.stdout)["Recovery status"], "FAILED")
        self.assertIn("Legacy 'outputs_backup.zip'", r.stdout)
        self.assertNotIn("FIRST RUN", r.stdout)


# ===================================================================== 9
@contextlib.contextmanager
def in_session(sb, session):
    old_cwd, old_env = os.getcwd(), dict(os.environ)
    os.environ.update(sb.env())
    os.chdir(session)
    try:
        yield
    finally:
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)


class T9_InterruptedRestoration(Base):
    def test_interrupt_then_resume(self):
        import importlib
        a = self.source_session({"output/x/%d.txt" % i: "file %d" % i for i in range(6)})
        make_backup(self.sb, a)
        b = self.sb.session()
        with in_session(self.sb, b):
            import backup as bk
            import setup as st
            importlib.reload(bk)
            importlib.reload(st)
            real = st._restore_one
            calls = {"n": 0}

            def flaky(*args, **kw):
                calls["n"] += 1
                if calls["n"] == 4:
                    raise KeyboardInterrupt  # simulates Ctrl+C / killed session mid-restore
                return real(*args, **kw)

            st._restore_one = flaky
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    st.recover_session()
            self.assertTrue(os.path.exists(st.MARKER), "in-progress marker must remain")
            self.assertTrue(os.path.isdir(st.RECOVERY_TMP), "temp files must not be cleaned after an interruption")
            partial = sum(len(f) for _, _, f in os.walk("workspace/input")) + \
                sum(len(f) for _, _, f in os.walk("workspace/output"))
            self.assertEqual(partial, 3)
            stray = [f for _, _, fs in os.walk("workspace") for f in fs
                     if f.endswith(".part") and "recovery_tmp" not in f]
            self.assertEqual(stray, [])

            st._restore_one = real
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                res = st.recover_session()
            out = buf.getvalue()
        self.assertIn("استرجاع سابق لم يكتمل", out)
        self.assertEqual(res["status"], "SUCCESS", out)
        self.assertEqual(res["conflicts"], [])
        self.assertEqual((res["input"], res["output"]), (2, 9))
        self.assertEqual(tree(b, "input"), tree(a, "input"))
        self.assertEqual(tree(b, "output"), tree(a, "output"))
        self.assertFalse(exists(b, ".recovery_tmp"))
        self.assertFalse(exists(b, "recovery_conflicts"))

    def test_real_process_killed_mid_restore(self):
        """SIGKILL a real setup.py process during restoration, then rerun."""
        a = self.source_session({"output/x/%d.txt" % i: "file %d" % i for i in range(6)})
        make_backup(self.sb, a)
        b = self.sb.session()
        driver = os.path.join(b, "kill_driver.py")
        open(driver, "w").write(
            "import os, sys, setup\n"
            "real = setup._restore_one\n"
            "n = {'c': 0}\n"
            "def f(*a, **k):\n"
            "    n['c'] += 1\n"
            "    if n['c'] == 4:\n"
            "        os.kill(os.getpid(), 9)\n"
            "    return real(*a, **k)\n"
            "setup._restore_one = f\n"
            "sys.exit(setup.main(['--skip-tools', '--skip-game-download']))\n")
        r = subprocess.run([sys.executable, driver], cwd=b, env=self.sb.env(), capture_output=True, text=True)
        self.assertEqual(r.returncode, -9)
        self.assertNotIn("SUCCESS", r.stdout)  # success must never be reported before verification
        r2 = self.sb.setup(b)
        s = summary(r2.stdout)
        self.assertEqual(s["Recovery status"], "SUCCESS", r2.stdout)
        self.assertEqual(tree(b, "input"), tree(a, "input"))
        self.assertEqual(tree(b, "output"), tree(a, "output"))
        self.assertFalse(exists(b, "recovery_conflicts"))


# ===================================================================== backup.py
class TB_BackupBehaviour(Base):
    def test_retries_failed_uploads_then_succeeds(self):
        a = self.source_session()
        r = self.sb.backup(a, MOCK_RCLONE_FAIL_UPLOADS=2)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout.count("إعادة المحاولة"), 2)
        self.assertEqual(len(self.sb.manifests()), 1)
        self.assertFalse(exists(a, "backups_pending"))

    def test_upload_gives_up_and_keeps_verified_local_copy(self):
        a = self.source_session()
        r = self.sb.backup(a, MOCK_RCLONE_FAIL_UPLOADS=99)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.sb.manifests(), [], "no manifest may exist for an unfinished upload")
        pending_root = os.path.join(a, "workspace", "backups_pending")
        pend = [
            os.path.join(pending_root, name)
            for name in os.listdir(pending_root)
            if os.path.isdir(os.path.join(pending_root, name))
        ]
        self.assertEqual(len(pend), 1)
        self.assertEqual(len(os.listdir(pend[0])), 2)  # archive + manifest kept locally
        self.assertTrue(any(name.endswith(".zip") for name in os.listdir(pend[0])))
        self.assertTrue(any(name.endswith(".manifest.json") for name in os.listdir(pend[0])))

    def test_previous_backups_are_preserved(self):
        a = self.source_session()
        make_backup(self.sb, a)
        first = self.sb.remote_files()
        write(a, "output/new.txt", "more")
        make_backup(self.sb, a)
        self.assertTrue(set(first) <= set(self.sb.remote_files()))
        self.assertEqual(len(self.sb.manifests()), 2)

    def test_backup_fails_clearly_without_drive(self):
        a = self.source_session()
        r = self.sb.backup(a, MOCK_RCLONE_MODE="auth")
        self.assertEqual(r.returncode, 1)
        self.assertIn("rclone config reconnect", r.stdout)
        self.assertEqual(self.sb.remote_files(), [])

    def test_empty_workspace_not_backed_up(self):
        a = self.sb.session()
        r = self.sb.backup(a)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.sb.remote_files(), [])

    def test_periodic_backup_skips_unchanged_and_backs_up_changes(self):
        import importlib
        a = self.source_session()
        with in_session(self.sb, a):
            import backup as bk
            importlib.reload(bk)
            n = {"c": 0}

            def fake_sleep(_):
                n["c"] += 1
                if n["c"] == 2:  # change something between cycle 2 and 3
                    write(a, "output/04_notes/findings.md", "changed during session " * 3)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                bk.watch(0, max_cycles=3, sleep=fake_sleep)
        out = buf.getvalue()
        self.assertEqual(len(self.sb.manifests()), 2, out)  # cycle1 + cycle3
        self.assertEqual(out.count("لا تغييرات منذ آخر نسخة"), 1)

    def test_after_recovery_first_watch_cycle_does_not_reupload(self):
        import importlib
        a = self.source_session()
        make_backup(self.sb, a)
        b = self.sb.session()
        self.assertEqual(self.sb.setup(b).returncode, 0)
        with in_session(self.sb, b):
            import backup as bk
            importlib.reload(bk)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                bk.watch(0, max_cycles=1)
        self.assertIn("لا تغييرات", buf.getvalue())
        self.assertEqual(len(self.sb.manifests()), 1)

    def test_restore_roundtrip_then_backup_again(self):
        a = self.source_session()
        make_backup(self.sb, a)
        b = self.sb.session()
        self.sb.setup(b)
        write(b, "output/04_notes/session2.md", "work on the new machine")
        self.assertEqual(self.sb.backup(b).returncode, 0)
        c = self.sb.session()
        r = self.sb.setup(c)
        self.assertEqual(summary(r.stdout)["Recovery status"], "SUCCESS")
        self.assertEqual(read(c, "output/04_notes/session2.md"), b"work on the new machine")
        self.assertEqual(read(c, "output/04_notes/findings.md"), SAMPLE["output/04_notes/findings.md"].encode())


if __name__ == "__main__":
    unittest.main(verbosity=2)
