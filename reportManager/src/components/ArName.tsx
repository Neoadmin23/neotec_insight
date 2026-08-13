import { useState } from 'react';
import { getLang, arName, hasArabic, saveTranslationOverride, t } from '../utils/i18n';

/* ArName (v2.0.0) — renders a master record's display name and, in Arabic
 * presentation, exposes an inline ✎ pencil so the user can fix/override the
 * Arabic translation. The override is saved to the backend (Insight
 * Translation Override) and applied immediately. An amber dot marks names
 * that are still showing the English fallback in Arabic mode, so untranslated
 * items are easy to spot and fix. */
export function ArName({
  name, fallback, source = 'Account', editable = true,
}: { name: string; fallback?: string; source?: string; editable?: boolean }) {
  const ar = getLang() === 'ar';
  const [override, setOverride] = useState<string | null>(null); // local echo after save
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);

  const shown = override != null
    ? (override || fallback || name)
    : arName(name, fallback);

  if (!ar || !editable) return <>{shown}</>;

  const translated = override != null ? !!override : hasArabic(name);

  function open() {
    setDraft(override != null ? override : (hasArabic(name) ? arName(name, '') : ''));
    setEditing(true);
  }
  function save() {
    setSaving(true);
    saveTranslationOverride(source, name, draft.trim(), fallback || name).then(() => {
      setOverride(draft.trim());
      setSaving(false);
      setEditing(false);
    });
  }

  return (
    <span className="arname">
      {!translated && <span className="arname-dot" title={t('No Arabic translation yet — click ✎ to add one')} />}
      <span className="arname-text">{shown}</span>
      <button type="button" className="arname-edit" title={t('Edit Arabic translation')} onClick={open}>✎</button>
      {editing && (
        <span className="arname-pop" onClick={(e) => e.stopPropagation()}>
          <input
            value={draft} dir="rtl" autoFocus
            placeholder={fallback || name}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false); }}
          />
          <button type="button" onClick={save} disabled={saving}>{saving ? '…' : t('Save')}</button>
          <button type="button" className="arname-x" onClick={() => setEditing(false)}>✕</button>
        </span>
      )}
    </span>
  );
}
