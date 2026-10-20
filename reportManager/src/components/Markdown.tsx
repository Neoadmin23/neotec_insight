import type { ReactNode } from 'react';

/* v2.94.0 — renders the Markdown that AI replies use (headings, bold,
 * italics, inline code, bullet and numbered lists, rules, paragraphs) as
 * React elements. Nothing is inserted as HTML, so text from a model or from
 * the books can never inject markup into the page. Unknown syntax simply
 * shows as text. */

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  // **bold**, *italic* / _italic_, `code`
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${key}-${i++}`;
    if (tok.startsWith('**')) out.push(<strong key={k}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith('`')) out.push(<code key={k}>{tok.slice(1, -1)}</code>);
    else out.push(<em key={k}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      const k = `p${blocks.length}`;
      blocks.push(<p key={k}>{inline(para.join(' '), k)}</p>);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      const k = `l${blocks.length}`;
      const items = list.items.map((it, j) => <li key={`${k}-${j}`}>{inline(it, `${k}-${j}`)}</li>);
      blocks.push(list.ordered ? <ol key={k}>{items}</ol> : <ul key={k}>{items}</ul>);
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const h = /^(#{1,6})\s+(.*)$/.exec(line.trim());
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const num = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flushPara(); flushList(); blocks.push(<hr key={`hr${blocks.length}`} />); continue; }
    if (h) {
      flushPara(); flushList();
      const level = Math.min(h[1].length + 2, 6);   // ### -> h5: inside cards, keep headings modest
      const k = `h${blocks.length}`;
      const Tag = (`h${level}`) as 'h3' | 'h4' | 'h5' | 'h6';
      blocks.push(<Tag key={k}>{inline(h[2], k)}</Tag>);
      continue;
    }
    if (bullet || num) {
      flushPara();
      const ordered = !!num && !bullet;
      if (!list || list.ordered !== ordered) { flushList(); list = { ordered, items: [] }; }
      list.items.push((bullet ? bullet[1] : num![1]).trim());
      continue;
    }
    flushList();
    para.push(line.trim());
  }
  flushPara(); flushList();
  return <div className={'md ' + (className || '')}>{blocks}</div>;
}
