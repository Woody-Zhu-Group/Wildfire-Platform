import { useEffect, useRef, useState } from 'react';
import { EventMap } from './EventMap';
import { TimeSeries, Comparison } from './AnalysisCharts';
import type { PanelInstance } from './PanelPicker';
import { PanelContext } from './state';
import type { InlineAnswerView } from './inlineViews.ts';

const CONTENT = { map: EventMap, time_series: TimeSeries, comparison: Comparison };

function viewScope(panel: PanelInstance): string {
  const { filters } = panel.settings;
  const period = filters.start === filters.end ? filters.start : filters.start.slice(0, 4) === filters.end.slice(0, 4)
    ? filters.start.slice(0, 4) : `${filters.start} – ${filters.end}`;
  return [filters.county, filters.utility, period].filter(Boolean).join(' · ');
}

function viewTitle(panel: PanelInstance): string {
  if (panel.name) return panel.name;
  return panel.type === 'map' ? 'Event map' : panel.type === 'time_series' ? 'Event trends' : 'Comparison';
}

function InlineViewCard({ view, onOpen }: { view: InlineAnswerView; onOpen: (panel: PanelInstance) => void }) {
  const host = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const element = host.current!;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), {
      root: element.closest('.chat-messages'), rootMargin: '180px',
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const { panel, updated } = view;
  const Content = CONTENT[panel.type as keyof typeof CONTENT];
  const title = viewTitle(panel);
  return <article ref={host} className={`inline-view${updated ? '' : ' is-previous'}`}>
    <header className="inline-view-heading"><div><h3>{title}</h3><p>{viewScope(panel)}</p></div><span>{updated ? 'Updated' : 'Previous view'}</span></header>
    <div className="inline-view-content" inert>
      {visible && <PanelContext.Provider value={{ settings: panel.settings, update: () => {}, expanded: false, expand: () => onOpen(panel), actionsHost: null, title }}><Content /></PanelContext.Provider>}
    </div>
    <footer><span>View from workspace data</span><button type="button" onClick={() => onOpen(panel)}>Open full view ↗</button></footer>
  </article>;
}

export function InlineAnswerViews({ views, onOpen }: { views: InlineAnswerView[]; onOpen: (panel: PanelInstance) => void }) {
  if (!views.length) return null;
  return <div className="inline-answer-views" aria-label="Charts shown with this answer">
    {views.map(view => <InlineViewCard key={view.panel.id} view={view} onOpen={onOpen} />)}
  </div>;
}
