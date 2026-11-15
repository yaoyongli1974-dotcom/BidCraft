import './styles/global.css'
import './styles/workspace.css'
import './styles/boq.css'
import './styles/responsive.css'
import './styles/auth-admin.css'
import './styles/billing.css'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { router } from './App'
import { ToastProvider } from './context/ToastContext'
import { ConfirmProvider } from './context/ConfirmContext'
import { AuthProvider } from './context/AuthContext'
import { HealthProvider } from './context/HealthContext'
import { HelpProvider } from './context/HelpContext'

const container = document.getElementById('root')
if (!container) throw new Error('未找到 #root 挂载点')

createRoot(container).render(
  <ToastProvider>
    <ConfirmProvider>
      <AuthProvider>
        <HealthProvider>
          <HelpProvider>
            <RouterProvider router={router} />
          </HelpProvider>
        </HealthProvider>
      </AuthProvider>
    </ConfirmProvider>
  </ToastProvider>,
)
