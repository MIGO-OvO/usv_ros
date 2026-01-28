import { useEffect } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Sidebar } from '@/components/layout/Sidebar'
import { MobileNav } from '@/components/layout/MobileNav'
import Monitor from '@/pages/Monitor'
import Automation from '@/pages/Automation'
import Data from '@/pages/Data'
import Settings from '@/pages/Settings'
import { useAppStore } from '@/store'

function App() {
  const connect = useAppStore((state) => state.connect)
  const disconnect = useAppStore((state) => state.disconnect)

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-background text-foreground font-sans antialiased selection:bg-primary/10">
        <Sidebar />
        
        <main className="md:pl-64 min-h-screen pb-20 md:pb-0 transition-all duration-300">
           <Routes>
             <Route path="/" element={<Monitor />} />
             <Route path="/automation" element={<Automation />} />
             <Route path="/data" element={<Data />} />
             <Route path="/settings" element={<Settings />} />
           </Routes>
        </main>
        
        <MobileNav />
      </div>
    </BrowserRouter>
  )
}

export default App
