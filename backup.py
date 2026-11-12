import os
import shutil
import subprocess
import zipfile

OUTPUT_DIR = "workspace/output"
BACKUP_FILE = "workspace/outputs_backup.zip"
# اسم remote في rclone (يُنشأ مرة واحدة بالأمر: rclone config) ومجلد الوجهة
RCLONE_REMOTE = "gdrive:GameRevBackup"


def create_output_backup():
    if not os.path.exists(OUTPUT_DIR) or not os.listdir(OUTPUT_DIR):
        print("[-] تنبيه: مجلد المخرجات فارغ، لا يوجد ما يتم نسخه.")
        return False
    print("[+] جاري ضغط مجلد المخرجات...")
    try:
        with zipfile.ZipFile(BACKUP_FILE, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(OUTPUT_DIR):
                for f in files:
                    full = os.path.join(root, f)
                    zipf.write(full, os.path.relpath(full, OUTPUT_DIR))
        print(f"[+] تم الضغط في: {BACKUP_FILE}")
        return True
    except Exception as e:
        print(f"[-] خطأ أثناء الضغط: {e}")
        return False


def upload_to_gdrive():
    if not create_output_backup():
        return False
    if shutil.which("rclone") is None:
        print("[-] لم يتم الرفع: أداة rclone غير مثبتة.")
        print("    ثبّتها (https://rclone.org/install/) ثم شغّل: rclone config")
        print(f"    الملف جاهز يدوياً هنا: {BACKUP_FILE}")
        return False
    print("[+] جاري الرفع إلى Google Drive...")
    result = subprocess.run(["rclone", "copy", BACKUP_FILE, RCLONE_REMOTE])
    if result.returncode == 0:
        print("[+] تم الرفع بنجاح.")
        return True
    print("[-] فشل الرفع، تحقق من إعداد rclone (اسم الـ remote).")
    return False


if __name__ == "__main__":
    upload_to_gdrive()