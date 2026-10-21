import { useLayoutEffect, useRef, useState } from 'react';
import { PanelPicker, panelTitle, type PanelId, type PanelInstance } from './PanelPicker';
import { PanelContext, useGlobalFilters, type PanelSettings } from './state';
import { TimeSeries, Comparison } from './AnalysisCharts';
import { EventMap } from './EventMap';
import { RecordTable, StatCard } from './RecordPanels';
import { currentView, panelDatasets, PANEL_VIEWS, viewSettings } from './panelViews.ts';
import { applySettingsPatch, effectiveSettings, panelUsesGlobalYear } from './globalFilters.ts';
import { usePanelDrag } from './usePanelDrag.ts';
import { PanelCaveats } from './PanelCaveats';
import { PanelYearPin } from './PanelYearPin';
import { WorkspaceFilters } from './WorkspaceFilters';
import { OptimizationStudy } from './OptimizationStudy';
import { STUDY_NOTES } from './studyData.ts';

const CONTENT: Record<PanelId, () => React.JSX.Element> = { map: EventMap, time_series: TimeSeries, comparison: Comparison, record_table: RecordTable, stat_card: StatCard, optimization: OptimizationStudy };
function PanelTitle({ panel, onRename }: { panel: PanelInstance; onRename: (id: number, name: string) => void }) {
  const title = panelTitle(panel);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const cancelled = useRef(false);
  const titleButton = useRef<HTMLButtonElement>(null);
  const save = () => { if (!cancelled.current && draft.trim()) onRename(panel.id, draft.trim()); setEditing(false); };
  return <h2 id={`panel-title-${panel.id}`} className="panel-title" aria-label={title}>
    {editing ? <input autoFocus aria-label="Panel name" value={draft} onFocus={e => e.currentTarget.select()} onChange={e => setDraft(e.target.value)} onBlur={save} onKeyDown={e => {
      if (e.nativeEvent.isComposing) return;
      if (e.key === 'Enter' || e.key === 'Escape') { e.preventDefault(); cancelled.current = e.key === 'Escape'; e.currentTarget.blur(); requestAnimationFrame(() => titleButton.current?.focus({ preventScroll: true })); }
    }} /> : <button ref={titleButton} type="button" className="panel-title-button" title="Click to rename" aria-label={`Rename ${title}`} onClick={() => { setDraft(title); cancelled.current = false; setEditing(true); }}>{title}</button>}
  </h2>;
}
export function PanelWorkspace({ panels, onRemove, onRename, onUpdate, onDuplicate, onReorder }: {
  panels: PanelInstance[]; onRemove: (id: number) => void;
  onRename: (id: number, name: string) => void; onUpdate: (id: number, patch: Partial<PanelSettings>) => void;
  onDuplicate: (id: number) => void;
  onReorder: (id: number, index: number) => void;
}) {
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const drag = usePanelDrag(onReorder);
  const global = useGlobalFilters();
  if (!panels.length) return null;
  return <section className="panel-workspace" aria-label="Panel workspace"><header className="workspace-heading"><h1>Your workspace <span>{panels.length} panels</span></h1></header>
    <WorkspaceFilters filters={global.filters} onYearChange={global.setYear} />
    <div ref={drag.gridRef} className="panel-grid">{panels.map(panel => <PanelFrame key={panel.id} panel={panel} expanded={expandedId === panel.id} drag={drag}
      onExpand={() => { drag.stopMotion(); setExpandedId(panel.id); }} onRestore={() => setExpandedId(null)} onRename={onRename} onDuplicate={()=>onDuplicate(panel.id)}
      onRemove={() => { if (expandedId === panel.id) setExpandedId(null); onRemove(panel.id); }} onUpdate={patch => onUpdate(panel.id, patch)} />)}</div>
  </section>;
}

function PanelFrame({ panel, expanded, onExpand, onRestore, onRemove, onRename, onUpdate, onDuplicate, drag }: {
  panel: PanelInstance; expanded: boolean; onExpand: () => void; onRestore: () => void;
  onRemove: () => void; onRename: (id: number, name: string) => void; onUpdate: (patch: Partial<PanelSettings>) => void;
  onDuplicate: () => void;
  drag: ReturnType<typeof usePanelDrag>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const wasExpanded = useRef(false);
  const Content = CONTENT[panel.type];
  const [actionsHost,setActionsHost]=useState<HTMLDivElement|null>(null);
  const [choosingView, setChoosingView] = useState(false);
  const global = useGlobalFilters();
  const settings = effectiveSettings(panel.type, panel.settings, global.filters);
  const update = (patch: Partial<PanelSettings>) => onUpdate(applySettingsPatch(panel.type, panel.settings, patch, global.filters));
  useLayoutEffect(() => {
    const element = dialog.current!;
    const root = document.documentElement;
    const previousOverflow = root.style.overflow;
    element.close();
    if (expanded) {
      root.style.overflow = 'hidden';
      element.showModal();
      closeButton.current?.focus({ preventScroll: true });
    } else {
      // Nonmodal overview: keep the same content and Leaflet instance mounted.
      element.open = true;
      if (wasExpanded.current) expandButton.current?.focus({ preventScroll: true });
    }
    wasExpanded.current = expanded;
    return () => { element.close(); if (expanded) root.style.overflow = previousOverflow; };
  }, [expanded]);
  return <div data-panel-id={panel.id} className={`panel-slot${drag.draggingId === panel.id ? ' is-dragging' : ''}${drag.targetId === panel.id ? ' is-drop-target' : ''}`}><dialog ref={dialog} id={`panel-${panel.id}`} tabIndex={-1} role={expanded ? 'dialog' : 'group'} aria-modal={expanded || undefined}
    aria-labelledby={`panel-title-${panel.id}`} data-panel-type={panel.type} className={`workspace-panel ${expanded ? 'is-expanded' : ''}`}
    onCancel={event => { if (event.target !== event.currentTarget) return; event.preventDefault(); onRestore(); }}>
    <header className={`panel-header${expanded ? '' : ' is-draggable'}`} onPointerDown={event => { if (!expanded) drag.start(event, panel.id); }} onClickCapture={drag.suppressDragClick}>
      {!expanded && <svg className="panel-drag-grip" width="10" height="16" viewBox="0 0 10 16" fill="currentColor" aria-hidden="true"><circle cx="2" cy="3" r="1"/><circle cx="8" cy="3" r="1"/><circle cx="2" cy="8" r="1"/><circle cx="8" cy="8" r="1"/><circle cx="2" cy="13" r="1"/><circle cx="8" cy="13" r="1"/></svg>}
      <PanelTitle panel={panel} onRename={onRename} /><div className="panel-actions">
      {panelUsesGlobalYear(panel.type, panel.settings) && <PanelYearPin type={panel.type} settings={panel.settings} global={global.filters} onUpdate={onUpdate} />}
      <PanelCaveats datasets={panelDatasets(panel.type, panel.settings)} title={panelTitle(panel)} extra={panel.type === 'optimization' ? STUDY_NOTES : undefined}/>
      {PANEL_VIEWS.filter(view => view.type === panel.type).length > 1 && <button className="panel-expand" aria-label={`Change view for ${panelTitle(panel)}`} title="Change view" onClick={() => setChoosingView(true)}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M4 7h6m4 0h6M4 17h10m4 0h2"/><circle cx="12" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></svg>
      </button>}
      <div ref={setActionsHost} className="panel-export-host" /><button className="panel-expand" aria-label={`Duplicate ${panelTitle(panel)}`} title="Duplicate panel with filters" onClick={onDuplicate}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/></svg></button>
      {!expanded && <button ref={expandButton} className="panel-expand" title="Expand panel" aria-label={`Expand ${panelTitle(panel)}`} onClick={onExpand}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M9 3H3v6m12 12h6v-6M3 3l7 7m11 11-7-7" /></svg>
      </button>}<button ref={closeButton} className="panel-close" title={expanded ? 'Return to workspace (Esc)' : 'Remove panel'} aria-label={`${expanded ? 'Restore' : 'Close'} ${panelTitle(panel)}`} onClick={expanded ? onRestore : onRemove}>×</button>
    </div></header>
    <div className="panel-body" role="region" aria-label={`${panelTitle(panel)} content`} tabIndex={expanded ? 0 : undefined}>
      <PanelContext.Provider value={{ settings, update, expanded, expand: onExpand, actionsHost, title: panelTitle(panel) }}><Content /></PanelContext.Provider>
    </div>
    {choosingView && <PanelPicker category={panel.type} activeView={currentView(panel.type, panel.settings)} onClose={() => setChoosingView(false)} onSelect={view => {
      onUpdate(viewSettings(panel.settings, view));
    }} />}
  </dialog></div>;
}
