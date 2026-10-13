import { useEffect, useId, useRef, useState } from "react";
import type { PanelSettings } from './state';
import { PANEL_VIEWS, type PanelView } from './panelViews.ts';

// Spatial context is shown directly on map events.
export const PANELS = [
  { id: "map", name: "Map", description: "Explore wildfire and outage locations.", icon: "M3 5l6-2 6 2 6-2v16l-6 2-6-2-6 2V5zm6-2v16m6-14v16" },
  { id: "time_series", name: "Time series", description: "Track events over days, months, or years.", icon: "M3 3v18h18M6 15l4-5 4 3 6-8" },
  { id: "comparison", name: "Comparison", description: "Compare utilities, regions, or periods.", icon: "M3 3v18h18M7 17v-5m5 5V6m5 11V9" },
  { id: "record_table", name: "Record table", description: "Browse individual events and their details.", icon: "M3 4h18v16H3V4zm0 5h18M3 14h18M9 4v16" },
  { id: "stat_card", name: "Stat card", description: "Highlight counts, risk, and key metrics.", icon: "M3 5h18v14H3V5zm4 10V9m4 6v-3m4 3V8" },
  { id: "optimization", name: "Planning study", description: "Explore the sectionalization and fast-trip optimization study.", icon: "M4 6h5m6 0h5M4 18h16M9 6a3 3 0 106 0 3 3 0 00-6 0M12 9v9" },
] as const;
// Categories a fresh workspace opens with; the planning study is added on request.
export const DEFAULT_PANEL_TYPES = PANELS.filter(panel => panel.id !== "optimization").map(panel => panel.id);

export type PanelId = typeof PANELS[number]["id"];
export interface PanelInstance { id: number; type: PanelId; name?: string; nameIsCustom?: boolean; settings: PanelSettings }
export function panelTitle(panel: PanelInstance) {
  return panel.name ?? `${PANELS.find(item => item.id === panel.type)!.name} ${panel.id}`;
}

function PanelIcon({ path }: { path: string }) {
  return <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d={path} /></svg>;
}

export function PanelStrip({ panels, onRemove, onOpen, onLocate }: {
  panels: PanelInstance[]; onRemove: (id: number) => void;
  onOpen: () => void; onLocate: (id: number) => void;
}) {
  return <nav aria-label="Open panels" className="panel-strip">
    {panels.map(instance => {
      const panel = PANELS.find(panel => panel.id === instance.type)!;
      const title = panelTitle(instance);
      return <div key={instance.id} className="panel-shortcut">
        <button type="button" onClick={() => onLocate(instance.id)} aria-label={`Go to ${title}`} aria-controls={`panel-${instance.id}`} className="panel-shortcut-icon">
          <PanelIcon path={panel.icon} />
        </button>
        <span className="panel-shortcut-label" title={title}>{title}</span>
        <button type="button" onClick={() => onRemove(instance.id)} aria-label={`Remove ${title}`} className="shortcut-remove">×</button>
      </div>;
    })}
    <button type="button" onClick={onOpen} className="panel-shortcut add-panel">
      <span className="panel-shortcut-icon"><PanelIcon path="M12 5v14M5 12h14" /></span>
      <span className="text-xs">Add panel</span>
    </button>
  </nav>;
}

export function PanelPicker({ onSelect, onClose, category, activeView }: {
  onSelect: (view: PanelView) => void; onClose: () => void;
  category?: PanelId; activeView?: string;
}) {
  const [selectedType, setSelectedType] = useState<PanelId>(category ?? 'map');
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden'; element.showModal();
    return () => { element.close(); document.documentElement.style.overflow = previous; };
  }, []);
  const type = PANELS.find(panel => panel.id === selectedType)!;
  return <dialog ref={dialog} className="panel-picker" aria-labelledby={titleId}
    onCancel={event => { event.stopPropagation(); onClose(); }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div onClick={event => event.stopPropagation()}>
      <header><h2 id={titleId}>{category ? `Change ${type.name} view` : 'Add panel'}</h2><button type="button" aria-label="Close panel picker" onClick={onClose}>×</button></header>
      {!category && <div className="panel-categories" role="group" aria-label="Panel category">
        {PANELS.map(panel => <button key={panel.id} type="button" aria-pressed={selectedType === panel.id} onClick={() => setSelectedType(panel.id)}>{panel.name}</button>)}
      </div>}
      <div className="panel-view-options" aria-label={`${type.name} views`}>
        {PANEL_VIEWS.filter(view => view.type === selectedType).map(view => <button key={view.id} type="button" aria-label={view.title} className="panel-view-option" aria-current={activeView === view.id ? 'true' : undefined}
          onClick={() => { onSelect(view); onClose(); }}>
          <PanelIcon path={type.icon} /><span><strong>{view.title}</strong><small>{view.description}</small></span>{activeView === view.id && <span className="current-view-mark" aria-label="Current view">✓</span>}
        </button>)}
      </div>
    </div>
  </dialog>;
}
