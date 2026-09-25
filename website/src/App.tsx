import { useEffect, useRef, useState } from 'react';
import { PANELS, PanelPicker, PanelStrip, panelTitle, type PanelInstance } from './PanelPicker';
import { PanelWorkspace } from './PanelWorkspace';
import { GlobalFiltersContext, SelectionContext, newPanel } from './state';
import { DataSources } from './Controls';
import { EventDetail } from './RecordPanels';
import { askAgent, type AgentAnswer, type AgentStreamEvent } from './api.ts';
import { ToolTrace } from './ToolTrace';
import { DATASETS, type EventRecord } from './data.ts';
import { panelsFromAnswer, unsupportedViewNotice } from './answerPanels.ts';
import { updatePanelSettings, viewSettings, type PanelView } from './panelViews.ts';
import { movePanel } from './panelOrder.ts';
import { ThemeToggle } from './ThemeToggle.tsx';
import { GLOBAL_FILTERS_STORAGE_KEY, parseStoredGlobalFilters } from './globalFilters.ts';
import { initialInlineViews, selectInlineViews, snapshotInlinePanel, type InlineAnswerView } from './inlineViews.ts';
import { InlineAnswerViews } from './InlineAnswerViews.tsx';

interface Message { id: string; role: 'user' | 'assistant'; content: string; error?: boolean; response?: AgentAnswer; events?: AgentStreamEvent[]; inlineViews?: InlineAnswerView[] }
const STORAGE_KEY = 'wildfire-workspace-v1';
function initialPanels(): PanelInstance[] {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null');
    const saved = Array.isArray(stored) ? stored.filter(panel => panel?.type !== 'spatial_context') : stored;
    if (Array.isArray(saved) && saved.every(p => Number.isInteger(p.id) && PANELS.some(t => t.id === p.type) && (!p.name || typeof p.name === 'string') && p.settings
      && (p.nameIsCustom === undefined || typeof p.nameIsCustom === 'boolean')
      && DATASETS.some(d => d.id === p.settings.dataset) && p.settings.filters && ['start','end','county','utility'].every(k => typeof p.settings.filters[k] === 'string')
      && ['daily','weekly','monthly','quarterly'].includes(p.settings.interval) && ['cause','county','utility'].includes(p.settings.groupBy)
      && ['count','share'].includes(p.settings.measure) && ['events','acres','counties','customers'].includes(p.settings.metric)
      && Array.isArray(p.settings.datasets) && p.settings.datasets.every((id: string) => DATASETS.some(d => d.id === id))
      && Array.isArray(p.settings.overlays) && p.settings.overlays.every((id: string) => ['hftd','territories','hdw'].includes(id))
      && (p.settings.filterMode === undefined || ['inherit','override'].includes(p.settings.filterMode))
      && (p.settings.mapMode === undefined || ['events','risk','residual'].includes(p.settings.mapMode))
      && (p.settings.mapView === undefined || ['range','daily'].includes(p.settings.mapView))
      && (p.settings.playbackDate === undefined || typeof p.settings.playbackDate === 'string')
      && (p.settings.riskDate === undefined || typeof p.settings.riskDate === 'string')
      && (p.settings.statMode === undefined || ['summary','medical_exposure','model_metrics'].includes(p.settings.statMode))
      && (p.settings.metricsCitation === undefined || (Number.isInteger(p.settings.metricsCitation?.evalYear) && typeof p.settings.metricsCitation?.paramsSha256 === 'string'))
      && (p.settings.weatherYear === undefined || Number.isInteger(p.settings.weatherYear))
      && (p.settings.weatherDate === undefined || typeof p.settings.weatherDate === 'string')
      && (p.settings.seriesMode === undefined || ['timeline','yearly','regional','seasonal','cumulative_acres','customer_events'].includes(p.settings.seriesMode))
      && (p.settings.seasonYears === undefined || (Array.isArray(p.settings.seasonYears) && p.settings.seasonYears.every((year: unknown) => Number.isInteger(year) && Number(year) >= 1900 && Number(year) <= 2100)))
      && (p.settings.comparisonYears === undefined || (Array.isArray(p.settings.comparisonYears) && p.settings.comparisonYears.every((year: unknown) => Number.isInteger(year) && Number(year) >= 1900 && Number(year) <= 2100)))
      && !p.settings.answerStat) && new Set(saved.map(p => p.id)).size === saved.length) return saved;
  } catch { /* Storage is optional; unavailable or old state opens the default workspace. */ }
  return PANELS.map((p, index) => newPanel(index + 1, p.id));
}
function Markdown({ text }: { text: string }) {
  return <>{text.split('\n').map((line, index) => <p key={index} className={line ? '' : 'paragraph-gap'}>{line.split(/(\*\*[^*]+\*\*)/g).map((part, i) => part.startsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part)}</p>)}</>;
}
function AnswerViewNotice({answer}: {answer?: AgentAnswer}) {
  const notice = answer ? unsupportedViewNotice(answer) : null;
  return notice ? <p className="answer-view-notice" role="status">{notice}</p> : null;
}
export default function App() {
  const [panels, setPanels] = useState<PanelInstance[]>(initialPanels);
  const [globalFilters, setGlobalFilters] = useState(() => {
    try { return parseStoredGlobalFilters(localStorage.getItem(GLOBAL_FILTERS_STORAGE_KEY)); }
    catch { return parseStoredGlobalFilters(null); }
  });
  const [showPicker, setShowPicker] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [streamEvents, setStreamEvents] = useState<AgentStreamEvent[]>([]);
  const [detail, setDetail] = useState<EventRecord | null>(null);
  const [storageError, setStorageError] = useState(false);
  const [showBack, setShowBack] = useState(false);
  const nextId = useRef(Math.max(0, ...panels.map(p => p.id)) + 1);
  const controller = useRef<AbortController | null>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const focusedViews = useRef(initialInlineViews(panels, globalFilters));
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(panels.filter(p => !p.settings.answerStat))); setStorageError(false); }
    catch { setStorageError(true); }
  }, [panels]);
  useEffect(() => {
    try { localStorage.setItem(GLOBAL_FILTERS_STORAGE_KEY, JSON.stringify(globalFilters)); }
    catch { /* Workspace year persistence is optional. */ }
  }, [globalFilters]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => { if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight; }, [messages, busy, progress]);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setShowBack(entry.isIntersecting), { rootMargin: '0px 0px -80px 0px' });
    observer.observe(workspaceRef.current!); return () => observer.disconnect();
  }, []);
  function removePanel(id: number) { setPanels(current => current.filter(p => p.id !== id)); }
  function addPanel(view: PanelView) {
    const panel = newPanel(nextId.current++, view.type);
    panel.name = view.title; panel.settings = viewSettings(panel.settings, view);
    setPanels(current => [...current, panel]);
  }
  function duplicatePanel(id: number) {
    const original=panels.find(panel=>panel.id===id); if(!original) return;
    const copy: PanelInstance = { ...original, id: nextId.current++, name: `${panelTitle(original)} · copy`, settings: structuredClone(original.settings) };
    setPanels(current=>{const index=current.findIndex(panel=>panel.id===id);return index<0?current:[...current.slice(0,index+1),copy,...current.slice(index+1)];});
  }
  function locatePanel(id: number) {
    const element = document.getElementById(`panel-${id}`);
    element?.focus({ preventScroll: true }); element?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
  }
  function openInlinePanel(panel: PanelInstance) {
    if (panels.some(item => item.id === panel.id)) { locatePanel(panel.id); return; }
    setPanels(current => current.some(item => item.id === panel.id) ? current : [...current, panel]);
    requestAnimationFrame(() => locatePanel(panel.id));
  }
  function applyAnswer(answer: AgentAnswer) {
    const added = panelsFromAnswer(answer).map(spec => {
      const panel = newPanel(nextId.current++, spec.type);
      return { ...panel, name: spec.name, settings: { ...panel.settings, ...spec.settings } };
    });
    if (added.length) setPanels(current => [...current, ...added]);
    return added;
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (!query.trim() || busy) return;
    const question = query.trim(); setQuery(''); setBusy(true); setProgress('Connecting to the agent…');
    const events: AgentStreamEvent[] = []; setStreamEvents([]);
    setMessages(current => [...current, { id: crypto.randomUUID(), role: 'user', content: question }]);
    requestAnimationFrame(() => stageRef.current?.scrollIntoView({ block: 'start', behavior: 'instant' }));
    const abort = new AbortController(); controller.current = abort;
    const timeout = setTimeout(() => abort.abort(new Error('The agent did not respond within 4 minutes. You can still use the data panels.')), 240_000);
    try {
      const answer = await askAgent(question, abort.signal, setProgress, event => { events.push(event); setStreamEvents([...events]); });
      const qualifications = (answer.qualifications ?? []).map(q => q.text).filter(t => !answer.answer_text.includes(t));
      const added = answer.status === 'error' ? [] : applyAnswer(answer);
      const inlineViews = answer.status === 'answer' ? selectInlineViews(focusedViews.current, added.map(panel => snapshotInlinePanel(panel, globalFilters))) : undefined;
      if (inlineViews) focusedViews.current = inlineViews.map(view => view.panel);
      setMessages(current => [...current, { id: crypto.randomUUID(), role: 'assistant', content: [answer.answer_text, ...qualifications].join('\n\n'), error: answer.status === 'error', response: answer, events, inlineViews }]);
    } catch (error) {
      const reason = abort.signal.aborted ? abort.signal.reason : error;
      setMessages(current => [...current, { id: crypto.randomUUID(), role: 'assistant', content: reason instanceof Error ? reason.message : 'Agent unavailable. You can still use the data panels.', error: true, events }]);
    } finally { clearTimeout(timeout); setBusy(false); controller.current = null; }
  }
  return <SelectionContext.Provider value={{ inspect: setDetail }}>
    <GlobalFiltersContext.Provider value={{ filters: globalFilters, setYear: year => setGlobalFilters(current => ({ ...current, year })) }}>
    <main className={`demo-app ${panels.length ? 'has-panels' : ''} ${messages.length ? 'has-chat' : ''}`}>
      <header className="site-header">
        <div className="site-brand">Wildfire <span>Analysis workspace</span></div>
        <ThemeToggle />
      </header>
      <section id="workspace-top" className="workspace-intro" aria-label="Ask and choose panels">
        <div ref={stageRef} className="conversation-stage">
          {messages.length > 0 && <div ref={chatRef} className="chat-messages" aria-label="Conversation" aria-live="polite">{messages.map(message => <div key={message.id} className={`chat-message ${message.role} ${message.error ? 'message-error' : ''}`}><div className="chat-message-copy"><Markdown text={message.content} /></div>{message.role === 'assistant' && <><AnswerViewNotice answer={message.response}/>{message.inlineViews && <InlineAnswerViews views={message.inlineViews} onOpen={openInlinePanel} />}<ToolTrace answer={message.response} events={message.events ?? []} finished /></>}</div>)}{busy && <div><p className="panel-note">{progress}</p><ToolTrace events={streamEvents} finished={false} /></div>}</div>}
          <form onSubmit={submit} className="search-form"><input aria-label="Ask a question" value={query} onChange={e => setQuery(e.target.value)} placeholder="What do you want to know today~" />
            {busy ? <button type="button" aria-label="Cancel request" onClick={() => controller.current?.abort(new Error('Request cancelled.'))}>■</button> : query.trim() && <button type="submit" aria-label="Send message">↑</button>}
          </form>
          <div className="shortcut-section"><PanelStrip panels={panels} onRemove={removePanel} onLocate={locatePanel} onOpen={() => setShowPicker(true)} /></div>
          {storageError && <p className="panel-note">Browser storage is unavailable. This workspace will reset when you refresh.</p>}
        </div>
      </section>
      <div ref={workspaceRef}><PanelWorkspace panels={panels} onRemove={removePanel} onDuplicate={duplicatePanel} onRename={(id, name) => setPanels(current => current.map(p => p.id === id ? { ...p, name, nameIsCustom: true } : p))}
        onReorder={(id, index) => setPanels(current => movePanel(current, id, index))}
        onUpdate={(id, patch) => setPanels(current => current.map(p => p.id === id ? updatePanelSettings(p, patch) : p))} /></div>
      <DataSources />
      {panels.length > 0 && showBack && <a href="#workspace-top" className="back-to-panels" aria-label="Back to top">↑</a>}
      {showPicker && <PanelPicker onSelect={addPanel} onClose={() => setShowPicker(false)} />}
      {detail && <EventDetail record={detail} onClose={() => setDetail(null)} />}
    </main>
    </GlobalFiltersContext.Provider>
  </SelectionContext.Provider>;
}
