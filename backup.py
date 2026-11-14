"""
backup.py — نسخ احتياطي لـ workspace/input و workspace/output إلى Google Drive (عبر rclone).

الاستخدام:
    python backup.py                       # نسخة واحدة الآن
    python backup.py --watch               # نسخ دوري تلقائي (كل 5 دقائق افتراضياً)
    python backup.py --watch --interval 600

كل نسخة = أرشيف ZIP مؤرَّخ + ملف manifest (JSON) يحتوي SHA-256 وقائمة الملفات.
النسخ السابقة لا تُحذف أبداً. يُرفع الأرشيف أولاً ثم الـ manifest، فوجود الـ manifest
يعني أن الرفع اكتمل. setup.py يستورد هذا الملف لاستخدام نفس الدوال أثناء الاسترجاع.
"""
import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone

WORKSPACE_DIR = "workspace"
INPUT_DIR = "workspace/input"
OUTPUT_DIR = "workspace/output"
AREAS = {"input": INPUT_DIR, "output": OUTPUT_DIR}

# اسم remote في rclone (يُنشأ بالأمر: rclone config) ومجلد الوجهة
REMOTE_NAME = "gdrive"
REMOTE_FOLDER = "GameRevBackup"
RCLONE_REMOTE = f"{REMOTE_NAME}:{REMOTE_FOLDER}"
RCLONE_FLAGS = ["--contimeout", "30s", "--timeout", "120s"]

BACKUP_PREFIX = "gamerev_backup_"
MANIFEST_SUFFIX = ".manifest.json"
TS_FORMAT = "%Y%m%dT%H%M%SZ"
TS_RE = r"\d{8}T\d{6}Z"
ARCHIVE_RE = re.compile(rf"^{BACKUP_PREFIX}({TS_RE})\.zip$")
MANIFEST_RE = re.compile(rf"^{BACKUP_PREFIX}({TS_RE}){re.escape(MANIFEST_SUFFIX)}$")
LEGACY_ARCHIVE = "outputs_backup.zip"  # الصيغة القديمة (بدون manifest)
MANIFEST_FORMAT = "gamerev-backup"
MANIFEST_VERSION = 1

TMP_DIR = os.path.join(WORKSPACE_DIR, ".backup_tmp")
PENDING_DIR = os.path.join(WORKSPACE_DIR, "backups_pending")
STATE_FILE = os.path.join(WORKSPACE_DIR, ".backup_state.json")

# ملفات هي نسخ من محتوى الـ APK (موجودة أصلاً في workspace/input) فلا حاجة لحفظها
# من مجلد المخرجات. (لا ينطبق على مجلد input.)
EXCLUDED_NAMES = {"libil2cpp.so", "global-metadata.dat", "libunity.so", "libmain.so"}
EXCLUDED_EXTS = (".apk",)
STORED_EXTS = (".apk", ".zip", ".7z", ".gz", ".png", ".jpg", ".jpeg")  # مضغوطة أصلاً

UPLOAD_ATTEMPTS = 3
RETRY_DELAYS = (5, 15, 45)
DEFAULT_INTERVAL = 300
MIN_INTERVAL = 60


# ---------------------------------------------------------------- أدوات عامة
def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def parse_ts(token):
    try:
        return datetime.strptime(token, TS_FORMAT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def iso_from_dt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _retry_delay(i):
    override = os.environ.get("GAMEREV_RETRY_DELAY")  # للاختبارات: 0 = بدون انتظار
    if override is not None:
        return float(override)
    return RETRY_DELAYS[min(i, len(RETRY_DELAYS) - 1)]


# ---------------------------------------------------------------- مسارات آمنة
_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                 *(f"lpt{i}" for i in range(1, 10))}


def check_member_name(name):
    """يتحقق أن اسم ملف داخل الأرشيف آمن (لا path traversal). يعيد (True,'') أو (False,سبب)."""
    if not isinstance(name, str) or not name:
        return False, "empty or non-string path"
    if any(ord(c) < 32 for c in name):
        return False, "control character in path"
    if "\\" in name:
        return False, "backslash in path"
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return False, "absolute path"
    if ":" in name:
        return False, "colon in path (drive / NTFS stream)"
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False, "path traversal or empty segment"
    if posixpath.normpath(name) != name:
        return False, "non-normalized path"
    if parts[0] not in AREAS:
        return False, f"outside input/ and output/ ({parts[0]})"
    if len(parts) < 2:
        return False, "no file name"
    for p in parts:
        if p.rstrip(". ") != p:
            return False, "trailing dot/space in a path segment"
        if p.split(".")[0].lower() in _WIN_RESERVED:
            return False, "reserved device name"
    return True, ""


class ManifestError(Exception):
    pass


def validate_manifest(m, ts):
    """يتحقق من بنية الـ manifest ومطابقته للطابع الزمني. يرفع ManifestError."""
    if not isinstance(m, dict):
        raise ManifestError("manifest is not a JSON object")
    if m.get("format") != MANIFEST_FORMAT or m.get("version") != MANIFEST_VERSION:
        raise ManifestError("unknown manifest format/version")
    if m.get("timestamp") != ts:
        raise ManifestError("manifest timestamp does not match file name")
    dt = parse_ts(ts)
    if dt is None or m.get("created_utc") != iso_from_dt(dt):
        raise ManifestError("created_utc does not match timestamp")
    arch = m.get("archive")
    if (not isinstance(arch, dict) or arch.get("name") != f"{BACKUP_PREFIX}{ts}.zip"
            or not isinstance(arch.get("size"), int) or arch["size"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(arch.get("sha256", "")))):
        raise ManifestError("invalid archive section")
    files = m.get("files")
    if not isinstance(files, list):
        raise ManifestError("files is not a list")
    seen = set()
    counts = {a: {"files": 0, "bytes": 0} for a in AREAS}
    for f in files:
        if not isinstance(f, dict):
            raise ManifestError("invalid file entry")
        ok, why = check_member_name(f.get("path"))
        if not ok:
            raise ManifestError(f"unsafe path {f.get('path')!r}: {why}")
        if f["path"] in seen:
            raise ManifestError(f"duplicate path {f['path']!r}")
        seen.add(f["path"])
        if (not isinstance(f.get("size"), int) or f["size"] < 0
                or not re.fullmatch(r"[0-9a-f]{64}", str(f.get("sha256", "")))):
            raise ManifestError(f"invalid size/sha256 for {f['path']!r}")
        area = f["path"].split("/", 1)[0]
        counts[area]["files"] += 1
        counts[area]["bytes"] += f["size"]
    if m.get("areas") != counts:
        raise ManifestError("areas summary does not match files list")
    return m


# ---------------------------------------------------------------- rclone
class RcloneError(Exception):
    """kind: no_rclone | no_remote | auth | network | notfound | other"""

    def __init__(self, kind, message, returncode=None):
        super().__init__(message)
        self.kind = kind
        self.returncode = returncode


_NETWORK_PATTERNS = ("dial tcp", "no such host", "i/o timeout", "network is unreachable",
                     "connection refused", "connection reset", "tls handshake",
                     "context deadline exceeded", "temporary failure in name resolution",
                     "timed out", "timeout", "no route to host")
_AUTH_PATTERNS = ("invalid_grant", "invalid_client", "oauth2", "couldn't fetch token",
                  "failed to configure token", "token has been expired", "empty token",
                  "unauthorized", "unauthenticated", "401", "invalid credentials",
                  "autherror")


def classify_failure(returncode, text):
    low = (text or "").lower()
    if returncode in (3, 4):
        return "notfound"
    if "didn't find section in config file" in low:
        return "no_remote"
    # الشبكة أولاً: انتهاء صلاحية التوكن بدون إنترنت يظهر كخطأ oauth2 + dial tcp
    if any(p in low for p in _NETWORK_PATTERNS):
        return "network"
    if any(p in low for p in _AUTH_PATTERNS):
        return "auth"
    return "other"


def rclone(args, timeout=120):
    try:
        p = subprocess.run(["rclone", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise RcloneError("no_rclone", "rclone executable not found")
    except subprocess.TimeoutExpired:
        raise RcloneError("network", f"rclone timed out after {timeout}s")
    if p.returncode != 0:
        text = (p.stderr or p.stdout or "").strip()
        raise RcloneError(classify_failure(p.returncode, text), text[-600:], p.returncode)
    return p


def with_retries(fn, attempts=UPLOAD_ATTEMPTS, label="rclone"):
    for i in range(attempts):
        try:
            return fn()
        except RcloneError as e:
            if e.kind in ("auth", "no_remote", "no_rclone", "notfound") or i == attempts - 1:
                raise
            d = _retry_delay(i)
            print(f"[!] {label}: فشلت المحاولة {i + 1}/{attempts} ({e.kind}). إعادة المحاولة بعد {d:g}ث...")
            time.sleep(d)


class DriveStatus:
    def __init__(self, ok, kind="ok", detail=""):
        self.ok, self.kind, self.detail = ok, kind, detail


def check_rclone_installed():
    if shutil.which("rclone") is None:
        return DriveStatus(False, "no_rclone", "rclone غير مثبت (غير موجود في PATH)")
    try:
        p = rclone(["version"], timeout=30)
        return DriveStatus(True, "ok", (p.stdout or "").splitlines()[0] if p.stdout else "")
    except RcloneError as e:
        return DriveStatus(False, "no_rclone", f"rclone لا يعمل: {e}")


def check_remote_configured():
    try:
        p = rclone(["listremotes"], timeout=30)
    except RcloneError as e:
        return DriveStatus(False, e.kind if e.kind == "no_rclone" else "other", str(e))
    remotes = {l.strip() for l in p.stdout.splitlines() if l.strip()}
    if f"{REMOTE_NAME}:" not in remotes:
        return DriveStatus(False, "no_remote",
                           f"الـ remote '{REMOTE_NAME}' غير موجود في إعداد rclone "
                           f"(الموجود: {', '.join(sorted(remotes)) or 'لا شيء'})")
    return DriveStatus(True)


def check_connection():
    try:
        with_retries(lambda: rclone(["lsd", f"{REMOTE_NAME}:", *RCLONE_FLAGS], timeout=120),
                     attempts=2, label="الاتصال بـ Google Drive")
    except RcloneError as e:
        return DriveStatus(False, e.kind if e.kind != "notfound" else "other", str(e))
    return DriveStatus(True)


def check_drive():
    for fn in (check_rclone_installed, check_remote_configured, check_connection):
        st = fn()
        if not st.ok:
            return st
    return DriveStatus(True)


def print_drive_help(st):
    """رسالة واضحة + تعليمات حسب نوع المشكلة."""
    print(f"[-] مشكلة في Google Drive ({st.kind}): {st.detail}")
    if st.kind == "no_rclone":
        print("    1) ثبّت rclone: https://rclone.org/install/  (على ويندوز: winget install Rclone.Rclone)")
        print("    2) أعد فتح الطرفية ثم شغّل: rclone config")
    if st.kind in ("no_rclone", "no_remote"):
        print(f"    أنشئ remote باسم '{REMOTE_NAME}' (النوع: drive) بالأمر: rclone config")
        print("    على RDP بدون متصفح: شغّل على جهازك المحلي  rclone authorize \"drive\"")
        print("    ثم الصق التوكن الناتج عند سؤال config_token>")
    elif st.kind == "auth":
        print("    المصادقة مفقودة أو منتهية. جهاز RDP يُدمَّر فتضيع إعدادات rclone معه،")
        print("    لذلك يجب إعادة الربط في كل جهاز جديد (لا يُفترض أنها محفوظة):")
        print(f"      rclone config reconnect {REMOTE_NAME}:")
        print("      (أو rclone config وأنشئ الـ remote من جديد، أو rclone authorize \"drive\" على جهاز بمتصفح)")
    elif st.kind == "network":
        print("    تعذر الوصول إلى Google Drive (شبكة/DNS/مهلة). تحقق من الاتصال بالإنترنت ثم أعد المحاولة.")
    if st.kind in ("no_rclone", "no_remote", "auth"):
        print("    تنبيه أمني: لا ترفع rclone.conf أو أي توكن إلى Git أبداً.")


def list_remote_dir():
    """يعيد (exists, [ {name,size} ])؛ المجلد غير الموجود => (False, [])."""
    try:
        p = with_retries(lambda: rclone(["lsjson", RCLONE_REMOTE + "/", "--files-only", *RCLONE_FLAGS],
                                        timeout=180), attempts=2, label="قراءة قائمة النسخ")
    except RcloneError as e:
        if e.kind == "notfound":
            return False, []
        raise
    try:
        data = json.loads(p.stdout or "[]")
        return True, [{"name": x["Name"], "size": x.get("Size", -1)} for x in data if not x.get("IsDir")]
    except (ValueError, KeyError, TypeError):
        raise RcloneError("other", "تعذر قراءة قائمة الملفات من rclone")


# ---------------------------------------------------------------- جمع الملفات
def workspace_files():
    """[(arcname, full_path)] مرتبة. تتجاهل الروابط الرمزية؛ الاستثناءات تخص output فقط."""
    out = []
    for area, root in AREAS.items():
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root, followlinks=False):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                if area == "output" and (f in EXCLUDED_NAMES or f.lower().endswith(EXCLUDED_EXTS)):
                    continue
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                out.append((f"{area}/{rel}", full))
    return out


def fingerprint(files=None):
    h = hashlib.sha256()
    for arc, full in (workspace_files() if files is None else files):
        try:
            st = os.stat(full)
        except OSError:
            continue
        h.update(f"{arc}\0{st.st_size}\0{st.st_mtime_ns}\n".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(fp, backup_name):
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fp, "last_backup": backup_name,
                   "time": iso_from_dt(datetime.now(timezone.utc))}, f)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- بناء الأرشيف
def build_archive(workdir, ts):
    """ينشئ الأرشيف والـ manifest ويتحقق منهما. يعيد (archive_path, manifest_path, manifest) أو None.
    أي ملف غير مقروء يوقف النسخة بخطأ صريح (لا نسخة ناقصة بصمت)."""
    files = workspace_files()
    if not files:
        return None
    os.makedirs(workdir, exist_ok=True)
    archive_path = os.path.join(workdir, f"{BACKUP_PREFIX}{ts}.zip")
    entries, skipped = [], []
    print(f"[+] جاري ضغط input و output ({len(files)} ملف)...")
    with zipfile.ZipFile(archive_path, "w", allowZip64=True) as zf:
        for arc, full in files:
            ok, why = check_member_name(arc)
            if not ok:
                print(f"[!] تخطي ملف باسم غير صالح للاسترجاع الآمن: {arc} ({why})")
                skipped.append({"path": arc, "reason": why})
                continue
            try:
                zi = zipfile.ZipInfo.from_file(full, arc)
            except ValueError:  # تاريخ قبل 1980
                zi = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
                zi.external_attr = (stat.S_IFREG | 0o644) << 16
            except OSError as e:
                raise RuntimeError(f"تعذرت قراءة {arc}: {e}")
            zi.compress_type = (zipfile.ZIP_STORED if full.lower().endswith(STORED_EXTS)
                                else zipfile.ZIP_DEFLATED)
            h, n = hashlib.sha256(), 0
            try:
                with open(full, "rb") as src, zf.open(zi, "w", force_zip64=True) as dst:
                    while True:
                        b = src.read(1 << 20)
                        if not b:
                            break
                        h.update(b)
                        dst.write(b)
                        n += len(b)
            except OSError as e:
                raise RuntimeError(f"تعذرت قراءة {arc}: {e}")
            entries.append({"path": arc, "size": n, "sha256": h.hexdigest()})
    return _finalize(archive_path, workdir, ts, entries, skipped)


def _finalize(archive_path, workdir, ts, entries, skipped):
    # تحقق من سلامة الـ ZIP ومطابقته للـ manifest قبل الرفع
    try:
        with zipfile.ZipFile(archive_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise RuntimeError(f"ZIP integrity check failed at {bad}")
            names = [i.filename for i in zf.infolist()]
            sizes = {i.filename: i.file_size for i in zf.infolist()}
    except (zipfile.BadZipFile, OSError) as e:
        raise RuntimeError(f"فشل التحقق من سلامة الأرشيف: {e}")
    if sorted(names) != sorted(e["path"] for e in entries) or any(sizes[e["path"]] != e["size"] for e in entries):
        raise RuntimeError("محتوى الأرشيف لا يطابق قائمة الملفات")
    counts = {a: {"files": 0, "bytes": 0} for a in AREAS}
    for e in entries:
        a = e["path"].split("/", 1)[0]
        counts[a]["files"] += 1
        counts[a]["bytes"] += e["size"]
    manifest = {
        "format": MANIFEST_FORMAT, "version": MANIFEST_VERSION, "timestamp": ts,
        "created_utc": iso_from_dt(parse_ts(ts)),
        "archive": {"name": os.path.basename(archive_path), "size": os.path.getsize(archive_path),
                    "sha256": sha256_file(archive_path)},
        "areas": counts, "files": entries, "skipped": skipped,
    }
    manifest_path = os.path.join(workdir, f"{BACKUP_PREFIX}{ts}{MANIFEST_SUFFIX}")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    with open(manifest_path, encoding="utf-8") as f:
        validate_manifest(json.load(f), ts)
    print(f"[+] تم التحقق من الأرشيف (ZIP سليم، SHA-256 {manifest['archive']['sha256'][:12]}...)")
    return archive_path, manifest_path, manifest


# ---------------------------------------------------------------- الرفع
def _upload_one(local, remote_name):
    size = os.path.getsize(local)

    def attempt():
        rclone(["copyto", local, f"{RCLONE_REMOTE}/{remote_name}", *RCLONE_FLAGS], timeout=7200)
        _, entries = list_remote_dir()
        if size not in [e["size"] for e in entries if e["name"] == remote_name]:
            raise RcloneError("other", f"حجم الملف على Drive لا يطابق المحلي ({remote_name})")
        # rclone check --download compares actual remote bytes, not only metadata/size.
        rclone(["check", local, f"{RCLONE_REMOTE}/{remote_name}", "--download", *RCLONE_FLAGS], timeout=7200)

    with_retries(attempt, attempts=UPLOAD_ATTEMPTS, label=f"رفع {remote_name}")


def upload_backup(archive_path, manifest_path):
    # الأرشيف أولاً ثم الـ manifest: وجود الـ manifest = الرفع اكتمل
    _upload_one(archive_path, os.path.basename(archive_path))
    _upload_one(manifest_path, os.path.basename(manifest_path))


def _unique_ts(existing_names):
    dt = datetime.now(timezone.utc).replace(microsecond=0)
    while (f"{BACKUP_PREFIX}{dt.strftime(TS_FORMAT)}.zip" in existing_names
           or f"{BACKUP_PREFIX}{dt.strftime(TS_FORMAT)}{MANIFEST_SUFFIX}" in existing_names):
        dt += timedelta(seconds=1)
    return dt.strftime(TS_FORMAT)


def _pending_runs():
    """Return persistent pending-run directories in creation order."""
    if not os.path.isdir(PENDING_DIR):
        return []
    runs = []
    for name in sorted(os.listdir(PENDING_DIR)):
        path = os.path.join(PENDING_DIR, name)
        if os.path.isdir(path) and not os.path.islink(path):
            runs.append(path)
    return runs


def flush_pending():
    """Upload pending backups oldest-first; remove each only after both files verify."""
    st = check_drive()
    if not st.ok:
        print_drive_help(st)
        return False
    try:
        list_remote_dir()  # confirms destination can be listed before attempting uploads
    except RcloneError as e:
        print(f"[-] تعذر الوصول إلى مجلد النسخ على Drive: {e}")
        return False

    all_ok = True
    for run_dir in _pending_runs():
        archives = [n for n in os.listdir(run_dir) if ARCHIVE_RE.match(n)]
        manifests = [n for n in os.listdir(run_dir) if MANIFEST_RE.match(n)]
        if len(archives) != 1 or len(manifests) != 1:
            print(f"[!] نسخة معلّقة غير مكتملة؛ تُترك محلياً دون حذف: {run_dir}")
            all_ok = False
            continue
        archive_path = os.path.join(run_dir, archives[0])
        manifest_path = os.path.join(run_dir, manifests[0])
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            ts = ARCHIVE_RE.match(archives[0]).group(1)
            validate_manifest(manifest, ts)
            if manifest["archive"]["sha256"] != sha256_file(archive_path):
                raise RuntimeError("local archive SHA-256 mismatch")
            if manifest["archive"]["size"] != os.path.getsize(archive_path):
                raise RuntimeError("local archive size mismatch")
            with zipfile.ZipFile(archive_path) as zf:
                bad = zf.testzip()
                if bad:
                    raise RuntimeError(f"corrupt ZIP member: {bad}")
            upload_backup(archive_path, manifest_path)
            print(f"[+] تم رفع والتحقق من النسخة المعلّقة: {archives[0]}")
            shutil.rmtree(run_dir)
            # Remove the parent queue only when it is empty.
            try:
                os.rmdir(PENDING_DIR)
            except OSError:
                pass
        except Exception as e:
            print(f"[-] لم تكتمل مزامنة {run_dir}؛ احتفظنا بها محلياً: {e}")
            all_ok = False
            # Do not skip the next pending run; it may be independently uploadable.
    return all_ok


def run_backup(skip_unchanged=False):
    """Create and verify a durable local backup first, then attempt Drive sync."""
    files = workspace_files()
    if not files:
        print("[-] تنبيه: مجلدا input و output فارغان، لا يوجد ما يتم نسخه.")
        return False
    fp_before = fingerprint(files)
    if skip_unchanged and load_state().get("fingerprint") == fp_before and not _pending_runs():
        print("[*] لا تغييرات منذ آخر نسخة، تخطي.")
        return True

    os.makedirs(PENDING_DIR, exist_ok=True)
    # Build directly in a unique persistent directory: no temp archive can vanish on network failure.
    known = set()
    for pending in _pending_runs():
        known.update(os.listdir(pending))
    try:
        _, remote_entries = list_remote_dir()
        known.update(e["name"] for e in remote_entries)
    except RcloneError:
        pass  # local timestamp uniqueness still applies; PID makes the directory unique
    ts = _unique_ts(known)
    run_dir = os.path.join(PENDING_DIR, f"{ts}-{os.getpid()}")
    try:
        built = build_archive(run_dir, ts)
    except Exception as e:
        print(f"[-] خطأ أثناء إنشاء النسخة المحلية: {e}")
        shutil.rmtree(run_dir, ignore_errors=True)
        return False
    if built is None:
        shutil.rmtree(run_dir, ignore_errors=True)
        return False

    archive_path, manifest_path, manifest = built
    print(f"[+] النسخة المحلية محفوظة ومتحقق منها: {run_dir}")
    synced = flush_pending()
    if synced:
        save_state(fp_before, os.path.basename(archive_path))
        a = manifest["areas"]
        print(f"[+] اكتملت النسخة: input={a['input']['files']} ملف، output={a['output']['files']} ملف.")
        return True
    print("[!] Google Drive غير متاح أو لم يكتمل الرفع؛ النسخة باقية محلياً في backups_pending.")
    return False


WATCHER_PID_FILE = os.path.join(WORKSPACE_DIR, "backup_watcher.pid")
WATCHER_STOP_FILE = os.path.join(WORKSPACE_DIR, "backup_watcher.stop")


def _pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            p = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                               capture_output=True, text=True, timeout=10)
            return str(pid) in p.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def watcher_status():
    try:
        with open(WATCHER_PID_FILE, encoding="ascii") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False, None
    if _pid_alive(pid):
        return True, pid
    try:
        os.remove(WATCHER_PID_FILE)
    except OSError:
        pass
    return False, None


def start_watcher(interval=DEFAULT_INTERVAL):
    running, pid = watcher_status()
    if running:
        print(f"[+] مراقب النسخ يعمل بالفعل (PID {pid}).")
        return True
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    try:
        os.remove(WATCHER_STOP_FILE)
    except FileNotFoundError:
        pass
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        flags = getattr(subprocess, "start_new_session", False)
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--watch", "--interval", str(interval), "--daemon-child"], **kwargs)
    # Wait briefly for child to publish its PID.
    for _ in range(30):
        time.sleep(0.1)
        running, pid = watcher_status()
        if running:
            print(f"[+] بدأ مراقب النسخ في الخلفية (PID {pid}).")
            return True
    print("[-] لم أتمكن من تأكيد بدء مراقب النسخ؛ شغّله يدوياً باستخدام python backup.py --watch")
    return False


def stop_watcher():
    running, pid = watcher_status()
    if not running:
        print("[*] لا يوجد مراقب نسخ يعمل حالياً.")
        return 0
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    with open(WATCHER_STOP_FILE, "w", encoding="ascii") as f:
        f.write("stop")
    print(f"[+] أُرسل طلب إيقاف للمراقب (PID {pid}).")
    return 0


def watch(interval, max_cycles=None, sleep=time.sleep):
    """Periodic watcher with a PID marker and graceful stop-file handling."""
    interval = max(MIN_INTERVAL, int(interval))
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    # O_EXCL makes lock creation atomic across concurrent setup.py invocations.
    lock_fd = None
    for attempt in range(2):
        try:
            lock_fd = os.open(WATCHER_PID_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(lock_fd, str(os.getpid()).encode("ascii"))
            os.close(lock_fd)
            lock_fd = None
            break
        except FileExistsError:
            running, existing_pid = watcher_status()
            if running:
                print(f"[-] يوجد مراقب آخر يعمل (PID {existing_pid}).")
                return 1
            try:
                os.remove(WATCHER_PID_FILE)  # stale PID marker
            except OSError:
                pass
    else:
        print("[-] تعذر الحصول على قفل مراقب النسخ.")
        return 1
    try:
        try:
            os.remove(WATCHER_STOP_FILE)
        except FileNotFoundError:
            pass
        print(f"[+] مراقب النسخ يعمل كل {interval} ثانية. للإيقاف: python backup.py --stop")
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            if os.path.exists(WATCHER_STOP_FILE):
                print("[+] تم استلام طلب إيقاف المراقب.")
                break
            cycles += 1
            try:
                run_backup(skip_unchanged=True)
            except Exception as e:
                print(f"[-] خطأ غير متوقع في دورة النسخ: {e}")
            if max_cycles is not None and cycles >= max_cycles:
                break
            # Short sleep slices make stop requests responsive.
            remaining = interval
            while remaining > 0 and not os.path.exists(WATCHER_STOP_FILE):
                chunk = min(2, remaining)
                sleep(chunk)
                remaining -= chunk
    except KeyboardInterrupt:
        print("\n[+] تم إيقاف النسخ الدوري.")
    finally:
        try:
            os.remove(WATCHER_PID_FILE)
        except OSError:
            pass
        try:
            os.remove(WATCHER_STOP_FILE)
        except OSError:
            pass
    return 0

def upload_to_gdrive():
    """متوافق مع الاسم القديم."""
    return run_backup()


def main(argv=None):
    ap = argparse.ArgumentParser(description="نسخ احتياطي لـ workspace/input و workspace/output إلى Google Drive")
    ap.add_argument("--watch", action="store_true", help="تشغيل المراقب الدوري")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="الفاصل بالثواني (الافتراضي 300)")
    ap.add_argument("--status", action="store_true", help="عرض حالة المراقب")
    ap.add_argument("--stop", action="store_true", help="إيقاف المراقب")
    ap.add_argument("--daemon-child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if args.status:
        running, pid = watcher_status()
        print(f"RUNNING (PID {pid})" if running else "STOPPED")
        print(f"Pending backups: {len(_pending_runs())}")
        return 0
    if args.stop:
        return stop_watcher()
    if args.watch:
        return watch(args.interval)
    return 0 if run_backup() else 1


if __name__ == "__main__":
    sys.exit(main())
