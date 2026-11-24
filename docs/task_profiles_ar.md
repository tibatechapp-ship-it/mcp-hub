# ملفات المهام بالعربية

> ملفات المهام وصف بياناتي فقط؛ لا تمنح صلاحيات جديدة ولا تنفّذ أدوات. السجل لا يزال هو المصدر الحقيقي لحقول `access_mode` و`requires_env`.

عدد الملفات: **5**.

| المعرّف | الاسم العربي | الوصول الافتراضي | التصنيفات المسموحة | الوسوم المفضّلة |
|---|---|---|---|---|
| `ai-agent-builder` | بناء وكيل ذكي | `read-only` | arabic, catalog, code, search | memory-context, rag-web-search, action-execution, eval-observability, localization |
| `automation-ops` | الأتمتة والتشغيل | `read-write` | arabic, browser, catalog, code, database, files | action-execution, execution, security-scan |
| `code-review` | مراجعة الكود | `read-only` | catalog, code, files | diff, symbol-search, static-analysis, eval-observability |
| `docs-localization` | التوثيق والتعريب | `read-write` | arabic, catalog, conversion, search | localization, rag-web-search, execution |
| `quality-security-audit` | تدقيق الجودة والأمان | `read-only` | browser, catalog, code, files | security-scan, static-analysis, eval-observability, execution |

## تفاصيل الملفات

### بناء وكيل ذكي (`ai-agent-builder`)

تجميع أدوات البحث والذاكرة والتقييم لبناء وكيل يعمل بصلاحيات قراءة فقط بشكل افتراضي.

| الخطوة | العنوان | وسوم الأدوات | الوصول |
|---|---|---|---|
| `gather_context` | جمع السياق والذاكرة | memory-context, rag-web-search | `read-only` |
| `plan_actions` | تخطيط الإجراءات | action-execution | `read-only` |
| `evaluate` | تقييم النتائج | eval-observability | `read-only` |

### الأتمتة والتشغيل (`automation-ops`)

تشغيل إجراءات كتابة وتنفيذ على الملفات وقواعد البيانات والمتصفح مع طلب موافقة صريحة قبل التنفيذ.

> يتطلب موافقة صريحة قبل أي خطوة `read-write`.

| الخطوة | العنوان | وسوم الأدوات | الوصول |
|---|---|---|---|
| `inspect_state` | فحص الحالة | eval-observability | `read-only` |
| `request_approval` | طلب موافقة صريحة | eval-observability | `read-only` |
| `execute_change` | تنفيذ التغيير | action-execution, execution | `read-write` |

### مراجعة الكود (`code-review`)

مراجعة الفروق والرموز وجودة التغييرات دون تعديل المستودع أو تنفيذ أوامر.

| الخطوة | العنوان | وسوم الأدوات | الوصول |
|---|---|---|---|
| `collect_diff` | جمع الفروق والسياق | diff | `read-only` |
| `map_symbols` | ربط الرموز والمراجع | symbol-search, static-analysis | `read-only` |
| `report_findings` | تلخيص الملاحظات | eval-observability | `read-only` |

### التوثيق والتعريب (`docs-localization`)

تحويل المحتوى وترجمته وربطه بمصادر العربية مع السماح بتعديل ملفات التوثيق فقط.

| الخطوة | العنوان | وسوم الأدوات | الوصول |
|---|---|---|---|
| `collect_sources` | جمع المصادر | rag-web-search | `read-only` |
| `localize` | التعريب والتحويل | localization, execution | `read-write` |
| `verify_terms` | التحقق من المصطلحات | eval-observability | `read-only` |

### تدقيق الجودة والأمان (`quality-security-audit`)

فحص الكود والملفات والمتصفح بحثاً عن المخاطر ومؤشرات الجودة دون تعديل البيانات.

| الخطوة | العنوان | وسوم الأدوات | الوصول |
|---|---|---|---|
| `scan_surface` | مسح سطح الفحص | security-scan, static-analysis | `read-only` |
| `inspect_behaviour` | فحص السلوك | execution, eval-observability | `read-only` |
| `summarise_risks` | تلخيص المخاطر | eval-observability | `read-only` |
