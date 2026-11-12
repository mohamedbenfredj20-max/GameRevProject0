import os
import sys
import zipfile
import subprocess

print("[+] جاري إعداد مجلدات العمل والبيئة...")
os.makedirs("workspace/input", exist_ok=True)
os.makedirs("workspace/output", exist_ok=True)
os.makedirs("tools", exist_ok=True)

# معرّفات ملفات Google Drive (الجزء بعد id= في الرابط)
APK_ID = "1CPBAnEnuud_4o77ikUh35SePV2M9louU"
CDN_ASSETS_ID = "1PEThMxR67yxVGusC7RY9UaEW8_w6a-bi"
OUTPUTS_BACKUP_ID = ""  # ضع هنا معرّف ملف outputs_backup.zip بعد رفعه (اتركه فارغاً إن لم يوجد)

REA_REPO_URL = "https://github.com/morluto/rea.git"
ASSET_RIPPER_URL = "https://github.com/AssetRipper/AssetRipper.git"


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
        # gdown يتعامل مع صفحة التأكيد للملفات الكبيرة في Drive
        result = gdown.download(id=file_id, output=dest, quiet=False)
        return result is not None
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
        print(f"[+] تم تثبيت {name} بنجاح!")
    except Exception as e:
        print(f"[-] خطأ أثناء تثبيت {name}: {e}")


gdown = ensure_gdown()

print("[+] جاري تنزيل ملفات اللعبة من Google Drive...")
ok_apk = download(gdown, APK_ID, "workspace/input/game.apk")
ok_cdn = download(gdown, CDN_ASSETS_ID, "workspace/input/assets.zip")
if ok_apk and ok_cdn:
    print("[+] تم تنزيل ملفات اللعبة بنجاح!")
else:
    print("[-] تحذير: لم تكتمل بعض التنزيلات، تحقق من صلاحيات المشاركة في Drive.")

if OUTPUTS_BACKUP_ID:
    backup_path = "workspace/outputs_backup.zip"
    if download(gdown, OUTPUTS_BACKUP_ID, backup_path):
        try:
            with zipfile.ZipFile(backup_path) as z:
                z.extractall("workspace/output")
            print("[+] تم استرجاع المخرجات السابقة إلى workspace/output")
        except Exception as e:
            print(f"[-] تنبيه: تعذر فك ضغط المخرجات السابقة: {e}")

print("[+] جاري تجهيز أدوات التحليل (REA & AssetRipper)...")
clone_tool("rea", REA_REPO_URL)
clone_tool("AssetRipper", ASSET_RIPPER_URL)

print("[+] البيئة، الملفات، والأدوات جاهزة!")