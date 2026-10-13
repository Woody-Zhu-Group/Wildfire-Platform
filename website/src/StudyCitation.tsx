import { useState } from 'react';
import { STUDY_CITATION } from './citation.ts';

// A footer fold beside "Data sources & coverage", in the same style.
export function StudyCitation() {
  const [copy, setCopy] = useState<'idle' | 'copied' | 'failed'>('idle');
  return <details className="study-citation">
    <summary>Citation</summary>
    <p>{STUDY_CITATION.authors} ({STUDY_CITATION.year}). {STUDY_CITATION.title}.{' '}
      <a href={STUDY_CITATION.url} target="_blank" rel="noreferrer">arXiv:{STUDY_CITATION.eprint}</a> [{STUDY_CITATION.primaryClass}].</p>
    <pre>{STUDY_CITATION.bibtex}</pre>
    <button type="button" className="quiet-button" onClick={async () => {
      try { await navigator.clipboard.writeText(STUDY_CITATION.bibtex); setCopy('copied'); }
      catch { setCopy('failed'); }
    }}>{copy === 'copied' ? 'Copied' : copy === 'failed' ? 'Copy failed; select the text instead' : 'Copy BibTeX'}</button>
  </details>;
}
