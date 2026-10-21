import { useId, useState } from 'react';
import { ASK_HINTS, ASK_HINTS_SHOWN } from './askHints.ts';

// Example questions under the Ask box. A click fills the box; nothing is sent
// until the person presses Enter, so they can edit the year or place first.
export function AskHints({ onPick }: { onPick: (question: string) => void }) {
  const [more, setMore] = useState(false);
  const list = useId();
  const shown = more ? ASK_HINTS : ASK_HINTS.slice(0, ASK_HINTS_SHOWN);
  return <div className="ask-hints">
    <div id={list} className="ask-hint-list" role="group" aria-label="Example questions">
      {shown.map(question => <button key={question} type="button" className="ask-hint" onClick={() => onPick(question)}>{question}</button>)}
    </div>
    <button type="button" className="ask-hints-toggle" aria-expanded={more} aria-controls={list}
      onClick={() => setMore(value => !value)}>{more ? 'Show less' : 'Show more'}</button>
  </div>;
}
