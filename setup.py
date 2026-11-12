import os
import urllib.request
import zipfile

print("[+] جاري إعداد مجلدات العمل والبيئة...")
os.makedirs("workspace/input", exist_ok=True)
os.makedirs("workspace/output", exist_ok=True)

# روابط التحميل المباشر لملفاتك من Google Drive (قم بتحديث رابط المخرجات المحفوظة لاحقاً عند توفره)
APK_DIRECT_URL = "https://drive.usercontent.google.com/download?id=1CPBAnEnuud_4o77ikUh35SePV2M9louU&export=download&authuser=0"
CDN_ASSETS_URL = "https://drive.usercontent.google.com/download?id=1PEThMxR67yxVGusC7RY9UaEW8_w6a-bi&export=download&authuser=0"
# (اختياري) رابط النسخة الاحتياطية السابقة للمخرجات على Google Drive
OUTPUTS_BACKUP_URL = "ضع_رابط_المخرجات_السابقة_هنا_إن_وجد"

print("[+] جاري تنزيل ملفات اللعبة الأساسية من Google Drive...")
try:
    urllib.request.urlretrieve(APK_DIRECT_URL, "workspace/input/game.apk")
    print("[+] تم تنزيل ملف game.apk بنجاح!")
    
    urllib.request.urlretrieve(CDN_ASSETS_URL, "workspace/input/assets.zip")
    print("[+] تم تنزيل ملف assets.zip بنجاح!")
except Exception as e:
    print(f"[-] خطأ أثناء تنزيل ملفات اللعبة: {e}")

# محاولة استرجاع المخرجات القديمة إذا كان الرابط متوفراً
if OUTPUTS_BACKUP_URL and "ضع_رابط" not in OUTPUTS_BACKUP_URL:
    print("[+] جاري استرجاع المخرجات السابقة من النسخة الاحتياطية...")
    try:
        backup_path = "workspace/outputs_backup.zip"
        urllib.request.urlretrieve(OUTPUTS_BACKUP_URL, backup_path)
        with zipfile.ZipFile(backup_path, 'r') as zip_ref:
            zip_ref.extractall("workspace/output")
        print("[+] تم استرجاع جميع المخرجات السابقة بنجاح إلى workspace/output !")
    except Exception as e:
        print(f"[-] تنبيه: لم يتم استرجاع المخرجات السابقة أو أنها غير متوفرة بعد: {e}")

print("[+] البيئة جاهزة بالكامل للعمل وتحليل الملفات!")
