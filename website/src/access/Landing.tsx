import { useState } from "react"
import { DATASETS, type DatasetId } from "../data.ts"
import { AccessDialog, type AccessTab } from "./AccessDialog.tsx"
import { AccessHeader } from "./AccessHeader.tsx"
import { CaliforniaGrid } from "./CaliforniaGrid.tsx"
import { SNAPSHOT } from "./trySnapshot.ts"
import { TryIt } from "./TryIt.tsx"
import "./access.css"

const SOURCES: { dataset?: DatasetId; name: string; detail: string }[] = [
  { dataset: "cpuc", name: "CPUC ignitions", detail: "Utility-reported ignitions filed with the CPUC." },
  { dataset: "calfire", name: "CAL FIRE incidents", detail: "Fire incidents posted by CAL FIRE, with acres burned." },
  { dataset: "epss", name: "EPSS outages", detail: "PG&E Enhanced Powerline Safety Settings outages by circuit." },
  { dataset: "psps", name: "PSPS events", detail: "Public Safety Power Shutoffs and the circuits they affected." },
  { name: "Context layers", detail: "HFTD fire-threat tiers, utility territories, historical fire weather and a modeled ignition-risk hindcast." },
]
const color = (id: DatasetId) => DATASETS.find(item => item.id === id)!.color
const ASK_NOTICE = "Questions are answered by the analysis agent, which needs an account."

/** What a visitor sees before signing in: the platform on its own grid, and a snapshot to try. */
export function Landing() {
  const [dialog, setDialog] = useState<{ tab: AccessTab; notice?: string } | null>(null)
  const hero = SNAPSHOT.hero
  const heroYears = `${hero.start.slice(0, 4)}–${hero.end.slice(0, 4)}`
  return (
    <div className="access-page">
      <AccessHeader>
        <button type="button" className="access-text-button access-header-request" onClick={() => setDialog({ tab: "request" })}>Request access</button>
        <button type="button" className="access-entry" onClick={() => setDialog({ tab: "sign-in" })}>Sign in</button>
      </AccessHeader>
      <main className="landing">
        <section className="landing-hero" aria-labelledby="landing-title">
          <div className="landing-hero-copy">
            <h1 id="landing-title">California wildfire and utility data, in one workspace.</h1>
            <p>Map, chart and compare ignitions, fire incidents, outages and power shutoffs, or ask a question in plain language.</p>
            <div className="landing-actions">
              <a className="access-primary landing-try" href="#try-it">Try it<span aria-hidden="true">↓</span></a>
              <button type="button" className="quiet-button" onClick={() => setDialog({ tab: "request" })}>Request access</button>
            </div>
          </div>
          <figure className="landing-figure">
            <CaliforniaGrid counts={hero.cells} color={color("cpuc")} glow
              label={`CPUC ignitions, ${heroYears}, on the risk model's 0.24 degree grid: ${hero.total.toLocaleString("en-US")} ignitions.`} />
            <figcaption title={hero.outside ? `${hero.outside} of ${hero.total.toLocaleString("en-US")} ignitions fall outside the model grid and are not drawn.` : undefined}>
              <i style={{ background: color("cpuc") }} aria-hidden="true" />
              CPUC ignitions, {heroYears}, per 0.24° grid cell
            </figcaption>
          </figure>
        </section>
        <TryIt onAsk={() => setDialog({ tab: "sign-in", notice: ASK_NOTICE })}
          onSignIn={() => setDialog({ tab: "sign-in" })} onRequest={() => setDialog({ tab: "request" })} />
        <section className="landing-data" aria-labelledby="landing-data-title">
          <h2 id="landing-data-title">Data in the workspace</h2>
          <ul className="landing-sources">
            {SOURCES.map(source => (
              <li key={source.name}>
                <strong>
                  <i aria-hidden="true" className={source.dataset ? undefined : "is-context"} style={source.dataset ? { background: color(source.dataset) } : undefined} />
                  {source.name}
                </strong>
                <span>{source.detail}</span>
              </li>
            ))}
          </ul>
        </section>
      </main>
      <footer className="landing-footer">
        <span>Access is by invitation or approved request.</span>
        <button type="button" className="text-button" onClick={() => setDialog({ tab: "request" })}>Request access</button>
      </footer>
      {dialog && <AccessDialog initialTab={dialog.tab} notice={dialog.notice} onClose={() => setDialog(null)} />}
    </div>
  )
}
