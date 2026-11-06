"use client"

import { useRouter } from "next/navigation"
import { useEffect, useRef, useState } from "react"

import { CommandPalette } from "./command-palette"
import { NAV } from "./nav"
import { MobileTabBar, Sidebar } from "./sidebar"
import { Topbar } from "./topbar"

export function isTyping(e: KeyboardEvent) {
  const el = e.target as HTMLElement | null
  return Boolean(el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)))
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const [paletteOpen, setPaletteOpen] = useState(false)
  const pendingG = useRef<number | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setPaletteOpen((v) => !v)
        return
      }
      if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === "/") {
        e.preventDefault()
        document.getElementById("feed-search")?.focus()
        return
      }
      // "g" then a letter jumps to a page (Linear-style).
      if (pendingG.current) {
        const target = NAV.find((n) => n.shortcut.split(" ")[1]?.toLowerCase() === e.key.toLowerCase())
        window.clearTimeout(pendingG.current)
        pendingG.current = null
        if (target) {
          e.preventDefault()
          router.push(target.href)
        }
        return
      }
      if (e.key === "g") pendingG.current = window.setTimeout(() => (pendingG.current = null), 900)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [router])

  return (
    <div className="flex min-h-dvh">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onOpenPalette={() => setPaletteOpen(true)} />
        <main className="flex-1 pb-24 md:pb-10">{children}</main>
      </div>
      <MobileTabBar />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  )
}
