import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import Landing from './landing/Landing.jsx'

void StrictMode
void Landing

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Landing />
  </StrictMode>,
)
