import type { ReactNode } from "react"
import { ThemeToggle } from "../ThemeToggle.tsx"
import "./access.css"

/** The workspace header (brand left, theme toggle right) with page actions in the corner. */
export function AccessHeader({ children }: { children?: ReactNode }) {
  return (
    <header className="site-header">
      <div className="site-brand">Wildfire <span>Analysis workspace</span></div>
      <div className="access-header-actions">
        <ThemeToggle />
        {children}
      </div>
    </header>
  )
}
