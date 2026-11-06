import {
  Activity,
  Bookmark,
  Building2,
  FileText,
  KanbanSquare,
  Settings,
  Sparkles,
  type LucideIcon,
} from "lucide-react"

export interface NavItem {
  href: string
  label: string
  icon: LucideIcon
  shortcut: string
  /** Phase in which the page becomes functional; shown honestly until then. */
  phase?: number
}

export const NAV: NavItem[] = [
  { href: "/", label: "Today", icon: Sparkles, shortcut: "G T" },
  { href: "/pipeline", label: "Pipeline", icon: KanbanSquare, shortcut: "G P", phase: 5 },
  { href: "/startups", label: "Startups", icon: Building2, shortcut: "G C" },
  { href: "/resume", label: "Resume", icon: FileText, shortcut: "G R", phase: 3 },
  { href: "/saved-searches", label: "Saved searches", icon: Bookmark, shortcut: "G S" },
  { href: "/health", label: "Agent health", icon: Activity, shortcut: "G H" },
  { href: "/settings", label: "Settings", icon: Settings, shortcut: "G ," },
]
