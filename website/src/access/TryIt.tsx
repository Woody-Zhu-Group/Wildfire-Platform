import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react"
import { DATASETS } from "../data.ts"
import { PanelCaveats } from "../PanelCaveats.tsx"
import { formatDay } from "./accessFlow.ts"
import { CaliforniaGrid } from "./CaliforniaGrid.tsx"
import { SNAPSHOT, cellCenter, formatCoordinate, type MapDataset, type SeriesDataset } from "./trySnapshot.ts"
import "./access.css"

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
const dataset = (id: SeriesDataset) => DATASETS.find(item => item.id === id)!
const NOUN: Record<SeriesDataset, string> = { cpuc: "ignitions", epss: "outages", calfire: "incidents" }
const GROUPED: { id: SeriesDataset; label: string }[] = [
  { id: "cpuc", label: "CPUC ignitions by utility" },
  { id: "epss", label: "EPSS outages by cause" },
  { id: "calfire", label: "CAL FIRE incidents by county" },
]

/**
 * A read-only corner of the workspace on the dated snapshot. Asking a question
 * needs the analysis agent, so `onAsk` hands the question to the sign-in dialog.
 */
export function TryIt({ onAsk, onSignIn, onRequest }: { onAsk: (question: string) => void; onSignIn: () => void; onRequest: () => void }) {
  const years = SNAPSHOT.years
  const [year, setYear] = useState(years[years.length - 1])
  const [question, setQuestion] = useState("")
  const ask = (event: FormEvent) => {
    event.preventDefault()
    if (question.trim()) onAsk(question.trim())
  }
  return (
    <section id="try-it" className="try-it" aria-labelledby="try-it-title">
      <header className="try-heading">
        <div>
          <h2 id="try-it-title">Try it</h2>
          <p>Snapshot from {formatDay(SNAPSHOT.generated)}</p>
        </div>
        <div className="measure-switch" role="group" aria-label="Year">
          {years.map(item => <button key={item} type="button" aria-pressed={item === year} onClick={() => setYear(item)}>{item}</button>)}
        </div>
      </header>
      <form className="search-form try-ask" onSubmit={ask}>
        <input aria-label="Ask a question" value={question} onChange={event => setQuestion(event.target.value)}
          placeholder="Ask about the data, for example: which county had the most CAL FIRE incidents?" />
        {question.trim() && <button type="submit" aria-label="Ask (sign-in required)">↑</button>}
      </form>
      <div className="try-panels">
        <TryMap year={year} />
        <TrySeries year={year} />
        <TryComparison year={year} />
      </div>
      <p className="try-more">
        Live data, every filter, records and Ask need an account.{" "}
        <button type="button" className="text-button" onClick={onRequest}>Request access</button> or{" "}
        <button type="button" className="text-button" onClick={onSignIn}>sign in</button>.
      </p>
    </section>
  )
}

function TryPanel({ title, datasets, className, controls, children }: {
  title: string; datasets: SeriesDataset[]; className?: string; controls?: ReactNode; children: ReactNode
}) {
  const id = useId()
  return (
    <article className={`try-panel ${className ?? ""}`} aria-labelledby={id}>
      <header className="panel-header">
        <h3 id={id}>{title}</h3>
        <PanelCaveats datasets={datasets} title={title} />
      </header>
      {controls && <div className="try-panel-controls">{controls}</div>}
      <div className="try-panel-body">{children}</div>
    </article>
  )
}

function TryMap({ year }: { year: number }) {
  const [shown, setShown] = useState<MapDataset>("cpuc")
  const [active, setActive] = useState<number | null>(null)
  const counts = SNAPSHOT.map[shown][year]
  const values = new Map(counts.cells)
  const max = Math.max(1, ...values.values())
  const { name, color } = dataset(shown)
  const noun = NOUN[shown]
  const center = active === null ? null : cellCenter(active)
  return (
    <TryPanel title="Map" datasets={[shown]} className="is-map" controls={
      <div className="measure-switch" role="group" aria-label="Map dataset">
        {(["cpuc", "calfire"] as const).map(id => <button key={id} type="button" aria-pressed={id === shown} onClick={() => setShown(id)}>{dataset(id).name}</button>)}
      </div>
    }>
      <div className="try-map">
        <CaliforniaGrid counts={counts.cells} color={color} active={active} onActive={setActive}
          label={`${name} ${noun} in ${year} by 0.24 degree grid cell: ${counts.total.toLocaleString("en-US")} records; the busiest cell has ${max}.`} />
      </div>
      <div className="series-readout try-map-readout" aria-live="polite">
        {center
          ? <span>{formatCoordinate(center)} · <strong>{(values.get(active!) ?? 0).toLocaleString("en-US")}</strong> {noun}</span>
          : <span title={counts.outside ? `${counts.outside} of these fall outside the model grid and are not drawn.` : undefined}>
              {name} {noun} in {year} · <strong>{counts.total.toLocaleString("en-US")}</strong>
            </span>}
        <span className="try-legend" aria-hidden="true">1<i style={{ background: `linear-gradient(90deg, ${color}40, ${color})` }} />{max} per cell</span>
      </div>
    </TryPanel>
  )
}

function TrySeries({ year }: { year: number }) {
  const ids: SeriesDataset[] = ["cpuc", "epss", "calfire"]
  const [selected, setSelected] = useState<SeriesDataset[]>(ids)
  const [hovered, setHovered] = useState<number | null>(null)
  const host = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 480, height: 190 })
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setSize({ width: Math.max(240, entry.contentRect.width), height: Math.max(140, entry.contentRect.height) }))
    observer.observe(host.current!)
    return () => observer.disconnect()
  }, [])
  const visible = ids.filter(id => selected.includes(id)).map(id => ({ ...dataset(id), values: SNAPSHOT.series[id][year] }))
  const ceiling = Math.ceil(Math.max(4, ...visible.flatMap(item => item.values)) / 4) * 4
  const { width, height } = size
  const left = 36, right = width - 12, top = 22, bottom = height - 24
  const x = (index: number) => left + index / 11 * (right - left)
  const y = (value: number) => bottom - value / ceiling * (bottom - top)
  return (
    <TryPanel title="Time series" datasets={selected.length ? selected : ids} controls={
      <div className="dataset-switches" role="group" aria-label="Visible datasets">
        {ids.map(id => <button key={id} type="button" role="checkbox" aria-checked={selected.includes(id)}
          onClick={() => setSelected(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id])}>
          <span className="dataset-swatch" style={{ background: dataset(id).color }} />{dataset(id).name}
          <span aria-hidden="true" className="dataset-check">{selected.includes(id) ? "✓" : "−"}</span>
        </button>)}
      </div>
    }>
      <div ref={host} className="series-plot try-series-plot">
        {visible.length ? <svg viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0} aria-label={`Monthly event counts in ${year}. Use left and right arrows to inspect months.`}
          onMouseLeave={() => setHovered(null)}
          onMouseMove={event => {
            const bounds = event.currentTarget.getBoundingClientRect()
            setHovered(Math.max(0, Math.min(11, Math.round(((event.clientX - bounds.left) / bounds.width * width - left) / (right - left) * 11))))
          }}
          onKeyDown={event => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return
            event.preventDefault()
            setHovered(current => Math.max(0, Math.min(11, (current ?? 0) + (event.key === "ArrowRight" ? 1 : -1))))
          }}>
          <text x={left} y="12">Events</text>
          {[0, 1, 2, 3, 4].map(i => <g key={i}><path d={`M${left} ${y(ceiling * i / 4)}H${right}`} stroke="var(--chart-grid)" /><text x={left - 8} y={y(ceiling * i / 4) + 4} textAnchor="end">{ceiling * i / 4}</text></g>)}
          {[0, 3, 6, 9, 11].map(index => <text key={index} x={x(index)} y={height - 8} textAnchor={index === 0 ? "start" : index === 11 ? "end" : "middle"}>{MONTHS[index]}</text>)}
          {visible.map(item => <g key={item.id}>
            <polyline points={item.values.map((value, index) => `${x(index)},${y(value)}`).join(" ")} fill="none" stroke={item.color} strokeWidth="2" strokeLinejoin="round" />
            {item.values.map((value, index) => <circle key={index} cx={x(index)} cy={y(value)} r="2.5" fill={item.color} />)}
          </g>)}
          {hovered !== null && <g>
            <path d={`M${x(hovered)} ${top}V${bottom}`} stroke="var(--chart-cursor)" strokeDasharray="3 4" />
            {visible.map(item => <circle key={item.id} cx={x(hovered)} cy={y(item.values[hovered])} r="4" fill={item.color} stroke="var(--plot-marker-stroke)" strokeWidth="2" />)}
          </g>}
        </svg> : <p className="chart-empty">Select at least one dataset to show a line.</p>}
      </div>
      {visible.length > 0 && <div className="series-readout" aria-live="polite">
        <span>{hovered === null ? year : `${MONTHS[hovered]} ${year}`}</span>
        <div>{visible.map(item => <span key={item.id} style={{ color: item.color }}>{item.name} <strong>{(hovered === null ? item.values.reduce((a, b) => a + b, 0) : item.values[hovered]).toLocaleString("en-US")}</strong></span>)}</div>
      </div>}
    </TryPanel>
  )
}

function TryComparison({ year }: { year: number }) {
  const [shown, setShown] = useState<SeriesDataset>("cpuc")
  const group = SNAPSHOT.groups[shown].years[year]
  const { color } = dataset(shown)
  const rows = group.rows.slice(0, 6)
  const max = Math.max(1, ...group.rows.map(row => row.value ?? 0))
  const hidden = group.rows.length - rows.length
  return (
    <TryPanel title="Comparison" datasets={[shown]} controls={
      <label className="try-select">
        <span className="visually-hidden">Comparison</span>
        <select value={shown} onChange={event => setShown(event.target.value as SeriesDataset)}>
          {GROUPED.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>
    }>
      <div className="try-bars">
        <div className="bar-summary" title={group.note && group.multi_county_incidents ? `${group.multi_county_incidents} of these incidents list more than one county. ${group.note}` : undefined}>
          <span>{group.total.toLocaleString("en-US")} {NOUN[shown]} in {year}</span><span>Event count</span>
        </div>
        <div className="analysis-bars" role="list" aria-label={`${GROUPED.find(item => item.id === shown)!.label}, ${year}`}>
          {rows.map(row => <div key={row.label} className="analysis-bar-row" role="listitem">
            <span className="bar-label" title={row.label}>{row.label}</span>
            <div className="bar-track">{row.value === null
              ? <span className="missing-bar" title={row.reason}>No data</span>
              : <div className="value-bar" style={{ background: color, width: `${row.value / max * 100}%` }} />}</div>
            <span className="bar-value"><strong>{row.value === null ? "No data" : row.value.toLocaleString("en-US")}</strong></span>
          </div>)}
        </div>
        {hidden > 0 && <p className="overview-more">{hidden} more {hidden === 1 ? "category" : "categories"} in the workspace</p>}
      </div>
    </TryPanel>
  )
}
