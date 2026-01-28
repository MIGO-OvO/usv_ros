import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Save, RefreshCw, Zap } from 'lucide-react'

export default function Settings() {
  const [config, setConfig] = useState({
      Kp: 0.14, Ki: 0.015, Kd: 0.06,
      output_min: 1.0, output_max: 8.0
  })

  useEffect(() => {
    fetchConfig()
  }, [])

  const fetchConfig = async () => {
      try {
          const res = await fetch('/api/pid/config')
          const json = await res.json()
          if (json.success) {
              setConfig(json.data)
          }
      } catch (e) {
          console.error(e)
      }
  }

  const saveConfig = async () => {
      try {
          await fetch('/api/pid/config', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(config)
          })
          alert("PID Parameters updated")
      } catch (e) {
          console.error(e)
      }
  }

  const handleChange = (key: string, value: string) => {
      setConfig(prev => ({ ...prev, [key]: parseFloat(value) }))
  }

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-7xl mx-auto">
      <header>
          <h1 className="text-3xl font-bold tracking-tight">System Settings</h1>
          <p className="text-muted-foreground">Low-level control parameters.</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Zap className="w-5 h-5 text-yellow-500" />
                    PID Parameters
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="grid grid-cols-3 gap-4">
                    <div className="space-y-2">
                        <Label>Kp (Proportional)</Label>
                        <Input type="number" step="0.001" value={config.Kp} onChange={(e) => handleChange('Kp', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>Ki (Integral)</Label>
                        <Input type="number" step="0.001" value={config.Ki} onChange={(e) => handleChange('Ki', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>Kd (Derivative)</Label>
                        <Input type="number" step="0.001" value={config.Kd} onChange={(e) => handleChange('Kd', e.target.value)} />
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                     <div className="space-y-2">
                        <Label>Output Min (V)</Label>
                        <Input type="number" step="0.1" value={config.output_min} onChange={(e) => handleChange('output_min', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>Output Max (V)</Label>
                        <Input type="number" step="0.1" value={config.output_max} onChange={(e) => handleChange('output_max', e.target.value)} />
                    </div>
                </div>

                <div className="flex gap-2 pt-4">
                    <Button className="flex-1" onClick={saveConfig}><Save className="w-4 h-4 mr-2" /> Save Parameters</Button>
                    <Button variant="outline" onClick={fetchConfig}><RefreshCw className="w-4 h-4" /></Button>
                </div>
            </CardContent>
        </Card>

        <Card>
            <CardHeader>
                <CardTitle>Zero Calibration</CardTitle>
            </CardHeader>
            <CardContent>
                <div className="p-4 rounded-lg bg-muted text-sm text-muted-foreground">
                    Calibration functionality coming soon.
                </div>
            </CardContent>
        </Card>
      </div>
    </div>
  )
}
