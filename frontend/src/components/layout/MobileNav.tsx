import { Link, useLocation } from "react-router-dom"
import { Database, FlaskConical, LayoutDashboard, MapPinned, PlayCircle, Settings, SlidersHorizontal } from "lucide-react"
import { cn } from "@/lib/utils"

const navItems = [
  { name: "监控", href: "/", icon: LayoutDashboard },
  { name: "自动", href: "/automation", icon: PlayCircle },
  { name: "手动", href: "/manual", icon: SlidersHorizontal },
  { name: "数据", href: "/data", icon: Database },
  { name: "地图", href: "/map", icon: MapPinned },
  { name: "实验", href: "/lab", icon: FlaskConical },
  { name: "设置", href: "/settings", icon: Settings },
]

export function MobileNav() {
  const location = useLocation()

  return (
    <div className="md:hidden fixed bottom-0 left-0 right-0 border-t border-border/40 bg-background/80 backdrop-blur-xl z-50 pb-safe">
      <nav className="flex justify-around items-center h-16 px-2">
        {navItems.map((item) => {
          const Icon = item.icon
          const isActive = location.pathname === item.href

          return (
            <Link
              key={item.name}
              to={item.href}
              className={cn(
                "flex flex-col items-center justify-center gap-1 w-full h-full text-xs font-medium transition-colors",
                isActive ? "text-primary" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <div className={cn("p-1.5 rounded-full transition-colors", isActive && "bg-primary/10")}>
                <Icon className="h-5 w-5" />
              </div>
              {item.name}
            </Link>
          )
        })}
      </nav>
    </div>
  )
}
