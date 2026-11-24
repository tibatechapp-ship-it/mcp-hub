# فهرس خوادم وأدوات MCP بالعربية

> هذا الفهرس مولّد من `registry/mcp_servers.json`. المعرّفات البرمجية (`id`، `name`، `command`، `args`، `env`) تبقى بالإنجليزية كما هي؛ أضفنا حقول `name_ar` و`description_ar` و`category_ar` و`params_ar` للعرض فقط، ثم أضفنا `tags` و`task_profiles` و`access_mode` و`requires_env` للاستعلام عن القدرات.

عدد الخوادم: **20** — عدد الأدوات: **140**.

## ملاحظة مهمة

قوائم الأدوات أدناه هي الأدوات الموثقة الشائعة لكل خادم؛ قد تختلف القائمة الدقيقة بحسب إصدار الحزمة المثبّتة. تحقّق دائماً من `tools/list` في الخادم المثبّت قبل الاعتماد على أداة معينة.

## القدرات وملفات المهام

- `tags`: وسوم قصيرة مثل `diff` و`rag-web-search` و`action-execution` و`eval-observability`.
- `task_profiles`: معرّفات ملفات المهام الخمسة؛ انظر [ملفات المهام](task_profiles_ar.md).
- `access_mode`: `read-only` للقراءة أو `read-write` للتعديل والتنفيذ.
- `requires_env`: أسماء متغيرات البيئة المطلوبة؛ `ready_only` يستبعد السجلات غير الجاهزة والـ fixture.

```bash
mcp-hub query-catalog --profile code-review --access-mode read-only
mcp-hub query-catalog --tag rag-web-search --ready-only
```

## طبقة التعريب في وقت التشغيل

عند ضبط `MCP_HUB_AR_RUNTIME=1` يلفّ `mcp-hub` معالج `tools/list` ويضيف وصفاً عربياً قبل الوصف الإنجليزية الأصلي. أسماء الأدوات و`inputSchema` لا تتغير أبداً، وأي فشل في الترجمة أو الكاش أو الشبكة يُمرّر الاستجابة الأصلية كما هي (Fail-Open).

- `MCP_HUB_AR_CACHE`: مسار الكاش المحلي؛ الافتراضي `~/.cache/mcp-hub/arabic_runtime.json`.
- `MCP_HUB_AR_UPSTREAM_URL`: مصدر التحديث الوحيد؛ يجب أن يكون رابط raw لملف السجل على الفرع `main` من هذا المستودع، وأي رابط آخر يُرفض.
- `MCP_HUB_AR_LIVE=1`: فحص المصدر عند كل استدعاء `tools/list` مع طلب شرطي، و`MCP_HUB_AR_SYNC_TTL` يضبط الفاصل بالثواني (الافتراضي 0 يعني فحصاً فورياً).
- للتحديث اليدوي: `mcp-hub sync-arabic --upstream <url> --cache <path>`.

## فهرس الخوادم

### فهرس خوادم MCP المحلي (`mcp-hub`)

- **الحالة:** existing
- **النقل:** stdio
- **المصدر:** `mcp-hub/examples/cursor-mcp.json; mcp-hub/src/mcp_hub/mcp_server.py`
- **التشغيل:** `python -m mcp_hub.mcp_server`
- **متغيرات البيئة المطلوبة:** `MCP_HUB_DB` (بدون قيم في هذا الملف)
- **شرح المدخلات:**
  - `MCP_HUB_DB`: المسار الكامل لقاعدة بيانات الفهرس المحلية.
- **القدرات:** وسوم: eval-observability, memory-context, rag-web-search | المهام: ai-agent-builder, quality-security-audit, docs-localization | الوصول: `read-only`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `search_servers` | البحث في الخوادم | يبحث في الفهرس بالاسم أو الناشر أو الوصف أو التصنيف ويعيد قائمة مختصرة. | `eval-observability`, `memory-context`, `rag-web-search` | `ai-agent-builder`, `quality-security-audit`, `docs-localization` | `read-only` | — | `query`: كلمة البحث؛ `limit`: أقصى عدد للنتائج؛ `mode`: نمط البحث: contains أو fts |
| `get_server` | تفاصيل خادم | يعيد السجل الكامل لخادم واحد مع بيانات الإثراء. | `eval-observability`, `memory-context` | `ai-agent-builder`, `quality-security-audit` | `read-only` | — | `server_id`: المعرّف الرقمي للخادم |
| `list_categories` | قائمة التصنيفات | يعيد التصنيفات مع عدد الخوادم في كل تصنيف. | `eval-observability`, `memory-context` | `ai-agent-builder`, `quality-security-audit` | `read-only` | — | — |
| `servers_by_category` | خوادم تصنيف واحد | يعرض خوادم تصنيف محدد. | `eval-observability`, `memory-context` | `ai-agent-builder`, `quality-security-audit` | `read-only` | — | `category`: اسم التصنيف؛ `limit`: أقصى عدد للنتائج |
| `provenance_conflicts` | تضاربات المصدر | يحدد الحقول التي تختلف مصادرها لخادم واحد. | `eval-observability`, `memory-context` | `ai-agent-builder`, `quality-security-audit` | `read-only` | — | `server_id`: المعرّف الرقمي للخادم |
| `provenance_events` | سجل أحداث الخادم | يعيد سجل التغييرات المرتبط بخادم واحد. | `eval-observability`, `memory-context` | `ai-agent-builder`, `quality-security-audit` | `read-only` | — | `server_id`: المعرّف الرقمي للخادم؛ `limit`: عدد الأحداث |
| `doctor_report` | تقرير صحة الفهرس | يشغّل فحوصات القراءة فقط ويعرض حالة قاعدة البيانات. | `eval-observability`, `memory-context` | `ai-agent-builder`, `quality-security-audit` | `read-only` | — | — |
| `suggest_category` | اقتراح تصنيف | يقترح تصنيفات لخادم باستخدام المصنّف الحتمي. | `eval-observability`, `memory-context` | `ai-agent-builder`, `quality-security-audit` | `read-only` | — | `server_id`: المعرّف الرقمي للخادم |
| `query_catalog` | الاستعلام عن الفهرس | يفلتر خوادم وأدوات الفهرس حسب التصنيف أو الوسم أو ملف المهمة أو مستوى الوصول أو الجاهزية. | `eval-observability`, `memory-context` | `ai-agent-builder`, `automation-ops`, `code-review`, `docs-localization`, `quality-security-audit` | `read-only` | — | `kind`: نوع السجل: all أو server أو tool؛ `category`: التصنيف مثل code أو database؛ `tag`: وسم القدرة مثل diff أو rag-web-search؛ `profile`: معرف ملف المهمة؛ `access_mode`: read-only أو read-write؛ `ready_only`: عرض السجلات الجاهزة فقط؛ `limit`: أقصى عدد للنتائج |

### ملف توافق بروتوكول cua-driver (`cua-driver-fixture`)

- **الحالة:** fixture
- **النقل:** none
- **المصدر:** `qwen-code/packages/cua-driver/compat-fixtures/mcp.json`
- **القدرات:** وسوم: action-execution, execution | المهام: ai-agent-builder, quality-security-audit, automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `list_apps` | قائمة التطبيقات | اختبار: سرد التطبيقات المتاحة. | `action-execution`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `list_windows` | قائمة النوافذ | اختبار: سرد النوافذ المفتوحة. | `action-execution`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `get_window_state` | حالة النافذة | اختبار: قراءة حالة نافذة. | `action-execution`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `launch_app` | تشغيل تطبيق | اختبار: تشغيل تطبيق. | `action-execution`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | — |
| `click` | نقر | اختبار: النقر على عنصر. | `action-execution`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | — |
| `type_text` | إدخال نص | اختبار: كتابة نص. | `action-execution`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | — |
| `press_key` | ضغط مفتاح | اختبار: الضغط على مفتاح. | `action-execution`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | — |

## أدوات عربية

### أدوات عربية (`arabic-tools`)

- **الحالة:** existing
- **النقل:** stdio
- **المصدر:** `~/arabic_mcp.py`
- **التشغيل:** `python3 /absolute/path/to/arabic_mcp.py`
- **القدرات:** وسوم: localization, rag-web-search | المهام: ai-agent-builder, docs-localization | الوصول: `read-only`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `greet` | تحية | يعيد تحية عربية للاسم المحدد. | `localization` | `ai-agent-builder`, `docs-localization` | `read-only` | — | `name`: الاسم |
| `prayer_times` | مواقيت الصلاة | يجلب مواقيت الصلاة لمدينة عبر API الأذان. | `localization` | `ai-agent-builder`, `docs-localization` | `read-only` | — | `city`: المدينة؛ `country`: البلد |
| `quran_search` | البحث في القرآن | يبحث عن كلمة في القرآن ويعيد الآيات مع اسم السورة ورقمها. | `localization`, `rag-web-search` | `ai-agent-builder`, `docs-localization` | `read-only` | — | `keyword`: كلمة البحث؛ `language`: لغة البحث؛ `limit`: عدد النتائج |
| `translate_to_arabic` | الترجمة إلى العربية | يترجم نصاً إنجليزياً إلى العربية. | `localization` | `ai-agent-builder`, `docs-localization` | `read-only` | — | `text`: النص المطلوب ترجمته |

## إدارة الكود

### فحص سريع للمشاريع (`fast-anything`)

- **الحالة:** existing
- **النقل:** stdio
- **المصدر:** `fast-anything/.mcp.json`
- **التشغيل:** `fast-anything mcp`
- **القدرات:** وسوم: action-execution, eval-observability, execution, security-scan, static-analysis | المهام: code-review, ai-agent-builder, quality-security-audit, automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `fast_scan` | فحص سريع | يفحص المشروع أو المسار المحدد. | `execution`, `security-scan`, `static-analysis` | `code-review`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `fast_port` | فحص المنافذ | يفحص المنافذ المستخدمة أو المتاحة. | `execution`, `static-analysis` | `code-review`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `fast_verify` | تحقق | يتحقق من صحة الإعدادات أو النتائج. | `eval-observability`, `execution`, `static-analysis` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `fast_enable` | تفعيل | يفعّل ميزة أو مكوّناً. | `action-execution`, `execution`, `static-analysis` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | — |
| `fast_disable` | تعطيل | يعطّل ميزة أو مكوّناً. | `action-execution`, `execution`, `static-analysis` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | — |
| `fast_bench` | قياس الأداء | يقيس زمن التنفيذ أو الأداء. | `eval-observability`, `execution`, `static-analysis` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `fast_report` | تقرير | ينشئ تقريراً عن نتائج الفحص. | `eval-observability`, `execution`, `static-analysis` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `fast_status` | الحالة | يعيد الحالة الحالية للأداة. | `eval-observability`, `execution`, `static-analysis` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |

### إدارة مستودعات GitHub (`github`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `github/github-mcp-server (official)`
- **التشغيل:** `docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server`
- **متغيرات البيئة المطلوبة:** `GITHUB_PERSONAL_ACCESS_TOKEN` (بدون قيم في هذا الملف)
- **شرح المدخلات:**
  - `GITHUB_PERSONAL_ACCESS_TOKEN`: رمز وصول شخصي إلى GitHub بصلاحيات القراءة والكتابة المطلوبة.
- **القدرات:** وسوم: action-execution, eval-observability, execution, rag-web-search | المهام: ai-agent-builder, quality-security-audit, automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `get_file_contents` | قراءة محتوى ملف | يقرأ محتوى ملف أو دليل من مستودع. | `execution` | `quality-security-audit`, `automation-ops` | `read-only` | `GITHUB_PERSONAL_ACCESS_TOKEN` | `owner`: المالك؛ `repo`: المستودع؛ `path`: المسار؛ `ref`: الفرع أو الالتزام |
| `list_commits` | قائمة الالتزامات | يعرض التزامات فرع أو مسار. | `execution` | `quality-security-audit`, `automation-ops` | `read-only` | `GITHUB_PERSONAL_ACCESS_TOKEN` | `owner`: المالك؛ `repo`: المستودع؛ `sha`: الفرع أو الالتزام |
| `search_repositories` | البحث في المستودعات | يبحث عن مستودعات GitHub. | `execution`, `rag-web-search` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | `GITHUB_PERSONAL_ACCESS_TOKEN` | `query`: عبارة البحث |
| `search_code` | البحث في الكود | يبحث عن كود داخل المستودعات. | `execution`, `rag-web-search` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | `GITHUB_PERSONAL_ACCESS_TOKEN` | `query`: عبارة البحث |
| `list_issues` | قائمة المشكلات | يعرض مشكلات المستودع. | `execution` | `quality-security-audit`, `automation-ops` | `read-only` | `GITHUB_PERSONAL_ACCESS_TOKEN` | `owner`: المالك؛ `repo`: المستودع؛ `state`: الحالة |
| `create_issue` | إنشاء مشكلة | ينشئ مشكلة جديدة. | `action-execution`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | `GITHUB_PERSONAL_ACCESS_TOKEN` | `owner`: المالك؛ `repo`: المستودع؛ `title`: العنوان |
| `list_pull_requests` | قائمة طلبات السحب | يعرض طلبات السحب. | `execution` | `quality-security-audit`, `automation-ops` | `read-only` | `GITHUB_PERSONAL_ACCESS_TOKEN` | `owner`: المالك؛ `repo`: المستودع؛ `state`: الحالة |
| `create_pull_request` | إنشاء طلب سحب | ينشئ طلب سحب بين فرعين. | `action-execution`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | `GITHUB_PERSONAL_ACCESS_TOKEN` | `owner`: المالك؛ `repo`: المستودع؛ `head`: الفرع المصدر؛ `base`: الفرع الهدف؛ `title`: العنوان |
| `merge_pull_request` | دمج طلب سحب | يدمج طلب سحب مقبول. | `action-execution`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | `GITHUB_PERSONAL_ACCESS_TOKEN` | `owner`: المالك؛ `repo`: المستودع؛ `pullNumber`: رقم الطلب |
| `get_me` | بيانات الحساب | يعيد بيانات المستخدم المصادّق عليه. | `eval-observability`, `execution` | `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | `GITHUB_PERSONAL_ACCESS_TOKEN` | — |

### عمليات Git المحلية (`git`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `modelcontextprotocol/servers (server-git)`
- **التشغيل:** `npx -y @modelcontextprotocol/server-git --repository /absolute/path/to/repo`
- **شرح المدخلات:**
  - `--repository`: المسار الكامل إلى المستودع المحلي.
- **القدرات:** وسوم: action-execution, diff, eval-observability, execution | المهام: code-review, ai-agent-builder, quality-security-audit, automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `git_status` | حالة المستودع | يعيد حالة ملفات المستودع. | `diff`, `eval-observability`, `execution` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `git_add` | إضافة ملفات | يضيف ملفات إلى منطقة التجهيز. | `action-execution`, `diff`, `execution` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | `files`: قائمة المسارات |
| `git_commit` | إنشاء التزام | ينشئ التزاماً بالرسالة المحددة. | `action-execution`, `diff`, `execution` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | `message`: رسالة الالتزام |
| `git_log` | سجل الالتزامات | يعرض سجل الالتزامات. | `diff`, `execution` | `code-review`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `git_diff` | عرض الفروق | يعرض الفروق بين التغييرات. | `diff`, `execution` | `code-review`, `quality-security-audit`, `automation-ops` | `read-only` | — | `target`: الهدف للمقارنة |
| `git_branch` | قائمة الفروع | يعرض الفروع المحلية. | `diff`, `execution` | `code-review`, `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `git_checkout` | التبديل إلى فرع | ينتقل إلى فرع أو التزام. | `action-execution`, `diff`, `execution` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | `ref`: اسم الفرع أو الالتزام |
| `git_show` | عرض التزام | يعرض تفاصيل التزام محدد. | `diff`, `execution` | `code-review`, `quality-security-audit`, `automation-ops` | `read-only` | — | `ref`: معرّف الالتزام |
| `git_init` | تهيئة مستودع | ينشئ مستودع Git جديداً. | `action-execution`, `diff`, `execution` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | `path`: مسار المستودع |
| `git_push` | دفع التغييرات | يدفع الالتزامات إلى المستودع البعيد. | `action-execution`, `diff`, `execution` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | — |
| `git_pull` | سحب التغييرات | يحدّث المستودع من المصدر البعيد. | `action-execution`, `diff`, `execution` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | — |
| `git_clone` | استنساخ مستودع | يستنسخ مستودعاً بعيداً. | `action-execution`, `diff`, `execution` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | `url`: رابط المستودع |

### تحليل الكود بالرموز (`serena`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `oraios/serena (official)`
- **التشغيل:** `uvx --from git+https://github.com/oraios/serena serena start-mcp-server --context ide-assistant`
- **القدرات:** وسوم: action-execution, execution, memory-context, static-analysis, symbol-search | المهام: code-review, ai-agent-builder, quality-security-audit, automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `find_symbol` | البحث عن رمز | يجد رمزاً في المشروع بالاسم أو المسار. | `memory-context`, `static-analysis`, `symbol-search` | `code-review`, `ai-agent-builder`, `quality-security-audit` | `read-only` | — | `name`: اسم الرمز؛ `path`: مسار الملف |
| `find_referencing_symbols` | البحث عن المراجع | يجد كل الرموز التي تشير إلى رمز محدد. | `memory-context`, `static-analysis`, `symbol-search` | `code-review`, `ai-agent-builder`, `quality-security-audit` | `read-only` | — | `name`: اسم الرمز |
| `get_symbols_overview` | نظرة عامة على الرموز | يعرض رموز ملف أو مجلد. | `memory-context`, `static-analysis`, `symbol-search` | `code-review`, `ai-agent-builder`, `quality-security-audit` | `read-only` | — | `path`: المسار |
| `list_memories` | قائمة الملاحظات | يعرض ملاحظات المشروع المحفوظة. | `memory-context`, `static-analysis`, `symbol-search` | `code-review`, `ai-agent-builder`, `quality-security-audit` | `read-only` | — | — |
| `read_memory` | قراءة ملاحظة | يقرأ ملاحظة محفوظة. | `memory-context`, `static-analysis`, `symbol-search` | `code-review`, `ai-agent-builder`, `quality-security-audit` | `read-only` | — | `name`: اسم الملاحظة |
| `write_memory` | كتابة ملاحظة | يحفظ أو يحدّث ملاحظة. | `action-execution`, `execution`, `memory-context`, `static-analysis`, `symbol-search` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | `name`: اسم الملاحظة؛ `content`: المحتوى |
| `activate_project` | تفعيل مشروع | يضبط المشروع الحالي للتحليل. | `memory-context`, `static-analysis`, `symbol-search` | `code-review`, `ai-agent-builder`, `quality-security-audit` | `read-only` | — | `path`: مسار المشروع |
| `execute_shell_command` | تنفيذ أمر | ينفّذ أمراً في بيئة المشروع. | `action-execution`, `execution`, `memory-context`, `static-analysis`, `symbol-search` | `code-review`, `ai-agent-builder`, `quality-security-audit`, `automation-ops` | `read-write` | — | `command`: الأمر |

## البحث والاستكشاف

### وثائق المكتبات (`context7`)

- **الحالة:** added
- **النقل:** streamable-http
- **المصدر:** `upstash/context7 (official)`
- **الرابط:** `https://mcp.context7.com/mcp`
- **متغيرات البيئة المطلوبة:** `CONTEXT7_API_KEY` (بدون قيم في هذا الملف)
- **شرح المدخلات:**
  - `CONTEXT7_API_KEY`: مفتاح API اختياري لرفع حد الطلبات.
- **القدرات:** وسوم: rag-web-search | المهام: ai-agent-builder, docs-localization | الوصول: `read-only`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `resolve-library-id` | تحديد معرّف المكتبة | يحوّل اسم مكتبة إلى معرّف مستقر في الوثائق. | `rag-web-search` | `ai-agent-builder`, `docs-localization` | `read-only` | `CONTEXT7_API_KEY` | `libraryName`: اسم المكتبة |
| `get-library-docs` | جلب وثائق المكتبة | يجلب مقاطع الوثائق الخاصة بموضوع ما. | `rag-web-search` | `ai-agent-builder`, `docs-localization` | `read-only` | `CONTEXT7_API_KEY` | `context7CompatibleLibraryID`: معرّف المكتبة؛ `topic`: الموضوع |

### جلب صفحات الويب (`fetch`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `modelcontextprotocol/servers (server-fetch)`
- **التشغيل:** `npx -y @modelcontextprotocol/server-fetch`
- **القدرات:** وسوم: browser-web, rag-web-search | المهام: ai-agent-builder, docs-localization | الوصول: `read-only`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `fetch` | جلب رابط | يجلب محتوى رابط ويعيده كنص. | `browser-web`, `rag-web-search` | `ai-agent-builder`, `docs-localization` | `read-only` | — | `url`: الرابط |

### بحث Brave (`brave-search`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `modelcontextprotocol/servers (server-brave-search)`
- **التشغيل:** `npx -y @modelcontextprotocol/server-brave-search`
- **متغيرات البيئة المطلوبة:** `BRAVE_API_KEY` (بدون قيم في هذا الملف)
- **شرح المدخلات:**
  - `BRAVE_API_KEY`: مفتاح API الخاص بحساب Brave Search.
- **القدرات:** وسوم: browser-web, rag-web-search | المهام: ai-agent-builder, docs-localization | الوصول: `read-only`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `brave_web_search` | بحث ويب | يبحث في الويب ويعيد العناوين والمقتطفات. | `browser-web`, `rag-web-search` | `ai-agent-builder`, `docs-localization` | `read-only` | `BRAVE_API_KEY` | `query`: عبارة البحث |
| `brave_local_search` | بحث محلي | يبحث في الأعمال والمواقع المحلية. | `browser-web`, `rag-web-search` | `ai-agent-builder`, `docs-localization` | `read-only` | `BRAVE_API_KEY` | `query`: عبارة البحث |

## قواعد البيانات

### إدارة Supabase (`supabase`)

- **الحالة:** added
- **النقل:** streamable-http
- **المصدر:** `supabase/supabase-mcp (official)`
- **الرابط:** `https://mcp.supabase.com/mcp`
- **متغيرات البيئة المطلوبة:** `SUPABASE_ACCESS_TOKEN` (بدون قيم في هذا الملف)
- **شرح المدخلات:**
  - `SUPABASE_ACCESS_TOKEN`: رمز وصول شخصي إلى حساب Supabase.
- **القدرات:** وسوم: action-execution, execution | المهام: automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `list_projects` | قائمة المشاريع | يعرض مشاريع Supabase المتاحة. | `execution` | `automation-ops` | `read-only` | `SUPABASE_ACCESS_TOKEN` | — |
| `get_project` | تفاصيل مشروع | يعيد بيانات مشروع محدد. | `execution` | `automation-ops` | `read-only` | `SUPABASE_ACCESS_TOKEN` | `project_id`: معرّف المشروع |
| `list_tables` | قائمة الجداول | يعرض جداول قاعدة بيانات المشروع. | `execution` | `automation-ops` | `read-only` | `SUPABASE_ACCESS_TOKEN` | `project_id`: معرّف المشروع |
| `execute_sql` | تنفيذ SQL | ينفّذ استعلام SQL للقراءة. | `action-execution`, `execution` | `automation-ops` | `read-write` | `SUPABASE_ACCESS_TOKEN` | `project_id`: معرّف المشروع؛ `query`: الاستعلام |
| `apply_migration` | تطبيق ترحيل | يطبّق ترحيل SQL على المشروع. | `action-execution`, `execution` | `automation-ops` | `read-write` | `SUPABASE_ACCESS_TOKEN` | `project_id`: معرّف المشروع؛ `name`: اسم الترحيل؛ `query`: الاستعلام |
| `list_extensions` | قائمة الإضافات | يعرض إضافات PostgreSQL المفعّلة. | `execution` | `automation-ops` | `read-only` | `SUPABASE_ACCESS_TOKEN` | — |

### استعلامات PostgreSQL (`postgres`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `modelcontextprotocol/servers (server-postgres)`
- **التشغيل:** `npx -y @modelcontextprotocol/server-postgres postgresql://user:password@localhost:5432/dbname`
- **متغيرات البيئة المطلوبة:** `POSTGRES_CONNECTION_STRING` (بدون قيم في هذا الملف)
- **شرح المدخلات:**
  - `POSTGRES_CONNECTION_STRING`: سلسلة الاتصال بقاعدة البيانات إن كانت مطلوبة من المضيف.
- **القدرات:** وسوم: execution | المهام: automation-ops | الوصول: `read-only`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `query` | تنفيذ استعلام | ينفّذ استعلام SQL ويعيد الصفوف. | `execution` | `automation-ops` | `read-only` | `POSTGRES_CONNECTION_STRING` | `sql`: استعلام SQL |
| `list_tables` | قائمة الجداول | يعرض جداول المخطط العام. | `execution` | `automation-ops` | `read-only` | `POSTGRES_CONNECTION_STRING` | — |
| `describe_table` | وصف جدول | يعرض أعمدة جدول وقيوده. | `execution` | `automation-ops` | `read-only` | `POSTGRES_CONNECTION_STRING` | `table`: اسم الجدول |

### قاعدة بيانات SQLite (`sqlite`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `modelcontextprotocol/servers (server-sqlite)`
- **التشغيل:** `npx -y @modelcontextprotocol/server-sqlite --db-path /absolute/path/to/db.sqlite3`
- **شرح المدخلات:**
  - `--db-path`: المسار الكامل لملف SQLite.
- **القدرات:** وسوم: action-execution, execution | المهام: automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `read_query` | استعلام قراءة | ينفّذ استعلام SELECT. | `execution` | `automation-ops` | `read-only` | — | `sql`: استعلام SQL |
| `write_query` | استعلام كتابة | ينفّذ INSERT أو UPDATE أو DELETE. | `action-execution`, `execution` | `automation-ops` | `read-write` | — | `sql`: استعلام SQL |
| `create_table` | إنشاء جدول | ينشئ جدولاً جديداً. | `action-execution`, `execution` | `automation-ops` | `read-write` | — | `sql`: تعريف الجدول |
| `list_tables` | قائمة الجداول | يعرض أسماء الجداول. | `execution` | `automation-ops` | `read-only` | — | — |
| `describe_table` | وصف جدول | يعرض بنية جدول. | `execution` | `automation-ops` | `read-only` | — | `table`: اسم الجدول |
| `append_insight` | إضافة ملاحظة | يضيف ملاحظة إلى سجل الرؤى. | `action-execution`, `execution` | `automation-ops` | `read-write` | — | `insight`: الملاحظة |

### اتصال قواعد البيانات المتعدد (`dbhub`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `bytebase/dbhub (official)`
- **التشغيل:** `npx -y @bytebase/dbhub --dsn postgresql://user:password@localhost:5432/dbname`
- **متغيرات البيئة المطلوبة:** `DBHUB_DSN` (بدون قيم في هذا الملف)
- **شرح المدخلات:**
  - `DBHUB_DSN`: سلسلة الاتصال بمحرك قاعدة البيانات.
- **القدرات:** وسوم: action-execution, execution, rag-web-search | المهام: automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `execute_sql` | تنفيذ SQL | ينفّذ استعلام SQL على الاتصال المحدد. | `action-execution`, `execution` | `automation-ops` | `read-write` | `DBHUB_DSN` | `sql`: الاستعلام |
| `list_tables` | قائمة الجداول | يعرض جداول قاعدة البيانات. | `execution` | `automation-ops` | `read-only` | `DBHUB_DSN` | — |
| `describe_table` | وصف جدول | يعرض بنية جدول. | `execution` | `automation-ops` | `read-only` | `DBHUB_DSN` | `table`: اسم الجدول |
| `search_objects` | البحث في الكائنات | يبحث عن جداول أو كائنات بالاسم. | `execution`, `rag-web-search` | `automation-ops` | `read-only` | `DBHUB_DSN` | `query`: عبارة البحث |
| `list_connections` | قائمة الاتصالات | يعرض اتصالات قواعد البيانات المضبوطة. | `execution` | `automation-ops` | `read-only` | `DBHUB_DSN` | — |

## أتمتة المتصفح

### أتمتة المتصفح بـ Playwright (`playwright`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `microsoft/playwright-mcp (official)`
- **التشغيل:** `npx -y @playwright/mcp@latest`
- **متغيرات البيئة المطلوبة:** `PLAYWRIGHT_BROWSERS_PATH` (بدون قيم في هذا الملف)
- **شرح المدخلات:**
  - `PLAYWRIGHT_BROWSERS_PATH`: مسار تثبيت متصفحات Playwright إن لزم.
- **القدرات:** وسوم: action-execution, browser-web, eval-observability, execution | المهام: quality-security-audit, automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `browser_navigate` | التنقل إلى صفحة | يفتح عنوان URL في المتصفح. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `url`: الرابط |
| `browser_click` | نقر | ينقر على عنصر في الصفحة. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `element`: وصف العنصر |
| `browser_type` | كتابة نص | يكتب نصاً في حقل إدخال. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `text`: النص |
| `browser_snapshot` | لقطة بنية الصفحة | يعيد تمثيلاً نصياً لبنية الصفحة. | `action-execution`, `browser-web` | `automation-ops` | `read-only` | — | — |
| `browser_take_screenshot` | لقطة شاشة | يحفظ لقطة شاشة للصفحة. | `action-execution`, `browser-web` | `automation-ops` | `read-only` | — | — |
| `browser_tabs` | إدارة التبويبات | يسرد التبويبات أو يبدّل بينها. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | — |
| `browser_evaluate` | تنفيذ JavaScript | ينفّذ تعبير JavaScript في الصفحة. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `function`: الدالة |
| `browser_fill_form` | تعبئة نموذج | يملأ عدة حقول في نموذج. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | — |
| `browser_press_key` | ضغط مفتاح | يضغط مفتاح لوحة المفاتيح. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `key`: المفتاح |
| `browser_wait_for` | انتظار | ينتظر ظهور عنصر أو انقضاء زمن. | `action-execution`, `browser-web` | `automation-ops` | `read-only` | — | `time`: الزمن بالثواني |
| `browser_network_requests` | طلبات الشبكة | يعيد طلبات الشبكة المسجلة. | `action-execution`, `browser-web`, `eval-observability` | `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `browser_console_messages` | رسائل الوحدة | يعيد رسائل وحدة تحكم المتصفح. | `action-execution`, `browser-web`, `eval-observability` | `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `browser_close` | إغلاق المتصفح | يغلق جلسة المتصفح. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | — |

### أدوات مطوري Chrome (`chrome-devtools`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `ChromeDevTools/chrome-devtools-mcp (official)`
- **التشغيل:** `npx -y chrome-devtools-mcp@latest`
- **متغيرات البيئة المطلوبة:** `CHROME_PATH` (بدون قيم في هذا الملف)
- **شرح المدخلات:**
  - `CHROME_PATH`: المسار إلى ملف تنفيذي مخصص لـ Chrome.
- **القدرات:** وسوم: action-execution, browser-web, eval-observability, execution | المهام: quality-security-audit, automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `list_pages` | قائمة الصفحات | يعرض الصفحات المفتوحة. | `action-execution`, `browser-web` | `automation-ops` | `read-only` | — | — |
| `new_page` | صفحة جديدة | يفتح صفحة جديدة. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `url`: الرابط |
| `select_page` | اختيار صفحة | يحدد الصفحة النشطة. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `pageIdx`: رقم الصفحة |
| `close_page` | إغلاق صفحة | يغلق صفحة محددة. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `pageIdx`: رقم الصفحة |
| `navigate_page` | التنقل | ينقل الصفحة إلى عنوان جديد. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `url`: الرابط |
| `take_snapshot` | لقطة بنية الصفحة | يعيد تمثيلاً نصياً للصفحة. | `action-execution`, `browser-web` | `automation-ops` | `read-only` | — | — |
| `take_screenshot` | لقطة شاشة | يحفظ لقطة شاشة للصفحة. | `action-execution`, `browser-web` | `automation-ops` | `read-only` | — | — |
| `click` | نقر | ينقر على عنصر. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `uid`: معرّف العنصر |
| `fill` | تعبئة حقل | يملأ حقلاً بالنص. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `uid`: معرّف العنصر؛ `value`: القيمة |
| `hover` | تمرير المؤشر | يمرر المؤشر فوق عنصر. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `uid`: معرّف العنصر |
| `press_key` | ضغط مفتاح | يضغط مفتاحاً. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `key`: المفتاح |
| `evaluate_script` | تنفيذ سكربت | ينفّذ JavaScript في الصفحة. | `action-execution`, `browser-web`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | `function`: الدالة |
| `list_network_requests` | طلبات الشبكة | يعيد طلبات الشبكة. | `action-execution`, `browser-web`, `eval-observability` | `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `list_console_messages` | رسائل الوحدة | يعيد رسائل وحدة التحكم. | `action-execution`, `browser-web`, `eval-observability` | `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `performance_start_trace` | بدء قياس الأداء | يبدأ تسجيل أثر الأداء. | `action-execution`, `browser-web`, `eval-observability`, `execution` | `quality-security-audit`, `automation-ops` | `read-write` | — | — |

### كشط وتصفح الويب (`firecrawl`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `firecrawl/firecrawl-mcp-server (official)`
- **التشغيل:** `npx -y firecrawl-mcp`
- **متغيرات البيئة المطلوبة:** `FIRECRAWL_API_KEY` (بدون قيم في هذا الملف)
- **شرح المدخلات:**
  - `FIRECRAWL_API_KEY`: مفتاح API الخاص بحساب Firecrawl.
- **القدرات:** وسوم: browser-web, eval-observability, rag-web-search | المهام: quality-security-audit, automation-ops | الوصول: `read-only`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `firecrawl_scrape` | كشط صفحة | يحول صفحة واحدة إلى markdown أو بيانات منظمة. | `browser-web`, `rag-web-search` | `automation-ops` | `read-only` | `FIRECRAWL_API_KEY` | `url`: الرابط |
| `firecrawl_crawl` | زحف موقع | يزحف إلى صفحات موقع ضمن حدود محددة. | `browser-web`, `rag-web-search` | `automation-ops` | `read-only` | `FIRECRAWL_API_KEY` | `url`: الرابط؛ `limit`: أقصى عدد صفحات |
| `firecrawl_search` | بحث ويب | يبحث في الويب ويعيد نتائج غنية. | `browser-web`, `rag-web-search` | `automation-ops` | `read-only` | `FIRECRAWL_API_KEY` | `query`: عبارة البحث |
| `firecrawl_extract` | استخراج منظم | يستخرج حقولاً منظمة من صفحات. | `browser-web`, `rag-web-search` | `automation-ops` | `read-only` | `FIRECRAWL_API_KEY` | `urls`: قائمة الروابط |
| `firecrawl_map` | خريطة الموقع | يعيد روابط موقع. | `browser-web`, `rag-web-search` | `automation-ops` | `read-only` | `FIRECRAWL_API_KEY` | `url`: الرابط |
| `firecrawl_check_crawl_status` | حالة الزحف | يتحقق من حالة مهمة زحف. | `browser-web`, `eval-observability`, `rag-web-search` | `quality-security-audit` | `read-only` | `FIRECRAWL_API_KEY` | `id`: معرّف المهمة |

## الملفات والنظام

### قراءة وكتابة الملفات (`filesystem`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `modelcontextprotocol/servers (server-filesystem)`
- **التشغيل:** `npx -y @modelcontextprotocol/server-filesystem /absolute/allowed/path`
- **شرح المدخلات:**
  - `[path]`: مسار مطلق واحد أو أكثر يُسمح للخادم بالوصول إليها.
- **القدرات:** وسوم: action-execution, execution, files-system, rag-web-search | المهام: quality-security-audit, automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `read_file` | قراءة ملف | يقرأ محتوى ملف نصي. | `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-only` | — | `path`: مسار الملف |
| `read_multiple_files` | قراءة عدة ملفات | يقرأ عدة ملفات في طلب واحد. | `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-only` | — | `paths`: قائمة المسارات |
| `write_file` | كتابة ملف | ينشئ ملفاً أو يستبدل محتواه. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `path`: مسار الملف؛ `content`: المحتوى |
| `edit_file` | تعديل ملف | يعدّل جزءاً من ملف. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `path`: مسار الملف |
| `list_directory` | سرد مجلد | يعيد محتويات مجلد. | `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-only` | — | `path`: مسار المجلد |
| `create_directory` | إنشاء مجلد | ينشئ مجلداً جديداً. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `path`: مسار المجلد |
| `move_file` | نقل ملف | ينقل أو يعيد تسمية ملف. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `source`: المصدر؛ `destination`: الهدف |
| `search_files` | البحث عن ملفات | يبحث عن ملفات بمطابقة اسم. | `execution`, `files-system`, `rag-web-search` | `quality-security-audit`, `automation-ops` | `read-only` | — | `path`: مسار البحث؛ `pattern`: نمط الاسم |
| `get_file_info` | بيانات ملف | يعيد بيانات ملف أو مجلد. | `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-only` | — | `path`: المسار |
| `list_allowed_directories` | المجلدات المسموحة | يعرض المسارات المسموح بالوصول إليها. | `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-only` | — | — |

### إدارة النظام والعمليات (`desktop-commander`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `wonderwhy-er/DesktopCommanderMCP (official)`
- **التشغيل:** `npx -y @wonderwhy-er/desktop-commander`
- **القدرات:** وسوم: action-execution, execution, files-system, rag-web-search | المهام: quality-security-audit, automation-ops | الوصول: `read-write`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `read_file` | قراءة ملف | يقرأ ملفاً نصياً. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-only` | — | `path`: مسار الملف |
| `write_file` | كتابة ملف | يكتب محتوى ملف. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `path`: مسار الملف؛ `content`: المحتوى |
| `edit_block` | تعديل مقطع | يستبدل مقطعاً محدداً داخل ملف. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `path`: مسار الملف |
| `create_directory` | إنشاء مجلد | ينشئ مجلداً. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `path`: مسار المجلد |
| `move_file` | نقل ملف | ينقل ملفاً أو مجلداً. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `source`: المصدر؛ `destination`: الهدف |
| `list_directory` | سرد مجلد | يعيد محتويات مجلد. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-only` | — | `path`: مسار المجلد |
| `search_files` | البحث عن ملفات | يبحث داخل الملفات. | `action-execution`, `execution`, `files-system`, `rag-web-search` | `quality-security-audit`, `automation-ops` | `read-only` | — | `path`: المسار؛ `pattern`: النمط |
| `start_process` | تشغيل عملية | يشغّل أمراً في الخلفية. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `command`: الأمر |
| `interact_with_process` | التفاعل مع عملية | يرسل مدخلاً إلى عملية شغّالة. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `pid`: معرّف العملية |
| `read_process_output` | قراءة مخرجات عملية | يقرأ مخرجات عملية. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-only` | — | `pid`: معرّف العملية |
| `list_processes` | قائمة العمليات | يعرض العمليات الشغّالة. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-only` | — | — |
| `kill_process` | إيقاف عملية | يوقف عملية محددة. | `action-execution`, `execution`, `files-system` | `quality-security-audit`, `automation-ops` | `read-write` | — | `pid`: معرّف العملية |

## تحويل الملفات

### تحويل الملفات إلى Markdown (`markitdown`)

- **الحالة:** added
- **النقل:** stdio
- **المصدر:** `microsoft/markitdown (official)`
- **التشغيل:** `uvx markitdown-mcp`
- **القدرات:** وسوم: execution, localization | المهام: docs-localization | الوصول: `read-only`

| الأداة | الاسم العربي | الوظيفة | الوسوم | المهام | الوصول | البيئة | المدخلات |
|---|---|---|---|---|---|---|---|
| `convert_to_markdown` | التحويل إلى Markdown | يحوّل ملفاً إلى Markdown. | `execution`, `localization` | `docs-localization` | `read-only` | — | `path`: مسار الملف أو الرابط |
