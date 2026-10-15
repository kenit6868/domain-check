import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { AppProviders } from './app/providers/AppProviders'
import { AppRouter } from './app/router/AppRouter'
import './styles/app.css'

const root = document.getElementById('root')
if (!root) throw new Error('Missing application root')



createRoot(root).render(
  <StrictMode>
    <AppProviders><AppRouter /></AppProviders>
  </StrictMode>,
)

