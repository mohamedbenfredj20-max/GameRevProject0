import os
import shutil
import subprocess
import zipfile

OUTPUT_DIR = "workspace/output"
BACKUP_FILE = "workspace/outputs_backup.zip"
# اسم remote في rclone (يُنشأ مرة واحدة بالأمر: rclone config) ومجلد الوجهة
RCLONE_REMOTE = "gdrive:GameRevBackup"

# ملفات هي نسخ من محتوى الـ APK (موجودة أصلاً في workspace/input) فلا حاجة لحفظها
EXCLUDED_NAMES = {"libil2cpp.so", "global-metadata.dat", "libunity.so", "libmain.so"}
EXCLUDED_EXTS = (".apk",)


def create_output_backup():
    if not os.path.exists(OUTPUT_DIR) or not os.listdir(OUTPUT_DIR):
        print("[-] تنبيه: مجلد المخرجات فارغ، لا يوجد ما يتم نسخه.")
        return False
    print("[+] جاري ضغط مجلد المخرجات...")
    try:
        with zipfile.ZipFile(BACKUP_FILE, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(OUTPUT_DIR):
                for f in files:
                    if f in EXCLUDED_NAMES or f.lower().endswith(EXCLUDED_EXTS):
                        continue
                    full = os.path.join(root, f)
                    zipf.write(full, os.path.relpath(full, OUTPUT_DIR))
        print(f"[+] تم الضغط في: {BACKUP_FILE}")
        return True
    except Exception as e:
        print(f"[-] خطأ أثناء الضغط: {e}")
        return False