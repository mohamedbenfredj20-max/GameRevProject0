import os
import urllib.request
import zipfile
import subprocess

print("[+] جاري إعداد مجلدات العمل والبيئة...")
os.makedirs("workspace/input", exist_ok=True)
os.makedirs("workspace/output", exist_ok=True)
os.makedirs("tools", exist_ok=True)

# روابط التحميل المباشر لملفاتك من Google Drive
APK_DIRECT_URL = "https://drive.usercontent.google.com/download?id=1CPBAnEnuud_4o77ikUh35SePV2M9louU&export=download&authuser=0"
CDN_ASSETS_URL = "https://drive.usercontent.google.com/download?id=1PEThMxR67yxVGusC7RY9UaEW8_w6a-bi&export=download&authuser=0"
OUTPUTS_BACKUP_URL = "ضع_رابط_المخرجات_السابقة_هنا_إن_وجد"

# روابط أدوات التحليل والهندسة العكسية
REA_REPO_URL = "https://github.com/morluto/rea.git"
ASSET_RIPPER_URL = "https://github.com/AssetRipper/AssetRipper.git"

print("[+] جاري تنزيل ملفات اللعبة الأساسية من Google Drive...")
try:
    urllib.request.urlretrieve(APK_DIRECT_URL, "workspace/input/game.apk")
    urllib.request.urlretrieve(CDN_ASSETS_URL, "workspace/input/assets.zip")
    print("[+] تم تنزيل ملفات اللعبة بنجاح!")
except Exception as e:
    print(f"[-] خطأ أثناء تنزيل ملفات اللعبة: {e}")

# استرجاع المخرجات السابقة إذا توفر الرابط
if OUTPUTS_BACKUP_URL and "ضع_رابط" not in OUTPUTS_BACKUP_URL:
    try:
        backup_path = "workspace/outputs_backup.zip"
        urllib.request.urlretrieve(OUTPUTS_BACKUP_URL, backup_path)
        with zipfile.ZipFile(backup_path, 'r') as zip_ref:
            zip_ref.extractall("workspace/output")
        print("[+] تم استرجاع المخرجات السابقة بنجاح إلى workspace/output !")
    except Exception as e:
        print(f"[-] تنبيه: لم يتم استرجاع المخرجات السابقة: {e}")

# تحميل واستنساخ أدوات التحليل تلقائياً داخل مجلد tools/
print("[+] جاري تحميل وتجهيز أدوات التحليل (REA & AssetRipper)...")

# 1. تثبيت REA
rea_path = "tools/rea"
if not os.path.exists(rea_path):
    try:
        print("[+] جاري استنساخ أداة REA...")
        subprocess.run(["git", "clone", REA_REPO_URL, rea_path], check=True)
        print("[+] تم تثبيت REA بنجاح!")
    except Exception as e:
        print(f"[-] خطأ أثناء تثبيت REA: {e}")
else:
 موجودة مسبقاً.")

# 2. تثبيت AssetRipper
ripper_path = "tools/AssetRipper"
if not os.path.exists(ripper_path):
    try:
        print("[+] جاري استنساخ أداة AssetRipper...")
        subprocess.run(["git", "clone", ASSET_RIPPER_URL, ripper_path], check=True)
        print("[+] تم تثبيت AssetRipper بنجاح!")
    except Exception as e:
        print(f"[-] خطأ أثناء تثبيت AssetRipper: {e}")
else:
    print("[*] أداة AssetRipper موجودة مسبقاً.")

print("[+] البيئة، الملفات، والأدوات جاهزة بالكامل للعمل!")
