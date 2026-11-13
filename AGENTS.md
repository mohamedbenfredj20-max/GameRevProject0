# تعليمات الوكيل (Agent Instructions)

مشروع **تعليمي** للهندسة العكسية للعبة أندرويد مبنية بـ **Unity** بنمط **IL2CPP** (مؤكد: يوجد `libil2cpp.so` و`global-metadata.dat`، ولا يوجد Mono).

> ⚠️ هذا الملف هو الأساسي لهذا المشروع. تجاهل `tools/rea/AGENTS.md` فهو لتطوير أداة REA نفسها.

## القواعد
- `workspace/input/` للقراءة فقط، ولا تعدّل الـ APK.
- احفظ النتائج في `workspace/output/` (مجلدات: `01_manifest`, `02_il2cpp`, `03_cdn_assets`, `04_notes`).
- لا تنسخ ملفات من الـ APK إلى المخرجات (مثل `libil2cpp.so` و`global-metadata.dat`)، بل أشِر إلى مسارها في `input`.
- كل استنتاج يُكتب في `workspace/output/04_notes/findings.md` مع **الدليل** (اسم الملف + الصنف/الدالة)، وفرّق بين المؤكد والتخمين.
- لا تتفاعل مع خوادم اللعبة الحية، ولا تحاول تجاوز حماية أو الغش.

## الأدوات
- `tools/Il2CppDumper/`: يستخرج `dump.cs` و`script.json` و`il2cpp.h` و`DummyDll/`.
  ```
  Il2CppDumper.exe <libil2cpp.so> <global-metadata.dat> <مجلد الإخراج>
  ```
  المسار داخل الـ APK: `lib/arm64-v8a/libil2cpp.so` و`assets/bin/Data/Managed/Metadata/global-metadata.dat`.
- `tools/jadx/`: لـ `AndroidManifest.xml` وكود Java/Kotlin الخاص بأندرويد.
- `tools/AssetRipper/`: لأصول Unity (يحتاج بناء بـ .NET SDK).
- `tools/rea/`: خادم MCP للتحليل (`npx rea-agents setup`).

## ما نعرفه حالياً
- `DummyDll/` و`dump.cs` يعطيان **أسماء الأصناف والدوال وتواقيعها فقط**، وليس منطق الدوال.
- المنطق الفعلي داخل `libil2cpp.so` كلغة آلة ARM64؛ لقراءته استخدم Ghidra/IDA مع `script.json` و`il2cpp.h`.

## سير العمل
1. اطرح سؤالاً محدداً (مثال: كيف تُحمَّل بيانات البطاقات؟).
2. ابحث عنه في `dump.cs`، ثم افتح الـ DLL المناسب (مثل `Game.dll`) في ILSpy/dnSpy لفهم البنية.
3. للمنطق الداخلي، حدّد عنوان الدالة من `script.json` وحلله في Ghidra.
4. دوّن النتيجة والدليل في `findings.md`.

## أسلوب التقرير
بالعربية، موجز، مع قائمة بالملفات المنتجة ومسار كل معلومة.
