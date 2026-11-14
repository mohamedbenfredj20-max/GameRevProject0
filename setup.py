"""
setup.py — تجهيز بيئة العمل + استرجاع الجلسة تلقائياً من Google Drive.

    python setup.py

الترتيب: (1) تجهيز المجلدات (2) تثبيت/فحص الأدوات (3-15) استرجاع آخر نسخة صالحة من
gdrive:GameRevBackup/ (rclone) ثم تنزيل ما ينقص من ملفات اللعبة وعرض ملخص الاسترجاع.
خيارات للعمل دون إنترنت/للاختبار: --skip-tools و --skip-game-download.
"""
import argparse
import tempfile
import errno
import ctypes
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
import zlib
from collections import Counter

import backup

# معرّفات ملفات Google Drive (الجزء بعد id= في الرابط)
APK_ID = "1CPBAnEnuud_4o77ikUh35SePV2M9louU"
CDN_ASSETS_ID = "1PEThMxR67yxVGusC7RY9UaEW8_w6a-bi"
# قديم: لم يعد يُستخدم (الاسترجاع صار تلقائياً من rclone). إن وُضعت قيمة هنا يُعرض تنبيه فقط.
OUTPUTS_BACKUP_ID = ""

REA_REPO_URL = "https://github.com/morluto/rea.git"
ASSET_RIPPER_URL = "https://github.com/AssetRipper/AssetRipper.git"
JADX_API = "https://api.github.com/repos/skylot/jadx/releases/latest"
IL2CPPDUMPER_API = "https://api.github.com/repos/Perfare/Il2CppDumper/releases/latest"

TOOL_DIRS = ["rea", "AssetRipper", "jadx", "Il2CppDumper"]
TOTAL_STEPS = 15
RECOVERY_TMP = os.path.join("workspace", ".recovery_tmp")
CONFLICTS_DIR = os.path.join("workspace", "recovery_conflicts")
MARKER = os.path.join(RECOVERY_TMP, "restore_in_progress.json")


def step(n, text):
    print(f"[{n:>2}/{TOTAL_STEPS}] {text}")


def ensure_gdown():
    try:
        import gdown  # noqa: F401
    except ImportError:
        print("[+] جاري تثبيت gdown...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "gdown"], check=True)
    import gdown
    return gdown


def download(gdown, file_id, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"[*] الملف موجود مسبقاً: {dest}")
        return True
    try:
        return gdown.download(id=file_id, output=dest, quiet=False) is not None
    except Exception as e:
        print(f"[-] فشل تنزيل {dest}: {e}")
        return False


def clone_tool(name, url):
    path = os.path.join("tools", name)
    if os.path.exists(path):
        print(f"[*] أداة {name} موجودة مسبقاً.")
        return
    try:
        print(f"[+] جاري استنساخ أداة {name}...")
        subprocess.run(["git", "clone", "--depth", "1", url, path], check=True)
        print(f"[+] تم استنساخ {name}")
    except Exception as e:
        print(f"[-] خطأ أثناء استنساخ {name}: {e}")


def install_jadx():
    """ينزّل آخر إصدار من JADX (يعمل ببيئة Java) إلى tools/jadx."""
    if os.path.exists("tools/jadx"):
        print("[*] أداة JADX موجودة مسبقاً.")
        return
    try:
        print("[+] جاري تنزيل JADX...")
        req = urllib.request.Request(JADX_API, headers={"User-Agent": "GameRevProject0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            release = json.load(r)
        asset = next(a for a in release["assets"]
                     if a["name"].startswith("jadx-") and a["name"].endswith(".zip")
                     and "gui" not in a["name"])
        zip_path = "tools/jadx.zip"
        urllib.request.urlretrieve(asset["browser_download_url"], zip_path)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall("tools/jadx")
        os.remove(zip_path)
        print(f"[+] تم تثبيت JADX ({release.get('tag_name', '')})")
    except Exception as e:
        print(f"[-] تعذر تثبيت JADX: {e}")


def install_il2cppdumper():
    """ينزّل Il2CppDumper (أداة .NET، مناسبة لويندوز RDP) إلى tools/Il2CppDumper."""
    if os.path.exists("tools/Il2CppDumper"):
        print("[*] أداة Il2CppDumper موجودة مسبقاً.")
        return
    try:
        print("[+] جاري تنزيل Il2CppDumper...")
        req = urllib.request.Request(IL2CPPDUMPER_API, headers={"User-Agent": "GameRevProject0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            release = json.load(r)
        zips = [a for a in release["assets"] if a["name"].lower().endswith(".zip")]
        if not zips:
            raise RuntimeError("لا توجد ملفات zip في آخر إصدار")
        # نفضّل النسخة المناسبة لـ .NET الحديث إن وُجد اختيار، وإلا أول ملف
        asset = next((a for a in zips if "net" in a["name"].lower()), zips[0])
        zip_path = "tools/Il2CppDumper.zip"
        urllib.request.urlretrieve(asset["browser_download_url"], zip_path)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall("tools/Il2CppDumper")
        os.remove(zip_path)
        print(f"[+] تم تثبيت Il2CppDumper ({release.get('tag_name', '')}, {asset['name']})")
    except Exception as e:
        print(f"[-] تعذر تثبيت Il2CppDumper تلقائياً: {e}")
        print("    نزّله يدوياً من https://github.com/Perfare/Il2CppDumper/releases إلى tools/Il2CppDumper")


def check_prereqs():
    print("[+] فحص المتطلبات...")
    for cmd, hint in [("git", "https://git-scm.com"),
                      ("node", "Node.js 22.23.4+ أو 24.22.1+ (مطلوب لـ REA)"),
                      ("npm", "يأتي مع Node.js"),
                      ("java", "JDK كامل (مطلوب لـ JADX وREA)")]:
        if shutil.which(cmd):
            print(f"    [OK] {cmd}")
        else:
            print(f"    [-] {cmd} غير موجود: {hint}")


def detect_unity_backend(apk_path):
    """يحدد هل لعبة Unity تستخدم IL2CPP أم Mono من محتويات الـ APK."""
    try:
        with zipfile.ZipFile(apk_path) as z:
            names = z.namelist()
    except Exception as e:
        print(f"[-] تعذر قراءة الـ APK: {e}")
        return
    il2cpp = any(n.endswith("libil2cpp.so") for n in names)
    metadata = any(n.endswith("global-metadata.dat") for n in names)
    mono = any(n.endswith("Assembly-CSharp.dll") for n in names)
    unity = any("libunity.so" in n for n in names)
    if not unity:
        print("[?] لم أجد libunity.so: قد لا تكون اللعبة Unity.")
    elif il2cpp and metadata:
        print("[+] Unity بنمط IL2CPP: استخدم Il2CppDumper مع libil2cpp.so وglobal-metadata.dat")
    elif mono:
        print("[+] Unity بنمط Mono: يمكن فك Assembly-CSharp.dll مباشرة (dnSpy / ILSpy)")
    else:
        print("[?] Unity لكن نمط الكود غير واضح (قد تكون الملفات في split APK).")


def validate_tools():
    """يتحقق أن مجلدات الأدوات موجودة وغير فارغة. يعيد قائمة الناقص."""
    missing = [t for t in TOOL_DIRS
               if not (os.path.isdir(os.path.join("tools", t)) and os.listdir(os.path.join("tools", t)))]
    if missing:
        print(f"[-] أدوات ناقصة: {', '.join(missing)} (قد يلزم اتصال إنترنت؛ أعد تشغيل setup.py لاحقاً)")
    else:
        print("[+] جميع الأدوات جاهزة: " + ", ".join(TOOL_DIRS))
    return missing


# ====================================================================
#                       استرجاع الجلسة من Google Drive
# ====================================================================
class RecoveryError(Exception):
    """نسخة معيّنة غير صالحة (فاسدة/غير آمنة): جرّب التي قبلها."""


def _count_local_files():
    return sum(len(fs) for root in backup.AREAS.values() for _, _, fs in os.walk(root))


def _classify_listing(entries):
    archives, manifests, other = {}, {}, []
    legacy = False
    for e in entries:
        n = e["name"]
        m = backup.ARCHIVE_RE.match(n)
        if m:
            archives.setdefault(m.group(1), []).append(e)
            continue
        m = backup.MANIFEST_RE.match(n)
        if m:
            manifests.setdefault(m.group(1), []).append(e)
            continue
        if n == backup.LEGACY_ARCHIVE:
            legacy = True
        elif n.startswith(backup.BACKUP_PREFIX):
            other.append(n)
    return archives, manifests, legacy, other


def _download(name, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        os.remove(dest)
    try:
        backup.with_retries(
            lambda: backup.rclone(["copyto", f"{backup.RCLONE_REMOTE}/{name}", dest, *backup.RCLONE_FLAGS],
                                  timeout=7200), label=f"تنزيل {name}")
    except backup.RcloneError as e:
        if e.kind in ("notfound", "other"):
            raise RecoveryError(f"download of {name} failed: {e}")
        raise  # شبكة/مصادقة: لن تنفع محاولة نسخة أخرى
    if not os.path.isfile(dest):
        raise RecoveryError(f"download of {name} produced no file")


def _fetch_manifest(ts, work):
    dest = os.path.join(work, f"{backup.BACKUP_PREFIX}{ts}{backup.MANIFEST_SUFFIX}")
    _download(os.path.basename(dest), dest)
    try:
        with open(dest, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError) as e:
        raise RecoveryError(f"manifest unreadable: {e}")
    try:
        return backup.validate_manifest(m, ts)
    except backup.ManifestError as e:
        raise RecoveryError(f"invalid manifest: {e}")


def _verify_archive(path, manifest):
    """الخطوة 9: الحجم + SHA-256 + سلامة ZIP."""
    exp = manifest["archive"]
    size = os.path.getsize(path)
    if size != exp["size"]:
        raise RecoveryError(f"archive size mismatch (got {size}, manifest {exp['size']})")
    if backup.sha256_file(path) != exp["sha256"]:
        raise RecoveryError("SHA-256 mismatch between archive and manifest")
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
    except (zipfile.BadZipFile, OSError, zlib.error, RuntimeError, NotImplementedError) as e:
        raise RecoveryError(f"ZIP corrupt: {e}")
    if bad is not None:
        raise RecoveryError(f"ZIP integrity failure at {bad}")


def _validate_members(zf, manifest):
    """الخطوة 10: لا path traversal ولا مسارات مطلقة ولا تكرار ولا روابط رمزية ولا ملفات خارج الـ manifest."""
    infos = zf.infolist()
    names = [i.filename for i in infos]
    dups = sorted(n for n, c in Counter(names).items() if c > 1)
    if dups:
        raise RecoveryError(f"duplicate member names in archive: {dups[:3]}")
    for i in infos:
        ok, why = backup.check_member_name(i.filename)
        if not ok:
            raise RecoveryError(f"unsafe path in archive {i.filename!r}: {why}")
        if i.flag_bits & 0x1:
            raise RecoveryError(f"encrypted member {i.filename!r}")
        if stat.S_ISLNK(i.external_attr >> 16):
            raise RecoveryError(f"symlink member {i.filename!r}")
    expected = {f["path"]: f for f in manifest["files"]}
    if set(names) != set(expected):
        raise RecoveryError("archive contents differ from manifest")
    for i in infos:
        if i.file_size != expected[i.filename]["size"]:
            raise RecoveryError(f"size mismatch for {i.filename!r}")


def _extract(zf, manifest, staging):
    """الخطوة 11: استخراج إلى مجلد مؤقت. لا تُستخدم أسماء الأرشيف كمسارات؛ كل ملف يُكتب باسم رقمي ويُتحقق من SHA-256."""
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    total = sum(f["size"] for f in manifest["files"])
    free = shutil.disk_usage(staging).free
    if free < total + (64 << 20):
        raise OSError(f"not enough disk space to extract ({total} bytes needed, {free} free)")
    staged = {}
    for idx, f in enumerate(manifest["files"]):
        dest = os.path.join(staging, f"{idx:06d}.bin")
        h, n = hashlib.sha256(), 0
        try:
            with zf.open(zf.getinfo(f["path"])) as src, open(dest, "wb") as out:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    n += len(b)
                    if n > f["size"]:
                        raise RecoveryError(f"member {f['path']!r} larger than manifest size")
                    h.update(b)
                    out.write(b)
        except (zipfile.BadZipFile, zlib.error, RuntimeError, EOFError) as e:
            raise RecoveryError(f"extraction failed for {f['path']!r}: {e}")
        if n != f["size"] or h.hexdigest() != f["sha256"]:
            raise RecoveryError(f"SHA-256 mismatch for {f['path']!r}")
        staged[f["path"]] = dest
    return staged


def _same_file(path, size, sha):
    return os.path.getsize(path) == size and backup.sha256_file(path) == sha



def _rename_noreplace(src, dst):
    """Atomically move src to dst without replacing an existing target."""

    if hasattr(os, "link"):
        try:
            os.link(src, dst)
            try:
                os.unlink(src)
            except OSError:
                pass
            return
        except FileExistsError:
            raise
        except OSError:
            pass

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError):
        raise OSError(errno.ENOTSUP, "Safe no-replace rename unavailable", dst)

    renameat2.argtypes = [
        ctypes.c_int, ctypes.c_char_p,
        ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint
    ]
    renameat2.restype = ctypes.c_int

    result = renameat2(
        -100, os.fsencode(src),
        -100, os.fsencode(dst),
        1
    )

    if result != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), dst)


def _stash_conflict(staged, area, rel, ts, sha):
    """Preserve a conflicting Drive file without replacing existing files."""

    parts = rel.split("/")
    base = os.path.join(CONFLICTS_DIR, ts, area, *parts)
    os.makedirs(os.path.dirname(base), exist_ok=True)

    n = 0
    while True:
        dest = base if n == 0 else f"{base}.{n}"

        if os.path.isfile(dest) and not os.path.islink(dest):
            if backup.sha256_file(dest) == sha:
                return dest

        if os.path.lexists(dest):
            n += 1
            continue

        fd, tmp = tempfile.mkstemp(
            prefix=".restore-",
            suffix=".part",
            dir=os.path.dirname(dest)
        )

        try:
            with os.fdopen(fd, "wb") as out, open(staged, "rb") as src:
                shutil.copyfileobj(src, out)
                out.flush()
                os.fsync(out.fileno())

            if backup.sha256_file(tmp) != sha:
                raise RecoveryError("Conflict copy SHA-256 verification failed")

            try:
                _rename_noreplace(tmp, dest)
                return dest
            except FileExistsError:
                n += 1
                continue
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def _restore_one(staged, area, rel, entry, ts):
    """Restore without overwriting local files."""

    root = backup.AREAS[area]
    target = os.path.join(root, *rel.split("/"))
    parent = os.path.dirname(target)
    reason = None

    try:
        os.makedirs(parent, exist_ok=True)
    except OSError:
        reason = "a parent path component is a file"

    if reason is None:
        real_root = os.path.realpath(root)
        real_parent = os.path.realpath(parent)

        if real_parent != real_root and not real_parent.startswith(real_root + os.sep):
            reason = "parent directory resolves outside the workspace (symlink)"

    if reason is None:
        for _ in range(2):
            if os.path.lexists(target):
                if os.path.islink(target) or not os.path.isfile(target):
                    reason = "local path is a directory/symlink/special file"
                elif _same_file(target, entry["size"], entry["sha256"]):
                    return "identical", {"target": target}
                else:
                    reason = "local file content differs from backup"
                break

            try:
                _rename_noreplace(staged, target)
                return "placed", {"target": target}

            except FileExistsError:
                continue

            except OSError as e:
                if e.errno == errno.EEXIST:
                    continue
                reason = f"safe no-replace restore failed: {e}"
                break
        else:
            reason = "target appeared during restore"

    stash = _stash_conflict(staged, area, rel, ts, entry["sha256"])
    return "conflict", {
        "target": target,
        "reason": reason,
        "stash": stash
    }


def _restore_files(manifest, staged, ts):
    """الخطوة 12."""
    os.makedirs(RECOVERY_TMP, exist_ok=True)
    with open(MARKER, "w", encoding="utf-8") as f:
        json.dump({"backup": manifest["timestamp"]}, f)
    outcomes = {}
    for entry in manifest["files"]:
        area, rel = entry["path"].split("/", 1)
        res, info = _restore_one(staged[entry["path"]], area, rel, entry, ts)
        outcomes[entry["path"]] = {"result": res, **info}
    return outcomes


def _verify_restored(manifest, outcomes):
    """الخطوة 13: عدد الملفات وأحجامها (و SHA-256 للملفات التي وُضعت للتو)."""
    ok = {a: 0 for a in backup.AREAS}
    expected = {a: 0 for a in backup.AREAS}
    problems = []
    for f in manifest["files"]:
        area = f["path"].split("/", 1)[0]
        out = outcomes[f["path"]]
        if out["result"] == "conflict":
            if not os.path.isfile(out["stash"]) or os.path.getsize(out["stash"]) != f["size"]:
                problems.append(f"conflict copy missing/wrong size: {f['path']}")
            continue
        expected[area] += 1
        t = out["target"]
        if not os.path.isfile(t) or os.path.getsize(t) != f["size"]:
            problems.append(f"missing or wrong size: {f['path']}")
        elif out["result"] == "placed" and backup.sha256_file(t) != f["sha256"]:
            problems.append(f"SHA-256 mismatch after restore: {f['path']}")
        else:
            ok[area] += 1
    for a in backup.AREAS:
        if ok[a] != expected[a]:
            problems.append(f"{a}: verified {ok[a]} of {expected[a]} files")
    return ok, problems


def _new_result():
    return {"drive": "FAILED", "latest": "UNKNOWN", "verification": "NOT PERFORMED", "input": 0, "output": 0,
            "status": "FAILED", "first_run": False, "conflicts": [], "skipped": [], "notes": [],
            "problem": None, "fresh": None}


def _abort_drive(r, st):
    r["drive"] = "FAILED"
    r["problem"] = f"Google Drive unavailable ({st.kind}): {st.detail}"
    backup.print_drive_help(st)
    print("\n    *** لا تبدأ التحليل الآن: قد توجد نسخ سابقة على Drive لم تُسترجع، ")
    print("        وبدء مساحة عمل فارغة قد يؤدي لضياع/تعارض مع عملك السابق. أصلح المشكلة ثم أعد: python setup.py ***")
    return r


def _recover(r):
    r["fresh"] = _count_local_files() == 0
    if os.path.exists(MARKER):
        r["notes"].append("A previous recovery was interrupted; resuming (already-restored files are verified, not duplicated).")
        print("[!] استرجاع سابق لم يكتمل: سيُستكمل بأمان (الملفات المسترجعة سابقاً تُتحقق ولا تتكرر).")
    print("[+] مساحة العمل: " + ("فارغة → استرجاع تلقائي" if r["fresh"]
                                else "تحتوي ملفات → سيتم المقارنة وحفظ أي ملف محلي متعارض"))

    step(3, "التحقق من تثبيت rclone")
    st = backup.check_rclone_installed()
    if not st.ok:
        return _abort_drive(r, st)
    print(f"    {st.detail}")

    step(4, f"التحقق من الـ remote باسم '{backup.REMOTE_NAME}'")
    st = backup.check_remote_configured()
    if not st.ok:
        return _abort_drive(r, st)

    step(5, f"الاتصال بـ {backup.RCLONE_REMOTE}/")
    st = backup.check_connection()
    if not st.ok:
        return _abort_drive(r, st)
    r["drive"] = "CONNECTED"

    step(6, "سرد النسخ المتوفرة")
    try:
        exists, entries = backup.list_remote_dir()
    except backup.RcloneError as e:
        return _abort_drive(r, backup.DriveStatus(False, e.kind, str(e)))
    archives, manifests, legacy, other = _classify_listing(entries)
    if not (archives or manifests or legacy or other):
        r.update(first_run=True, latest="NONE", verification="N/A", status="SUCCESS")
        print("[*] ========== أول تشغيل: لا توجد نسخ احتياطية سابقة ==========")
        print("    (هذا ليس خطأ) سيتم تجهيز مساحة عمل نظيفة ومتابعة الإعداد العادي.")
        for n in range(7, TOTAL_STEPS):
            step(n, "تخطي (لا توجد نسخة للاسترجاع)")
        return r

    if legacy:
        r["notes"].append(f"Legacy '{backup.LEGACY_ARCHIVE}' found on Drive: it has no manifest/SHA-256, "
                          "so it is NOT restored automatically.")
    candidates = []
    for ts in set(archives) | set(manifests):
        if len(archives.get(ts, [])) > 1 or len(manifests.get(ts, [])) > 1:
            r["skipped"].append((ts, "duplicate files with the same name on Drive"))
        elif ts not in archives:
            r["skipped"].append((ts, "manifest without archive"))
        elif ts not in manifests:
            r["skipped"].append((ts, "archive without manifest (incomplete upload)"))
        elif backup.parse_ts(ts) is None:
            r["skipped"].append((ts, "invalid timestamp"))
        else:
            candidates.append(ts)
    candidates.sort(key=backup.parse_ts, reverse=True)
    print(f"    نسخ مكتملة: {len(candidates)}" + (f" | ناقصة/مستبعدة: {len(r['skipped'])}" if r["skipped"] else ""))

    step(7, "تحديد أحدث نسخة صالحة (بالـ manifest والطابع الزمني)")
    chosen = None
    for ts in candidates:
        work = os.path.join(RECOVERY_TMP, ts)
        print(f"    - فحص النسخة {ts}")
        try:
            manifest = _fetch_manifest(ts, work)
            step(8, f"تنزيل الأرشيف ({ts})")
            archive = os.path.join(work, manifest["archive"]["name"])
            _download(manifest["archive"]["name"], archive)
            step(9, "التحقق من SHA-256 وسلامة ZIP")
            _verify_archive(archive, manifest)
            with zipfile.ZipFile(archive) as zf:
                step(10, "التحقق من مسارات الأرشيف (path traversal)")
                _validate_members(zf, manifest)
                step(11, "الاستخراج إلى مجلد مؤقت (staging)")
                staged = _extract(zf, manifest, os.path.join(work, "staging"))
            chosen = (ts, manifest, staged)
            break
        except RecoveryError as e:
            print(f"      [-] نسخة غير صالحة، تجربة الأقدم: {e}")
            r["skipped"].append((ts, str(e)))
    if chosen is None:
        r["latest"], r["verification"] = "NONE VALID", "FAILED"
        r["problem"] = "Backups exist on Drive but none passed verification"
        print("[-] توجد نسخ على Drive لكن لا توجد نسخة صالحة. لن تُنشأ مساحة عمل فارغة بصمت.")
        print(f"    الملفات المؤقتة محفوظة للفحص في {RECOVERY_TMP}")
        print("    *** لا تبدأ التحليل قبل معالجة المشكلة (راجع الأسباب أدناه) ***")
        return r

    ts, manifest, staged = chosen
    r["latest"], r["verification"] = manifest["created_utc"], "PASSED"
    fallback = any(s[0] > ts for s in r["skipped"])  # نسخ أحدث تم استبعادها (الطابع الزمني يُقارَن نصياً)
    if fallback:
        r["notes"].append("A newer backup was skipped (invalid or incomplete); an OLDER backup was restored. "
                          "Recent work may be missing.")

    step(12, "استرجاع input و output (دون حذف أو استبدال)")
    outcomes = _restore_files(manifest, staged, ts)
    step(13, "التحقق من عدد الملفات وأحجامها")
    ok, problems = _verify_restored(manifest, outcomes)
    r["input"], r["output"] = ok["input"], ok["output"]
    r["conflicts"] = [(p, o) for p, o in outcomes.items() if o["result"] == "conflict"]
    placed = sum(1 for o in outcomes.values() if o["result"] == "placed")
    ident = sum(1 for o in outcomes.values() if o["result"] == "identical")
    print(f"    ملفات جديدة: {placed} | مطابقة موجودة مسبقاً: {ident} | تعارضات محفوظة: {len(r['conflicts'])}")
    if problems:
        r["status"], r["problem"] = "FAILED", "; ".join(problems[:5])
        print("[-] فشل التحقق بعد الاسترجاع:")
        for p in problems[:10]:
            print(f"      {p}")
        print(f"    الملفات المؤقتة محفوظة في {RECOVERY_TMP}")
        return r

    if r["conflicts"]:
        cdir = os.path.join(CONFLICTS_DIR, ts)
        with open(os.path.join(cdir, "CONFLICTS.json"), "w", encoding="utf-8") as f:
            json.dump([{"path": p, "reason": o["reason"], "local": o["target"], "remote_copy": o["stash"]}
                       for p, o in r["conflicts"]], f, indent=1, ensure_ascii=False)
    r["status"] = "PARTIAL" if (r["conflicts"] or fallback) else "SUCCESS"

    # الحالة المحلية = النسخة تماماً؟ فلا حاجة لنسخ احتياطي فوري متطابق في وضع --watch
    local = backup.workspace_files()
    if not r["conflicts"] and {a for a, _ in local} == {f["path"] for f in manifest["files"]}:
        backup.save_state(backup.fingerprint(local), manifest["archive"]["name"])

    step(14, "تنظيف الملفات المؤقتة (بعد نجاح التحقق)")
    try:
        shutil.rmtree(RECOVERY_TMP)
    except OSError as e:
        print(f"[!] تعذر حذف {RECOVERY_TMP}: {e}")
    return r


def recover_session():
    """ينفذ الخطوات 3–14 ويعيد dict بالنتيجة (الخطوة 15 = print_summary)."""
    r = _new_result()
    try:
        return _recover(r)
    except backup.RcloneError as e:  # فُقد الاتصال/المصادقة أثناء التنزيل
        return _abort_drive(r, backup.DriveStatus(False, e.kind, str(e)))
    except OSError as e:
        r["problem"] = f"{type(e).__name__}: {e}"
        r["status"] = "FAILED"
        print(f"[-] فشل الاسترجاع: {r['problem']}")
        print(f"    الملفات المؤقتة محفوظة في {RECOVERY_TMP}؛ أعد تشغيل setup.py بعد إصلاح المشكلة.")
        return r


def print_summary(r):
    step(15, "عرض ملخص الاسترجاع")
    first = r["first_run"]
    print()
    print("=" * 56)
    print("GAME REV PROJECT — SESSION RECOVERY")
    print()
    print(f"Google Drive: {r['drive']}")
    print(f"Latest backup: {'NONE (first run)' if first else r['latest']}")
    print(f"Backup verification: {'N/A (no backup to verify)' if first else r['verification']}")
    print(f"Input files restored: {r['input']}")
    print(f"Output files restored: {r['output']}")
    print(f"Recovery status: {'SUCCESS (first run - nothing to restore)' if first else r['status']}")
    print("=" * 56)
    if first:
        print("FIRST RUN: no previous backups exist. A clean workspace was initialized.")
    if r["problem"]:
        print(f"Problem: {r['problem']}")
    for ts, why in r["skipped"]:
        print(f"Skipped backup {ts}: {why}")
    for n in r["notes"]:
        print(f"Note: {n}")
    if r["conflicts"]:
        print(f"Conflicts: {len(r['conflicts'])} local file(s) were kept untouched; "
              f"the Drive version of each was saved under {CONFLICTS_DIR}{os.sep}<backup>{os.sep}")
        for p, o in r["conflicts"][:20]:
            print(f"  - {p}: {o['reason']}")
        if len(r["conflicts"]) > 20:
            print(f"  ... and {len(r['conflicts']) - 20} more (see CONFLICTS.json)")
    if r["status"] == "FAILED":
        print("DO NOT start analysis until this is resolved: previous work may exist on Drive.")
    print()


def exit_code(r):
    return {"SUCCESS": 0, "PARTIAL": 1}.get(r["status"], 2)


def configure_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="تجهيز بيئة العمل واسترجاع الجلسة من Google Drive")
    ap.add_argument("--skip-tools", action="store_true", help="تخطي تنزيل/استنساخ الأدوات (دون إنترنت)")
    ap.add_argument("--skip-game-download", action="store_true", help="تخطي تنزيل APK/assets عبر gdown")
    args = ap.parse_args(argv)
    configure_stdout()

    step(1, "تجهيز مجلدات العمل")
    os.makedirs("workspace/input", exist_ok=True)
    os.makedirs("workspace/output", exist_ok=True)
    os.makedirs("tools", exist_ok=True)

    step(2, "تثبيت وفحص أدوات التحليل")
    if args.skip_tools:
        print("[*] تم تخطي تثبيت الأدوات (--skip-tools)")
    else:
        check_prereqs()
        print("[+] جاري تجهيز الأدوات...")
        clone_tool("rea", REA_REPO_URL)
        clone_tool("AssetRipper", ASSET_RIPPER_URL)
        install_jadx()
        install_il2cppdumper()
        validate_tools()

    if OUTPUTS_BACKUP_ID:
        print("[!] OUTPUTS_BACKUP_ID لم يعد مستخدماً: الاسترجاع صار تلقائياً من gdrive:GameRevBackup/ (تم تجاهله).")

    result = recover_session()

    ok_apk = os.path.exists("workspace/input/game.apk")
    if args.skip_game_download:
        print("[*] تم تخطي تنزيل ملفات اللعبة (--skip-game-download)")
    else:
        try:
            gdown = ensure_gdown()
            print("[+] جاري تنزيل ملفات اللعبة من Google Drive (الموجود مسبقاً يُتخطى)...")
            ok_apk = download(gdown, APK_ID, "workspace/input/game.apk")
            ok_cdn = download(gdown, CDN_ASSETS_ID, "workspace/input/assets.zip")
            print("[+] ملفات اللعبة جاهزة!" if ok_apk and ok_cdn
                  else "[-] تحذير: لم تكتمل بعض التنزيلات.")
        except Exception as e:
            print(f"[-] تعذر تنزيل ملفات اللعبة: {e}")
    if ok_apk and os.path.exists("workspace/input/game.apk"):
        detect_unity_backend("workspace/input/game.apk")

    if result["status"] == "FAILED":
        print("\n[!] انتهى تجهيز الأدوات/الملفات، لكن استرجاع الجلسة لم ينجح: راجع الملخص أدناه قبل أي تحليل.")
    else:
        print("\n[+] اكتمل التجهيز.")
        if os.environ.get("GAMEREV_DISABLE_AUTO_WATCHER") != "1" and not os.environ.get("MOCK_RCLONE_ROOT"):
            print("[+] تشغيل مراقب النسخ الاحتياطي تلقائياً كل 5 دقائق...")
            backup.start_watcher(backup.DEFAULT_INTERVAL)
        else:
            print("[*] تم تعطيل تشغيل المراقب التلقائي في بيئة الاختبار.")
    print("""    الخطوات اليدوية المتبقية:
    1) ربط REA بالوكيل:  npx rea-agents setup   (من مجلد خارج المشروع)
    2) افتح المجلد في Antigravity واقرأ AGENTS.md
    3) فحص المراقب: python backup.py --status
    4) إيقاف المراقب: python backup.py --stop
""")
    print_summary(result)
    return exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
