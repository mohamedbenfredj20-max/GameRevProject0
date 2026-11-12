# تعليمات الوكيل (Agent Instructions)

مشروع **تعليمي** للهندسة العكسية للعبة أندرويد مبنية بـ **Unity**. الهدف فهم كيف تعمل اللعبة، لا نشر أصولها ولا الغش.

## القواعد
- لا تعدّل شيئاً في `workspace/input/` (للقراءة فقط).
- احفظ كل النتائج في `workspace/output/` داخل مجلدات منظمة (`apk/`, `assets/`, `code/`, `notes/`).
- اكتب ما تكتشفه في `workspace/output/notes/findings.md` مع الدليل (اسم الملف، الدالة، السطر).
- فرّق دائماً بين ما **تأكدت منه** وما هو **تخمين**.
- لا تحاول تجاوز حماية أو تعديل اللعبة للغش أو التفاعل مع خوادمها الحية.

## الملفات
- `workspace/input/game.apk`: حزمة اللعبة.
- `workspace/input/assets.zip`: أصول CDN (غالباً AssetBundles).
- `tools/jadx/`: فك كود Java/Kotlin وبيان الـ Manifest (`tools/jadx/bin/jadx`).
- `tools/AssetRipper/`: استخراج أصول Unity (مستودع مصدري يحتاج بناء بـ .NET SDK).
- `tools/rea/`: خادم MCP للهندسة العكسية (يُفعَّل عبر `npx rea-agents setup`).

## سير العمل المقترح
1. **الاستطلاع:** افتح الـ APK كملف zip، اقرأ `AndroidManifest.xml` عبر JADX، سجّل الحزمة والإصدار والصلاحيات.
2. **نمط الكود:** هل يوجد `libil2cpp.so` + `global-metadata.dat` (IL2CPP) أم `Assembly-CSharp.dll` (Mono)؟
   - Mono: حلّل الـ DLL مباشرة.
   - IL2CPP: استخدم Il2CppDumper لاستخراج أسماء الأصناف والدوال.
3. **الأصول:** افحص `assets.zip` (AssetBundles) وحلّلها بـ AssetRipper.
4. **التوثيق:** لخّص بنية اللعبة (المشاهد، الأنظمة، تدفق البيانات) في `notes/`.

## أسلوب التقرير
أجب بالعربية، بإيجاز، مع قائمة بالملفات التي أنتجتها وأين وجدت كل معلومة.