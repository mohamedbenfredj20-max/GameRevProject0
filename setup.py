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