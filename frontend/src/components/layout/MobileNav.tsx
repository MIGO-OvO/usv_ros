import { Link, useLocation } from "react-router-dom"
import { LayoutDashboard, PlayCircle, Database, Settings } from "lucide-react"
import { cn } from "@/lib/utils"

const navItems = [
  { name: "Monitor", href: "/", icon: LayoutDashboard },
  { name: "Auto", href: "/automation", icon: PlayCircle },
  { name: "Data", href: "/data", icon: Database },
  { name: "Settings", href: "/settings", icon: Settings },
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
                "flex flex-col items-center justify-center gap-1 w-full h-full text-[10px] font-medium transition-colors",
                isActive 
                  ? "text-primary" 
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <div className={cn(
                  "p-1.5 rounded-full transition-all",
                  isActive && "bg-primary/10"
              )}>
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
