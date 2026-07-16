import { useEffect } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Sidebar } from '@/components/layout/Sidebar'
import { MobileNav } from '@/components/layout/MobileNav'
import Monitor from '@/pages/Monitor'
import Automation from '@/pages/Automation'
import Data from '@/pages/Data'
import Manual from '@/pages/Manual'
import Settings from '@/pages/Settings'
import MapPage from '@/pages/Map'
import Lab from '@/pages/Lab'
import { useAppStore } from '@/store'
import { Toaster } from '@/components/ui/toaster'
import { ConfirmProvider } from '@/hooks/use-confirm'

function App() {
  const connect = useAppStore((state) => state.connect)
  const disconnect = useAppStore((state) => state.disconnect)

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  return (
    <BrowserRouter>
      <ConfirmProvider>
        <div className="min-h-screen bg-background text-foreground font-sans antialiased selection:bg-primary/10">
          <Sidebar />

          <main className="min-h-screen pb-[calc(5rem+env(safe-area-inset-bottom))] transition-[padding] duration-300 md:pb-0 md:pl-64">
             <Routes>
               <Route path="/" element={<Monitor />} />
               <Route path="/automation" element={<Automation />} />
               <Route path="/manual" element={<Manual />} />
               <Route path="/data" element={<Data />} />
               <Route path="/map" element={<MapPage />} />
               <Route path="/lab" element={<Lab />} />
               <Route path="/settings" element={<Settings />} />
             </Routes>
          </main>

          <MobileNav />
        </div>
        <Toaster />
      </ConfirmProvider>
    </BrowserRouter>
  )
}

export default App
