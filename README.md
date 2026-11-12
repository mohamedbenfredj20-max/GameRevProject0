# Game Reverse Engineering Pipeline (GameRevProject0)

بيئة عمل مؤتمتة للهندسة العكسية لألعاب الأندرويد بغرض التعلم، مصممة للعمل في بيئات RDP المؤقتة مع مزامنة الملفات والأدوات تلقائياً.

## 📂 هيكل المشروع
- `setup.py`: تجهيز البيئة بالكامل (المجلدات، تنزيل APK وأصول CDN من Google Drive، استنساخ الأدوات).
- `backup.py`: ضغط `workspace/output/` ورفعه إلى Google Drive عبر `rclone`.
- `workspace/input/`: ملفات الإدخال الخام (`game.apk`, `assets.zip`). مستبعد من Git عبر `.gitignore`.
- `workspace/output/`: نتائج ومخرجات التحليل (مستبعد من Git أيضاً، لذا استخدم `backup.py`).
- `tools/`: أدوات التحليل المستنسخة تلقائياً:
  - [REA](https://github.com/morluto/rea)
  - [AssetRipper](https://github.com/AssetRipper/AssetRipper)

## 🚀 التشغيل في أي جلسة RDP جديدة
```bash
python setup.py
```
يثبّت `gdown` تلقائياً، وينزّل الملفات، ويستنسخ الأدوات. الملفات الموجودة مسبقاً تُتخطى.

## 🔍 سير العمل
1. شغّل `python setup.py`.
2. افتح المجلد في Antigravity واطلب منه تحليل `workspace/input/`.
3. احفظ النتائج في `workspace/output/`.
4. قبل إغلاق الجلسة شغّل `python backup.py`.

## 💾 النسخ الاحتياطي (مرة واحدة لكل جلسة)
1. ثبّت [rclone](https://rclone.org/install/).
2. شغّل `rclone config` وأنشئ remote باسم `gdrive`.
3. شغّل `python backup.py`.

لاسترجاع المخرجات في جلسة جديدة: ارفع `outputs_backup.zip` إلى Drive، وضع معرّفه في `OUTPUTS_BACKUP_ID` داخل `setup.py`.

## ⚠️ ملاحظة
هذا المشروع لأغراض التعلم فقط. لا تعِد نشر أصول اللعبة أو الكود المستخرج منها، ولا تستخدمه لتجاوز الحماية أو للغش في الألعاب الأونلاين.
