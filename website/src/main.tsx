import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { applyTheme, readStoredTheme } from './theme.ts'

applyTheme(readStoredTheme())

const root = ReactDOM.createRoot(document.getElementById('root')!)
// The accounts build (`npm run build:accounts`) serves the site behind the
// accounts gateway; the Pages build keeps the anonymous workspace.
if (import.meta.env.VITE_ACCOUNTS === 'on') {
  import('./access/AccountsRoot.tsx').then(({ AccountsRoot }) => root.render(
    <React.StrictMode>
      <AccountsRoot />
    </React.StrictMode>,
  ))
} else {
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}
