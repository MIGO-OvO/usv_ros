import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Download, Trash2, RefreshCw } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

export default function Data() {
  const [data, setData] = useState<any[]>([])
  
  const fetchData = async () => {
    try {
        const res = await fetch('/api/data/voltage')
        const json = await res.json()
        if (json.success) {
            setData(json.data)
        }
    } catch (e) {
        console.error(e)
    }
  }

  const clearData = async () => {
      if (!confirm("Clear all data?")) return
      await fetch('/api/data/voltage/clear', { method: 'POST' })
      fetchData()
  }

  const exportData = () => {
      const csvContent = "data:text/csv;charset=utf-8," 
          + "Timestamp,Voltage\n"
          + data.map(row => `${row.timestamp},${row.voltage}`).join("\n")
      
      const encodedUri = encodeURI(csvContent)
      const link = document.createElement("a")
      link.setAttribute("href", encodedUri)
      link.setAttribute("download", "spectrometer_data.csv")
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
  }

  useEffect(() => {
      fetchData()
      const interval = setInterval(fetchData, 5000) // Poll every 5s
      return () => clearInterval(interval)
  }, [])

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-7xl mx-auto pb-32">
      <header className="flex items-center justify-between">
        <div>
            <h1 className="text-3xl font-bold tracking-tight">Data Center</h1>
            <p className="text-muted-foreground">Historical spectrometer voltage data.</p>
        </div>
        <div className="flex gap-2">
            <Button variant="outline" onClick={fetchData}><RefreshCw className="w-4 h-4 mr-2" /> Refresh</Button>
            <Button variant="outline" onClick={exportData}><Download className="w-4 h-4 mr-2" /> Export CSV</Button>
            <Button variant="destructive" onClick={clearData}><Trash2 className="w-4 h-4 mr-2" /> Clear</Button>
        </div>
      </header>

      <Card>
          <CardHeader>
              <CardTitle>Voltage History</CardTitle>
          </CardHeader>
          <CardContent className="h-[500px]">
             <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                    <XAxis dataKey="timestamp" tickFormatter={(t) => new Date(t).toLocaleTimeString()} />
                    <YAxis domain={[0, 5]} />
                    <Tooltip labelFormatter={(t) => new Date(t).toLocaleString()} />
                    <Line type="monotone" dataKey="voltage" stroke="#8884d8" dot={false} strokeWidth={2} />
                </LineChart>
             </ResponsiveContainer>
          </CardContent>
      </Card>
      
      <div className="border rounded-lg overflow-hidden">
          <div className="max-h-96 overflow-y-auto">
              <table className="w-full text-sm">
                  <thead className="bg-muted sticky top-0">
                      <tr>
                          <th className="p-2 text-left">Timestamp</th>
                          <th className="p-2 text-left">Voltage (V)</th>
                      </tr>
                  </thead>
                  <tbody>
                      {data.slice().reverse().map((row, i) => (
                          <tr key={i} className="border-t hover:bg-muted/50">
                              <td className="p-2 font-mono">{new Date(row.timestamp).toLocaleString()}</td>
                              <td className="p-2 font-mono">{row.voltage.toFixed(4)}</td>
                          </tr>
                      ))}
                  </tbody>
              </table>
          </div>
      </div>
    </div>
  )
}
