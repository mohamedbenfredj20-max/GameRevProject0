import os
import sys
import json
import shutil
import zipfile
import subprocess
import urllib.request

print("[+] جاري إعداد مجلدات العمل والبيئة...")
os.makedirs("workspace/input", exist_ok=True)
os.makedirs("workspace/output", exist_ok=True)
os.makedirs("tools", exist_ok=True)

# معرّفات ملفات Google Drive (الجزء بعد id= في الرابط)
APK_ID = "1CPBAnEnuud_4o77ikUh35SePV2M9louU"
CDN_ASSETS_ID = "1PEThMxR67yxVGusC7RY9UaEW8_w6a-bi"
OUTPUTS_BACKUP_ID = ""  # معرّف ملف outputs_backup.zip بعد رفعه (فارغ إن لم يوجد)

REA_REPO_URL = "https://github.com/morluto/rea.git"
ASSET_RIPPER_URL = "https://github.com/AssetRipper/AssetRipper.git"
JADX_API = "https://api.github.com/repos/skylot/jadx/releases/latest"
IL2CPPDUMPER_API = "https://api.github.com/repos/Perfare/Il2CppDumper/releases/latest"


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


check_prereqs()

gdown = ensure_gdown()
print("[+] جاري تنزيل ملفات اللعبة من Google Drive...")
ok_apk = download(gdown, APK_ID, "workspace/input/game.apk")
ok_cdn = download(gdown, CDN_ASSETS_ID, "workspace/input/assets.zip")
print("[+] تم تنزيل ملفات اللعبة!" if ok_apk and ok_cdn
      else "[-] تحذير: لم تكتمل بعض التنزيلات.")

if OUTPUTS_BACKUP_ID:
    bp = "workspace/outputs_backup.zip"
    if download(gdown, OUTPUTS_BACKUP_ID, bp):
        try:
            with zipfile.ZipFile(bp) as z:
                z.extractall("workspace/output")
            print("[+] تم استرجاع المخرجات السابقة")
        except Exception as e:
            print(f"[-] تعذر فك ضغط المخرجات السابقة: {e}")

print("[+] جاري تجهيز الأدوات...")
clone_tool("rea", REA_REPO_URL)
clone_tool("AssetRipper", ASSET_RIPPER_URL)
install_jadx()
install_il2cppdumper()

if ok_apk:
    detect_unity_backend("workspace/input/game.apk")

print("""
[+] اكتمل التجهيز. الخطوات اليدوية المتبقية:
    1) ربط REA بالوكيل:  npx rea-agents setup   (من مجلد خارج المشروع)
    2) افتح المجلد في Antigravity واقرأ AGENTS.md
""")
