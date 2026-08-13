// Lightweight, dependency-free i18n for the Neotec Insight UI.
//
// Design goals (per product owner):
//  • Bilingual EN/AR, switchable at runtime with a header toggle.
//  • Layout stays Left-To-Right in both languages (text is translated, the
//    grid/tables are never mirrored) — so report rendering is unaffected.
//  • Zero impact on data/logic: t() only maps display strings; anything not in
//    the dictionary falls through to the original English text unchanged.
//
// Mechanism: a module-level `currentLang` read by t(). A React context at the
// app root holds the language in state; toggling updates state (re-rendering
// the tree) AND the module var, so every t() call re-evaluates. No DOM
// MutationObserver is used, so it cannot fight React or freeze the page.

import { createContext, useContext } from 'react';

export type Lang = 'en' | 'ar';
const KEY = 'nai_lang';

function read(): Lang {
  try { return (localStorage.getItem(KEY) as Lang) || 'en'; } catch { return 'en'; }
}
let currentLang: Lang = read();

export function getLang(): Lang { return currentLang; }
export function setLangGlobal(l: Lang): void {
  currentLang = l === 'ar' ? 'ar' : 'en';
  try { localStorage.setItem(KEY, currentLang); } catch { /* ignore */ }
  // Mark the language for font selection, but FORCE the layout to stay
  // Left-To-Right in both languages — the report grid is never mirrored.
  try {
    const html = document.documentElement;
    html.setAttribute('lang', currentLang);
    html.setAttribute('dir', 'ltr');
    if (document.body) document.body.setAttribute('dir', 'ltr');
  } catch { /* ignore */ }
}

// English → Arabic. Keys are the exact English shown in the UI.
const DICT: Record<string, string> = {
  // v2.0.0 — GL tree picker, P&L drill, translation editor
  'Browse tree': 'استعراض الشجرة', 'Hide tree': 'إخفاء الشجرة',
    'selected': 'محدد',
  'Tick a group to select all accounts under it': 'حدّد مجموعة لاختيار كل الحسابات ضمنها',
  'Loading chart of accounts…': 'جارٍ تحميل دليل الحسابات…',
  'No accounts found for this company.': 'لا توجد حسابات لهذه الشركة.',
   'Layout': 'التخطيط', 'Cross-tab': 'جدول متقاطع',
  'P&L Drill': 'تفصيل الأرباح والخسائر', 'P&L Drill (with subtotals)': 'تفصيل الأرباح والخسائر (بالمجاميع الفرعية)',
  'Expand all': 'توسيع الكل', 'Collapse all': 'طي الكل',
   'Cost of Sales': 'تكلفة المبيعات', 
  'Operating Expenses': 'المصاريف التشغيلية',  'Net %': 'النسبة الصافية',
    'Grand Total': 'الإجمالي العام',
  'Edit Arabic translation': 'تعديل الترجمة العربية', 
  'No Arabic translation yet — click ✎ to add one': 'لا توجد ترجمة عربية بعد — اضغط ✎ لإضافتها',
  'EBITDA add-backs': 'إضافات الأرباح قبل الفوائد والضرائب والإهلاك',

  // nav
  'Reports': 'التقارير', 'CFO Briefing': 'إحاطة المدير المالي', 'Dashboard': 'لوحة المعلومات',
  'Group': 'المجموعة', 'Visuals': 'الرسوم',
  // report-editor tabs
  'Run': 'تشغيل', 'Equity': 'حقوق الملكية', 'Rows': 'الصفوف', 'Account map': 'ربط الحسابات',
  'Budget': 'الموازنة',
  // view-by
  'Period': 'الفترة', 'Dimension': 'البُعد', 'Years': 'السنوات', 'Combo': 'مركّب',
  // control labels (as displayed, incl. upper-case variants)
  'REPORT': 'التقرير', 'PERIOD MODE': 'نمط الفترة', 'FISCAL YEAR': 'السنة المالية',
  'FISCAL YEAR (PRESET)': 'السنة المالية (إعداد)', 'CALENDAR': 'التقويم', 'FROM DATE': 'من تاريخ',
  'TO DATE': 'إلى تاريخ', 'GRANULARITY': 'التفصيل', 'COMPANY': 'الشركة', 'COST CENTER': 'مركز التكلفة',
  'PROJECT': 'المشروع', 'DEPARTMENT': 'القسم', 'BRANCH': 'الفرع', 'EMPLOYEE': 'الموظف',
  'COMPARISON': 'المقارنة', 'COMPARE TO (BUDGET BOOK)': 'المقارنة مع (دفتر الموازنة)',
  'PRIOR YEARS': 'السنوات السابقة', 'OUTER DIM': 'البُعد الخارجي', 'INNER DIM': 'البُعد الداخلي',
  'VIEW BY': 'العرض حسب',
  // toolbar
  'Excel': 'Excel', 'PDF': 'PDF', 'CSV': 'CSV', 'Print': 'طباعة', 'Visualize': 'تصوّر',
  'Report dimensions': 'أبعاد التقرير', 'Save changes': 'حفظ التغييرات',
  // run options / toggles
  '% Growth': '٪ النمو', '% of Revenue': '٪ من الإيراد', '% Achieved': '٪ المُنجز',
  'Variance': 'الانحراف', 'Decimals': 'المنازل العشرية', 'Collapse empties': 'طيّ الفارغة',
  'Comparative years': 'سنوات المقارنة', 'Actual vs Budget': 'الفعلي مقابل الموازنة',
  'Actuals only': 'الفعلي فقط',
  // rows editor
  'Section': 'قسم', 'Source': 'مصدر', 'Formula': 'صيغة', 'Presentation format': 'صيغة العرض',
  'Vertical': 'عمودي', 'T-Account': 'حساب T', 'Select a row to edit.': 'اختر صفًّا لتحريره.',
  // dropdown "all" options
  '— All cost centers —': '— كل مراكز التكلفة —', '— All projects —': '— كل المشاريع —',
  '— All departments —': '— كل الأقسام —', '— All branches —': '— كل الفروع —',
  // KPI tiles
  'REVENUE': 'الإيراد', 'GROSS PROFIT': 'مجمل الربح', 'EBITDA': 'الأرباح قبل الفوائد والضرائب والإهلاك',
  'NET INCOME': 'صافي الدخل',  
  // column headers
  'ACTUAL': 'الفعلي', 'BUDGET': 'الموازنة', 'Row': 'البند', 'Total': 'الإجمالي',
  'Debit': 'مدين', 'Credit': 'دائن', 'Balance': 'الرصيد', 'Net': 'الصافي', 'Accounts': 'الحسابات',
  // misc
  'Loading…': 'جارٍ التحميل…', 'Loading...': 'جارٍ التحميل...', 'No data': 'لا توجد بيانات',
  // exact RunTab source strings (mixed case as authored)
  'Report': 'التقرير', 'Period mode': 'نمط الفترة', 'Fiscal year': 'السنة المالية',
  'Reporting calendar': 'تقويم التقارير', 'From': 'من', 'To': 'إلى', 'Granularity': 'التفصيل',
  'Date range': 'نطاق تاريخ', 'Company': 'الشركة', 'Comparison': 'المقارنة',
  'Compare to (budget book)': 'المقارنة مع (دفتر الموازنة)', 'Prior years': 'السنوات السابقة',
  'Pivot by': 'تجميع حسب', 'Outer dim': 'البُعد الخارجي', 'Inner dim': 'البُعد الداخلي',
  'Cost center': 'مركز التكلفة', 'Project': 'المشروع', 'Department': 'القسم', 'Branch': 'الفرع',
  'From date': 'من تاريخ', 'To date': 'إلى تاريخ', 'Running…': 'جارٍ التشغيل…', 'Copy': 'نسخ',
  // month abbreviations (kept short to preserve column widths)
  'Jan': 'ينا', 'Feb': 'فبر', 'Mar': 'مار', 'Apr': 'أبر', 'May': 'ماي', 'Jun': 'يون',
  'Jul': 'يول', 'Aug': 'أغس', 'Sep': 'سبت', 'Oct': 'أكت', 'Nov': 'نوف', 'Dec': 'ديس',
  // period-column sub-headers (values)
  'Actual': 'الفعلي', 'Var': 'انحراف', '% Grw': '٪ نمو', '% Ach': '٪ إنجاز', '% Rev': '٪ إيراد',
  'FY': 'سنة مالية', 'of budget': 'من الموازنة', 'of revenue': 'من الإيراد', 'vs': 'مقابل',
  // statement headers (values)
  'Account': 'الحساب', 'Opening': 'افتتاحي', 'Closing': 'ختامي', 'Movement': 'الحركة',
  'Beginning': 'بداية', 'Ending': 'نهاية', 'Amount': 'المبلغ', 'Description': 'الوصف',
  // granularity option labels (values)
  'Monthly only': 'شهري فقط', 'Quarterly only': 'ربع سنوي فقط', 'Half-yearly only': 'نصف سنوي فقط',
  'YTD only': 'منذ بداية السنة فقط', 'Monthly + Quarterly': 'شهري + ربع سنوي',
  'Monthly + Half-yearly': 'شهري + نصف سنوي', 'Quarterly + Half-yearly': 'ربع سنوي + نصف سنوي',
  'Monthly + Quarterly + Half-yearly': 'شهري + ربع سنوي + نصف سنوي',
  'Quarterly + Quarter-YTD (interleaved)': 'ربع سنوي + ربع سنوي تراكمي (متداخل)',
  // calendar values
  'Local': 'محلي',
  // date-range quick presets
  'Quick range': 'نطاق سريع', 'Custom': 'مخصّص', 'Year to date': 'منذ بداية السنة',
  'Current year': 'السنة الحالية', 'Prior year': 'السنة السابقة', 'This quarter': 'الربع الحالي',
  'Last quarter': 'الربع السابق', 'This month': 'الشهر الحالي', 'Last month': 'الشهر السابق',
  'Trailing 12 months': 'آخر 12 شهرًا',
  // GL drill-through
  'GL entries': 'قيود دفتر الأستاذ', 'Verify': 'تحقّق', 'Scope': 'النطاق', 'Entries': 'القيود',
  'showing': 'عرض', 'Net total (credit − debit)': 'الصافي (دائن − مدين)',
  'Matches the report value': 'مطابق لقيمة التقرير', 'Differs from report value': 'مختلف عن قيمة التقرير',
  'Open in ERP GL': 'فتح في دفتر أستاذ ERP', 'Date': 'التاريخ', 'Voucher': 'السند',
  'Against': 'مقابل', 'No entries': 'لا توجد قيود',
  // General Ledger
  'Supplier': 'المورّد',
  'Customer': 'العميل',
  'Hide from display (still counts in totals)': 'إخفاء من العرض (يظل محتسبًا في الإجماليات)',
  '(No value)': '(بدون قيمة)',
  'Backup': 'نسخة احتياطية',
  'Configuration backup': 'النسخ الاحتياطي للإعدادات',
  'Move your report setup between sites (same company)': 'نقل إعداد التقارير بين المواقع (نفس الشركة)',
  'Export': 'تصدير',
  'Import / Restore': 'استيراد / استعادة',
  'Export configuration': 'تصدير الإعدادات',
  'Download all Insight configuration (report rows, account mappings, budgets, equity, dashboards, AI settings) as one JSON file.': 'تنزيل كل إعدادات Insight (صفوف التقارير، ربط الحسابات، الموازنات، حقوق الملكية، اللوحات، إعدادات الذكاء الاصطناعي) كملف JSON واحد.',
  'Restore REPLACES all existing Insight configuration on this site. Use it on a fresh install or to overwrite with your tested setup.': 'الاستعادة تستبدل كل إعدادات Insight الحالية على هذا الموقع. استخدمها على تثبيت جديد أو للكتابة فوق الإعداد المُختبَر.',
  'Ready to restore': 'جاهز للاستعادة',
  'from': 'من',
  'Confirm restore (replace all)': 'تأكيد الاستعادة (استبدال الكل)',
  'Restoring…': 'جارٍ الاستعادة…',
  'Working…': 'جارٍ العمل…',
  'Exported': 'تم التصدير',
  'records': 'سجل',
  'Restore complete': 'اكتملت الاستعادة',
  'warnings': 'تحذيرات',
  'Reload the page to see your restored reports.': 'أعد تحميل الصفحة لرؤية التقارير المستعادة.',
  'Not a valid configuration file.': 'ملف إعدادات غير صالح.',
  'Studio': 'الاستوديو',
  'Loading Studio…': 'جارٍ تحميل الاستوديو…',
  'Report Studio': 'استوديو التقارير',
  'Describe the report you want — or build it by hand. No code.': 'صِف التقرير الذي تريده — أو ابنِه يدويًا. بدون برمجة.',
  'Pick a document…': 'اختر مستندًا…',
  'Build with AI': 'بناء بالذكاء الاصطناعي',
  'Thinking…': 'يفكّر…',
  'e.g. this year’s totals by customer, only paid invoices': 'مثال: إجماليات هذا العام حسب العميل، الفواتير المدفوعة فقط',
  'Document': 'المستند',
  'Search documents…': 'بحث المستندات…',
  'Group by': 'تجميع حسب',
  'Row limit': 'حد الصفوف',
  'Add filter': 'إضافة عامل تصفية',
  'No filters — showing everything.': 'لا عوامل تصفية — عرض كل شيء.',
  'value': 'قيمة',
  'Report title to save…': 'عنوان التقرير للحفظ…',
  'Open saved…': 'فتح المحفوظة…',
  'Pick a document first.': 'اختر مستندًا أولًا.',
  'Give the report a title.': 'أعطِ التقرير عنوانًا.',
  'Pick a document to begin': 'اختر مستندًا للبدء',
  'Choose any document on the left, then describe your report to the AI or pick fields yourself.': 'اختر أي مستند من اليسار، ثم صِف تقريرك للذكاء الاصطناعي أو اختر الحقول بنفسك.',
  'rows': 'صفوف',
  'grouped by': 'مُجمّع حسب',
  'Subtotal': 'مجموع فرعي',
  'TOTAL': 'الإجمالي',
  'Linked documents': 'المستندات المرتبطة',
  'Calculated columns': 'أعمدة محسوبة',
  'Column name (e.g. Net)': 'اسم العمود (مثل: الصافي)',
  'Formula e.g. qty * rate': 'صيغة مثل: الكمية * السعر',
  'Use field names; operators + - * / ; functions abs, round, min, max.': 'استخدم أسماء الحقول؛ العمليات + - * / ؛ الدوال abs، round، min، max.',
  'click a column header to style it': 'انقر على رأس العمود لتنسيقه',
  'Style / show-as': 'تنسيق / طريقة العرض',
  'Column': 'العمود',
  'Label': 'التسمية',
  'Align': 'المحاذاة',
  'Width (px)': 'العرض (بكسل)',
  'Color': 'اللون',
  'Show as': 'العرض كـ',
  'Value': 'القيمة',
  'Running total': 'إجمالي تراكمي',
  '% of total': '٪ من الإجمالي',
  'Apply & run': 'تطبيق وتشغيل',
  'Search fields…': 'بحث الحقول…',
  'Selected only': 'المحددة فقط',
  'Columns (drag to reorder)': 'الأعمدة (اسحب لإعادة الترتيب)',
  'to': 'إلى',
  'a,b,c': 'أ،ب،ج',
  'Separate Sales & Returns': 'فصل المبيعات والمرتجعات',
  'Field that marks a return…': 'الحقل الذي يحدد المرتجع…',
  '= value (e.g. 1 or Return)': '= القيمة (مثل 1 أو Return)',
  'Returns are listed and subtotalled separately; group subtotal = Sales − Returns.': 'تُدرج المرتجعات وتُجمّع بشكل منفصل؛ المجموع الفرعي للمجموعة = المبيعات − المرتجعات.',
  'Sales': 'المبيعات',
  'Returns': 'المرتجعات',
  'Sales Subtotal': 'مجموع المبيعات الفرعي',
  'Returns Subtotal': 'مجموع المرتجعات الفرعي',
  'Net Subtotal': 'الصافي الفرعي',
  'Cost Center': 'مركز التكلفة',
  'Advanced pivot (cross-tab)': 'جدول محوري متقدم',
  'Rows…': 'الصفوف…',
  'Columns…': 'الأعمدة…',
  'Value…': 'القيمة…',
  'Pivot turns Rows × Columns into a matrix of the aggregated Value.': 'يحوّل الجدول المحوري الصفوف × الأعمدة إلى مصفوفة للقيمة المجمّعة.',
  'Pivot': 'محوري',
  'of': 'لـ',
  'sum': 'مجموع',
  'count': 'عدد',
  'avg': 'متوسط',
  'min': 'أدنى',
  'max': 'أقصى',
  'Value (optional)…': 'القيمة (اختياري)…',
  'Count': 'العدد',
  'For the pivot, choose Rows, Columns and a Value (Value is optional only for Count).': 'للجدول المحوري، اختر الصفوف والأعمدة والقيمة (القيمة اختيارية فقط مع العدّ).',
  'Table': 'جدول',
  'Chart': 'رسم بياني',
  'Category': 'الفئة',
  'auto': 'تلقائي',
  'Measures': 'القيم',
  'bar': 'أعمدة',
  'hbar': 'أعمدة أفقية',
  'stacked': 'مكدّس',
  'line': 'خطي',
  'area': 'مساحي',
  'pie': 'دائري',
  'donut': 'حلقي',
  'Builder': 'المُنشئ',
  'Building your dashboard…': 'جارٍ بناء لوحة المعلومات…',
  'No saved reports yet': 'لا توجد تقارير محفوظة بعد',
  'Build a report, choose a chart, give it a title and Save — it will appear here as a dashboard tile.': 'أنشئ تقريرًا، اختر رسمًا بيانيًا، أعطه عنوانًا واحفظه — سيظهر هنا كبطاقة في اللوحة.',
  'Drag tiles to arrange. Each tile is a saved report.': 'اسحب البطاقات لترتيبها. كل بطاقة هي تقرير محفوظ.',
  'Refresh': 'تحديث',
  'Open in builder': 'فتح في المُنشئ',
  'Columns (optional)…': 'الأعمدة (اختياري)…',
  'For the pivot, choose Rows and a Value (Value is optional for Count). Columns are optional.': 'للجدول المحوري، اختر الصفوف وقيمة (القيمة اختيارية مع العدّ). الأعمدة اختيارية.',
  'Your settings changed — click Run to refresh the result.': 'تغيّرت إعداداتك — انقر تشغيل لتحديث النتيجة.',
  'Financial Health': 'الصحة المالية',
  'Financial Health of the Firm': 'الصحة المالية للمنشأة',
  'From accounting data to a CEO-level verdict on the business.': 'من بيانات المحاسبة إلى حكم تنفيذي على أداء المنشأة.',
  'Calculating financial health…': 'جارٍ حساب الصحة المالية…',
  'Trend': 'الاتجاه',
  'direction matters more than the number': 'الاتجاه أهم من الرقم',
  'Ratio': 'النسبة',
  'Liquidity Health': 'صحة السيولة',
  'Profitability Health': 'صحة الربحية',
  'Operational Efficiency': 'الكفاءة التشغيلية',
  'Financial Stability': 'الاستقرار المالي',
  'Cash Flow Health': 'صحة التدفق النقدي',
  'Can we pay our bills?': 'هل نستطيع سداد التزاماتنا؟',
  'Are we making money?': 'هل نحقق أرباحًا؟',
  'How efficiently do we operate?': 'ما مدى كفاءة تشغيلنا؟',
  'How risky is the business?': 'ما مدى مخاطرة النشاط؟',
  'Are we generating real cash?': 'هل نولّد نقدًا حقيقيًا؟',
  'Current Ratio': 'النسبة الجارية',
  'Quick Ratio': 'النسبة السريعة',
  'Working Capital': 'رأس المال العامل',
  'Gross Profit Margin': 'هامش الربح الإجمالي',
  'Net Profit Margin': 'هامش صافي الربح',
  'Return on Assets (ROA)': 'العائد على الأصول',
  'Return on Equity (ROE)': 'العائد على حقوق الملكية',
  'Days Sales Outstanding (DSO)': 'متوسط فترة التحصيل',
  'Inventory Days': 'أيام المخزون',
  'Payable Days': 'أيام السداد',
  'Cash Conversion Cycle': 'دورة التحويل النقدي',
  'Asset Turnover': 'معدل دوران الأصول',
  'Debt-to-Equity': 'الدين إلى حقوق الملكية',
  'Debt Ratio': 'نسبة الدين',
  'Interest Coverage': 'تغطية الفوائد',
  'Cash & Equivalents': 'النقد وما في حكمه',
  'EBITDA Margin': 'هامش EBITDA',
  'Operating Cash Flow Ratio (approx)': 'نسبة التدفق النقدي التشغيلي (تقريبي)',
  'Excellent': 'ممتاز',
  'Very Good': 'جيد جدًا',
  'Good': 'جيد',
  'Fair': 'مقبول',
  'Needs Attention': 'يحتاج إلى انتباه',
  'Ratios are computed from your General Ledger using ERP account types (root type for Assets/Liabilities/Equity/Income/Expense; account type for Receivable, Payable, Stock, Bank/Cash, Cost of Goods Sold). Current liabilities ≈ total liabilities, and interest/depreciation are detected by account name — verify classification for precise figures.': 'تُحتسب النسب من دفتر الأستاذ باستخدام أنواع حسابات ERP (النوع الجذري للأصول/الالتزامات/حقوق الملكية/الإيرادات/المصروفات؛ ونوع الحساب للذمم المدينة والدائنة والمخزون والبنك/النقد وتكلفة البضاعة المباعة). تُعتبر الالتزامات المتداولة ≈ إجمالي الالتزامات، ويُكتشف الفائدة والإهلاك حسب اسم الحساب — يُرجى التحقق من التصنيف للحصول على أرقام دقيقة.',
  'Cards': 'بطاقات',
  'Graphical': 'رسومي',
  'Click to see how this is calculated': 'انقر لمعرفة طريقة الحساب',
  'accounts': 'الحسابات',
  'No posted accounts in this bucket.': 'لا توجد حسابات مرحّلة في هذه الفئة.',
  'Section score': 'درجة القسم',
  'Health profile': 'ملف الصحة',
  'AI Deep Analysis': 'تحليل معمّق بالذكاء الاصطناعي',
  'Analyzing…': 'جارٍ التحليل…',
  'AI Financial Analysis': 'التحليل المالي بالذكاء الاصطناعي',
  'The AI is reviewing your ratios…': 'يقوم الذكاء الاصطناعي بمراجعة نسبك…',
  'Key Figures': 'الأرقام الرئيسية',
  'The period’s headline numbers': 'أبرز أرقام الفترة',
  'How to read this': 'كيفية القراءة',
  'Status & healthy targets': 'الحالة والمستهدفات الصحية',
  'Healthy': 'سليم',
  'Watch': 'انتباه',
  'Needs attention': 'يحتاج اهتمامًا',
  'positive & growing': 'موجب ومتزايد',
  'Revenue': 'الإيرادات',
  'Gross Profit': 'الربح الإجمالي',
  'Net Income': 'صافي الدخل',
  'Total Assets': 'إجمالي الأصول',
  'Current Assets': 'الأصول المتداولة',
  'Total Liabilities': 'إجمالي الالتزامات',
  'Cash & Bank': 'النقد والبنك',
  'Inventory': 'المخزون',
  'Receivables': 'الذمم المدينة',
  'Payables': 'الذمم الدائنة',
  'DSO': 'متوسط التحصيل',
  'ROA': 'العائد على الأصول',
  'ROE': 'العائد على حقوق الملكية',
  "No 'Cost of Goods Sold' accounts found — classify your cost-of-sales accounts for this to be meaningful.": "لم يُعثر على حسابات «تكلفة البضاعة المباعة» — صنّف حسابات تكلفة المبيعات ليصبح هذا ذا معنى.",
  "Margin looks high — some cost-of-sales accounts may not be typed 'Cost of Goods Sold'.": "الهامش يبدو مرتفعًا — قد لا تكون بعض حسابات تكلفة المبيعات مصنّفة كـ«تكلفة البضاعة المباعة».",
  "No 'Receivable' accounts found — classify your AR control account.": "لم يُعثر على حسابات «ذمم مدينة» — صنّف حساب مراقبة المدينين.",
  "Unusually high — verify 'Receivable' classification and that the period's revenue is complete.": "مرتفع بشكل غير معتاد — تحقّق من تصنيف «الذمم المدينة» ومن اكتمال إيرادات الفترة.",
  "Inventory is zero — verify 'Stock' account classification.": "المخزون صفر — تحقّق من تصنيف حساب «المخزون».",
  "Negative — the Payable balance is a net debit; verify 'Payable' classification.": "سالب — رصيد الدائنين مدين صافٍ؛ تحقّق من تصنيف «الذمم الدائنة».",
  "No 'Payable' accounts found — classify your AP control account.": "لم يُعثر على حسابات «ذمم دائنة» — صنّف حساب مراقبة الدائنين.",
  "No interest accounts detected (named '…interest…').": "لم تُكتشف حسابات فوائد (مسمّاة «...فائدة...»).",
  'Fix account types': 'تصحيح أنواع الحسابات',
  'Account Setup Advisor': 'مرشد إعداد الحسابات',
  'Scanned your Chart of Accounts and found type corrections that make the ratios accurate.': 'تم فحص شجرة الحسابات وإيجاد تصحيحات للأنواع تجعل النسب دقيقة.',
  'Scanning the account tree…': 'جارٍ فحص شجرة الحسابات…',
  'No account-type issues found.': 'لا توجد مشكلات في أنواع الحسابات.',
  'You can review here, but you need write access to apply changes.': 'يمكنك المراجعة هنا، لكنك تحتاج صلاحية التعديل لتطبيق التغييرات.',
  'Current': 'الحالي',
  'Recommended': 'المقترح',
  'Priority': 'الأولوية',
  'group': 'مجموعة',
  'High': 'عالية',
  'Medium': 'متوسطة',
  'Review': 'مراجعة',
  'Low': 'منخفضة',
  'Apply selected': 'تطبيق المحدد',
  'Applying…': 'جارٍ التطبيق…',
  'Select at least one applicable change.': 'اختر تغييرًا واحدًا قابلًا للتطبيق على الأقل.',
  'Applied {0}, failed {1}.': 'تم تطبيق {0}، وفشل {1}.',
  'Net Profit': 'صافي الربح',
  'Net Loss': 'صافي الخسارة',
  'Showing the top {n} dimension combinations by value.': 'يتم عرض أعلى {n} مجموعات من الأبعاد حسب القيمة.',
  'VAT Return': 'إقرار ضريبة القيمة المضافة',
  'Saudi VAT return (16 boxes) assembled from your invoices and GL for the selected period.': 'إقرار ضريبة القيمة المضافة السعودي (16 خانة) مُجمّع من فواتيرك ودفتر الأستاذ للفترة المحددة.',
  'Generate': 'إنشاء',
  'Calculating…': 'جارٍ الحساب…',
  'Export CSV': 'تصدير CSV',
  'Output VAT (sales)': 'ضريبة المخرجات (المبيعات)',
  'Input VAT (purchases)': 'ضريبة المدخلات (المشتريات)',
  'Net VAT payable': 'صافي الضريبة المستحقة',
  'Net VAT reclaimable': 'صافي الضريبة القابلة للاسترداد',
  'Adjustment': 'التعديل',
  'VAT Amount': 'مبلغ الضريبة',
  'VAT on Sales (Output VAT)': 'ضريبة القيمة المضافة على المبيعات (المخرجات)',
  'VAT on Purchases (Input VAT)': 'ضريبة القيمة المضافة على المشتريات (المدخلات)',
  'Net VAT Due': 'صافي الضريبة المستحقة',
  'Standard rated sales': 'مبيعات بالنسبة الأساسية',
  'Private healthcare / education to citizens': 'الرعاية الصحية/التعليم الخاص للمواطنين',
  'Zero-rated domestic sales': 'مبيعات محلية بنسبة صفر',
  'Exports': 'الصادرات',
  'Exempt sales': 'مبيعات معفاة',
  'Total sales': 'إجمالي المبيعات',
  'Standard rated domestic purchases': 'مشتريات محلية بالنسبة الأساسية',
  'Imports subject to VAT (paid at customs)': 'واردات خاضعة للضريبة (مدفوعة في الجمارك)',
  'Imports subject to VAT (reverse charge)': 'واردات خاضعة للضريبة (التكليف العكسي)',
  'Zero-rated purchases': 'مشتريات بنسبة صفر',
  'Exempt purchases': 'مشتريات معفاة',
  'Total purchases': 'إجمالي المشتريات',
  'Total VAT due for the period': 'إجمالي الضريبة المستحقة عن الفترة',
  'Corrections from previous period': 'تصحيحات من الفترة السابقة',
  'VAT credit carried forward': 'رصيد ضريبي مُرحّل',
  'Net VAT due to ZATCA': 'صافي الضريبة المستحقة للهيئة',
  'VAT accounts used': 'حسابات الضريبة المستخدمة',
  'Output': 'المخرجات',
  'Input': 'المدخلات',
  'Show source invoices': 'عرض الفواتير المصدر',
  'Tax Category': 'فئة الضريبة',
  'VAT': 'الضريبة',
  'Box': 'خانة',
  'No documents in this box for the period.': 'لا توجد مستندات في هذه الخانة للفترة.',
  "This is a preparation aid. Verify every box against your source documents before filing on the ZATCA Fatoora portal. Category splits are inferred from each invoice's Tax Category (or VAT rate); confirm zero-rated, exempt, export and import classifications.": "هذه أداة تحضيرية. تحقّق من كل خانة مقابل مستنداتك قبل التقديم على بوابة فاتورة. تقسيم الفئات مستنتج من فئة الضريبة لكل فاتورة (أو نسبة الضريبة)؛ أكّد تصنيفات نسبة الصفر والإعفاء والتصدير والاستيراد.",
  'General Ledger': 'دفتر الأستاذ العام', 'Voucher Date': 'تاريخ السند', 'Voucher No': 'رقم السند',
  'Voucher Type': 'نوع السند', 'Particular': 'البيان', 'Details': 'التفاصيل', 'Party': 'الطرف',
  'Opening Balance': 'الرصيد الافتتاحي', 'Sub Total': 'المجموع الفرعي', 'REPORT TOTAL': 'إجمالي التقرير',
  'Fields': 'الحقول', 'Pick fields': 'اختيار الحقول', 'Search and add one or more accounts': 'ابحث وأضف حسابًا أو أكثر',
  'Select at least one account.': 'اختر حسابًا واحدًا على الأقل.',
  'Print each account on separate page': 'طباعة كل حساب في صفحة منفصلة',
  'Show accounts without transactions': 'عرض الحسابات بدون حركات',
  'Show accounts with zero closing balance': 'عرض الحسابات ذات الرصيد الختامي صفر',
  'Show accounts having only opening balance': 'عرض الحسابات ذات الرصيد الافتتاحي فقط',
  'Show each other account on its own line': 'عرض كل حساب مقابل في سطر مستقل',
  // rows-editor extras
  'ADD NEW ROW:': 'إضافة صف جديد:', 'Add new row': 'إضافة صف جديد',
  // common actions
  'Save': 'حفظ', 'Cancel': 'إلغاء', 'Add': 'إضافة', 'Delete': 'حذف', 'Edit': 'تعديل',
  'Close': 'إغلاق', 'Apply': 'تطبيق', 'Reset': 'إعادة ضبط', 'Search': 'بحث', 'All': 'الكل', 'None': 'لا شيء',
  // CFO Briefing
  'Account Type': 'نوع الحساب', 'Breakdown': 'التفصيل', 'Briefing unavailable': 'الإحاطة غير متاحة',
  'By Branch': 'حسب الفرع', 'Fiscal Year': 'السنة المالية', 'Gap': 'الفجوة', 'HHI': 'مؤشر التركّز',
  'Metric': 'المؤشر', 'Name': 'الاسم', 'Share': 'الحصة', 'Top 3': 'أعلى 3', 'Total AR': 'إجمالي المدينين',
  'Y/Y change': 'التغير السنوي', 'Year': 'السنة',
  // Dashboard
  'Ageing bucket': 'فئة التقادم', 'Best case': 'أفضل حالة', 'Biggest variances vs budget': 'أكبر الانحرافات مقابل الموازنة',
  'Cash In': 'النقد الداخل', 'Cash Out': 'النقد الخارج', 'Cash on hand': 'النقد المتوفر',
  'Collection %': '٪ التحصيل', 'Collections': 'التحصيلات', 'Committed only': 'الملتزم فقط',
  'Customised': 'مخصّص', 'Expected In': 'المتوقع داخلًا', 'Expected Out': 'المتوقع خارجًا',
  'Expense baseline': 'خط الأساس للمصروفات', 'Financial ratios': 'النسب المالية', 'Horizon': 'الأفق',
  'Line': 'خط', 'Liquidity': 'السيولة', 'Month': 'الشهر', 'Not yet due': 'غير مستحق بعد',
  'CEO': 'الرئيس التنفيذي', 'CFO': 'المدير المالي',
  // Group
  'Clear': 'مسح', 'Currency': 'العملة', 'Group view': 'عرض المجموعة', 'Group view unavailable': 'عرض المجموعة غير متاح',
  'KPI': 'مؤشر أداء', 'Presentation Currency': 'عملة العرض', 'Select all': 'تحديد الكل',
  // Visuals
  'Actual only': 'الفعلي فقط', 'Area': 'مساحي', 'Bar': 'أعمدة', 'Chart type': 'نوع الرسم',
  'Configure new tile': 'إعداد بطاقة جديدة', 'Cool': 'بارد', 'Dashboard name': 'اسم اللوحة',
  'Donut': 'حلقي', 'Grouped bar': 'أعمدة مجمّعة', 'Horizontal bar': 'أعمدة أفقية', 'KPI tile': 'بطاقة مؤشر',
  'Mini table': 'جدول مصغّر', 'Mono blue': 'أزرق أحادي', 'Neotec brand': 'هوية نيوتك', 'Palette': 'لوحة الألوان',
  'Pie': 'دائري', 'Saved dashboards': 'اللوحات المحفوظة', 'Series': 'السلاسل', 'Source run': 'مصدر التشغيل',
  'Stacked bar': 'أعمدة متراكمة', 'Title': 'العنوان', 'Visuals workspace': 'مساحة الرسوم', 'Warm': 'دافئ', 'YTD': 'منذ بداية السنة',
  // v2.48.2 — Brand Kit / print letterhead
  'Rendering…': 'جارٍ التحويل…',
  'PDF rendering failed — use Print → Save as PDF instead.': 'تعذّر إنشاء ملف PDF — استخدم الطباعة ← حفظ كـ PDF بدلًا من ذلك.',
  'Browser header & footer': 'رأس وتذييل المتصفح',
  'Suppress': 'إخفاء',
  'Show': 'إظهار',
  'The browser prints its own date, title and URL in the page margin. Suppressing removes the margin — which also removes page numbers, the only place a page counter can live.': 'يطبع المتصفح تاريخه وعنوانه ورابطه في هامش الصفحة. الإخفاء يُلغي الهامش — وبذلك تُلغى أرقام الصفحات، فهي المكان الوحيد الذي يمكن أن يوجد فيه عدّاد الصفحات.',
  'Comparative annual totals — switches Period mode back to Fiscal year': 'إجماليات سنوية مقارنة — يعيد وضع الفترة إلى السنة المالية',
  'Comparative annual totals across multiple fiscal years': 'إجماليات سنوية مقارنة عبر عدة سنوات مالية',
  'Applied — accent, surfaces and text taken from this palette': 'تم التطبيق — لون الهوية والأسطح والنص مأخوذة من هذه اللوحة',
  'Print colours': 'ألوان الطباعة',
  'Brand Kit': 'هوية الطباعة',
  'Match app theme': 'مطابقة سمة التطبيق',
  'From a palette': 'من لوحة ألوان',
  'Paste any hex list — with or without coverage percentages. The most saturated colour, weighted by coverage, becomes the accent; a palette with no colour in it stays neutral instead of being tinted.': 'الصق أي قائمة ألوان سداسية — بنسب التغطية أو بدونها. يصبح اللون الأكثر تشبعًا، مرجّحًا بنسبة تغطيته، لون الهوية؛ واللوحة الخالية من الألوان تبقى محايدة بدل أن تُصبغ.',
  'No colours found — paste one hex per line, e.g. #3F3F3F 0.03': 'لم يتم العثور على ألوان — الصق لونًا سداسيًا في كل سطر، مثال: ‎#3F3F3F 0.03',
  'Monochrome': 'أحادي اللون',
  'Header text': 'نص الرأس',
  'Page numbers': 'أرقام الصفحات',
  'Hidden': 'مخفي',
  'Element': 'العنصر',
  'Logo': 'الشعار',
  'Company details': 'بيانات الشركة',
  'Letterhead layout': 'تخطيط الترويسة',
  'VAT No': 'الرقم الضريبي',
  'CR No': 'رقم السجل التجاري',
  'Center': 'وسط',
  'Small': 'صغير',
  'Large': 'كبير',
  'Default': 'افتراضي',
  'Print header & footer': 'رأس وتذييل الطباعة',
  'Print header & footer setup': 'إعداد رأس وتذييل الطباعة',
  'Header title': 'عنوان الرأس',
  'Header subtitle': 'العنوان الفرعي',
  'Show company name': 'إظهار اسم الشركة',
  'Show period (from → to)': 'إظهار الفترة (من – إلى)',
  'Footer text': 'نص التذييل',
  'Page numbers (Page X / Y)': 'أرقام الصفحات (صفحة س / ص)',
  'Generated timestamp': 'ختم وقت الإنشاء',
  'Company logo URL': 'رابط شعار الشركة',
  'Logo position': 'موضع الشعار',
  'Logo size': 'حجم الشعار',
  'Center header block': 'توسيط كتلة الرأس',
  'Heading size': 'حجم العنوان',
  'Body size': 'حجم النص',
  'Borders': 'الحدود',
  'Accent color': 'لون الهوية',
  'Paper': 'الورق',
  'Orientation': 'الاتجاه',
  'Landscape': 'أفقي',
  'Portrait': 'عمودي',
  'Left': 'يسار',
  'Right': 'يمين',
  'Minimal (hairline)': 'أدنى (خط رفيع)',
  'Classic': 'كلاسيكي',
  'Strong': 'قوي',
  'Compact': 'مضغوط',
  'XL': 'كبير جدًا',
  'Company name (as printed)': 'اسم الشركة (كما يُطبع)',
  'Company name — Arabic': 'اسم الشركة — بالعربية',
  'VAT number': 'الرقم الضريبي',
  'CR number': 'رقم السجل التجاري',
  'optional second line': 'سطر ثانٍ اختياري',
  'Generated': 'أُنشئ في',
  'Pop-ups are blocked — allow them for this site to print.': 'النوافذ المنبثقة محظورة — يرجى السماح بها لهذا الموقع لتتم الطباعة.',
  'e.g. branch, department, note': 'مثال: فرع، قسم، ملاحظة',
  'e.g. Confidential — internal use only': 'مثال: سري — للاستخدام الداخلي فقط',
  'Header and footer repeat on every printed page and adapt to the chosen paper size and orientation. Saved per company. PDF = Print → Save as PDF.': 'يتكرر الرأس والتذييل في كل صفحة مطبوعة ويتكيّفان مع حجم الورق والاتجاه المختارين. تُحفظ الإعدادات لكل شركة. ملف PDF = طباعة ← حفظ كـ PDF.',
};

export function t(s: string): string {
  if (currentLang !== 'ar') return s;
  return Object.prototype.hasOwnProperty.call(DICT, s) ? DICT[s] : s;
}

export const LangContext = createContext<{ lang: Lang; toggle: () => void }>({
  lang: 'en', toggle: () => { /* noop */ },
});
export function useLang() { return useContext(LangContext); }

// ── Arabic labels sourced from master records (Account / Cost Center / … ) ──
// Filled at runtime from the backend (which reads Insight AI Settings →
// Arabic Label Sources). `arName` returns the Arabic name when Arabic is
// active and a mapping exists, else the supplied fallback. Selectable values
// never change — this only affects displayed text.
const flatLabels: Record<string, string> = {};

export function mergeLabels(map: Record<string, string>) {
  if (map) for (const k in map) { if (map[k]) flatLabels[k] = map[k]; }
}
export function arName(name: string, fallback?: string): string {
  if (currentLang === 'ar' && name && flatLabels[name]) return flatLabels[name];
  return fallback != null ? fallback : name;
}

function apiGet(url: string): Promise<any> {
  let csrf = '';
  try { csrf = (window as any).csrf_token || ''; } catch { /* ignore */ }
  return fetch(url, { credentials: 'same-origin', headers: { 'X-Frappe-CSRF-Token': csrf } })
    .then((r) => r.json()).then((j) => (j && j.message) || null).catch(() => null);
}

const AI = '/api/method/neotec_insight.neotec_insight.api.ai.';

/** Load dimension/company options and cache their Arabic labels by name. */
export function loadDimensionOptions(kind: string, company?: string): Promise<void> {
  const q = company ? ('&company=' + encodeURIComponent(company)) : '';
  return apiGet(AI + 'dimension_options?kind=' + encodeURIComponent(kind) + q).then((rows) => {
    const list = Array.isArray(rows) ? rows : [];
    const map: Record<string, string> = {};
    list.forEach((r) => { if (r && r.label_ar) map[r.name] = r.label_ar; });
    mergeLabels(map);
  });
}

/** Load Arabic labels for a set of master records (e.g. accounts) by name. */
export function loadArabicLabels(sourceDoctype: string, names: string[]): Promise<void> {
  const uniq = Array.from(new Set((names || []).filter(Boolean)));
  if (uniq.length === 0) return Promise.resolve();
  const url = AI + 'arabic_labels?source_doctype=' + encodeURIComponent(sourceDoctype)
    + '&names=' + encodeURIComponent(JSON.stringify(uniq));
  return apiGet(url).then((m) => { if (m && typeof m === 'object') mergeLabels(m); });
}

function apiPost(url: string, body: any): Promise<any> {
  let csrf = '';
  try { csrf = (window as any).csrf_token || (window as any).frappe?.csrf_token || ''; } catch { /* ignore */ }
  return fetch(url, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': csrf },
    body: JSON.stringify(body),
  }).then((r) => r.json()).then((j) => (j && j.message) || null);
}

/** Save (or, with empty arabic, clear) a user Arabic-translation override.
 *  Updates the in-memory label cache immediately so the UI reflects it. */
export function saveTranslationOverride(
  sourceDoctype: string, name: string, arabic: string, english?: string,
): Promise<boolean> {
  const ar = (arabic || '').trim();
  return apiPost(AI + 'save_translation_override', {
    source_doctype: sourceDoctype, source_name: name, arabic: ar, english: english || '',
  }).then((res) => {
    if (ar) flatLabels[name] = ar; else delete flatLabels[name];
    return !!res;
  }).catch(() => false);
}

/** True when a stored Arabic label exists for this name. */
export function hasArabic(name: string): boolean {
  return !!(name && flatLabels[name]);
}
