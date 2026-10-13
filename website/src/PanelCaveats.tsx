import { useEffect, useRef } from 'react';
import { datasetCaveats } from './caveats.ts';
import type { DatasetId } from './data.ts';
import { useDismissDetails } from './useDismissDetails.ts';

export function PanelCaveats({datasets, title, extra = []}: {datasets: readonly DatasetId[]; title: string; extra?: readonly string[]}) {
  const notes = [...datasetCaveats(datasets), ...extra];
  const disclosure = useRef<HTMLDetailsElement>(null);
  const sourceKey = datasets.join('|');
  useDismissDetails(disclosure);
  useEffect(() => {
    if (disclosure.current) disclosure.current.open = false;
  }, [sourceKey]);
  if (!notes.length) return null;
  return <details ref={disclosure} className="panel-caveats" onPointerDown={event => event.stopPropagation()} onKeyDown={event => {
    if (event.key === 'Escape' && event.currentTarget.open) {
      event.preventDefault(); event.stopPropagation(); event.currentTarget.open = false;
      event.currentTarget.querySelector('summary')?.focus({preventScroll: true});
    }
  }}>
    <summary aria-label={`Data notes for ${title}`} title="Data notes"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></svg></summary>
    <div className="panel-caveats-note" role="note" aria-label="Dataset notes">{notes.map(note => <p key={note}>{note}</p>)}</div>
  </details>;
}
