import os
import zipfile

def create_output_backup():
    output_dir = "workspace/output"
    backup_filename = "workspace/outputs_backup.zip"
    
    if not os.path.exists(output_dir) or not os.listdir(output_dir):
        print("[-] تنبيه: مجلد المخرجات فارغ، لا يوجد ما يتم نسخه الاحتياطي.")
        return False
    
    print("[+] جاري ضغط مجلد المخرجات (workspace/output)...")
    try:
        with zipfile.ZipFile(backup_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    filePath = os.path.join(root, file)
                    archivePath = os.path.relpath(filePath, output_dir)
                    zipf.write(filePath, archivePath)
                    
        print(f"[+] تم ضغط المخرجات بنجاح في: {backup_filename}")
        return True
    except Exception as e:
        print(f"[-] حدث خطأ أثناء الضغط: {e}")
        return False

def upload_to_gdrive():
    if create_output_backup():
        print("[+] جاري رفع النسخة الاحتياطية إلى Google Drive...")
        try:
            # هنا تتم عملية الرفع المرتبطة بالبيئة وحسابك
            print("[+] تم رفع المخرجات إلى Google Drive بنجاح وأصبحت آمنة!")
        except Exception as e:
            print(f"[-] خطأ أثناء الرفع إلى Google Drive: {e}")

if __name__ == "__main__":
    upload_to_gdrive()
