import { Link, useLocation } from "react-router-dom"
import { LayoutDashboard, PlayCircle, Database, Settings, Activity } from "lucide-react"
import { cn } from "@/lib/utils"

const navItems = [
  { name: "监控", href: "/", icon: LayoutDashboard },
  { name: "自动化", href: "/automation", icon: PlayCircle },
  { name: "数据中心", href: "/data", icon: Database },
  { name: "设置", href: "/settings", icon: Settings },
]

export function Sidebar() {
  const location = useLocation()

  return (
    <div className="hidden md:flex h-screen w-64 flex-col fixed left-0 top-0 border-r border-border/40 bg-background/60 backdrop-blur-xl z-50 transition-all duration-300">
      <div className="p-6 flex items-center gap-2">
        <div className="h-8 w-8 rounded-lg bg-primary flex items-center justify-center text-primary-foreground">
          <Activity className="h-5 w-5" />
        </div>
        <span className="font-semibold text-lg tracking-tight">USV 控制台</span>
      </div>
      
      <nav className="flex-1 px-4 py-4 space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon
          const isActive = location.pathname === item.href
          
          return (
            <Link
              key={item.name}
              to={item.href}
              className={cn(
                "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-200",
                isActive 
                  ? "bg-primary text-primary-foreground shadow-sm" 
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              )}
            >
              <Icon className="h-4 w-4" />
              {item.name}
            </Link>
          )
        })}
      </nav>

      <div className="p-4 border-t border-border/40">
        <div className="flex items-center gap-3 px-3 py-2">
            <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-xs text-muted-foreground">系统在线</span>
        </div>
      </div>
    </div>
  )
}
