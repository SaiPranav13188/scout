"use client"

import { Briefcase, CheckCircle2, Radar, Sparkles } from "lucide-react"
import { motion } from "motion/react"

import { fadeUp, NumberTicker, stagger } from "@/components/motion"
import { useStats } from "@/lib/queries"
import { cn } from "@/lib/utils"

const TILES = [
  { key: "new_24h", label: "New in 24h", icon: Sparkles, tone: "text-brand", glow: "from-brand/20" },
  { key: "open_in_feed", label: "Waiting in feed", icon: Briefcase, tone: "text-info", glow: "from-info/20" },
  { key: "applied_24h", label: "Applied in 24h", icon: CheckCircle2, tone: "text-success", glow: "from-success/20" },
  { key: "boards_active", label: "Boards watched", icon: Radar, tone: "text-warning", glow: "from-warning/20" },
] as const

export function StatsStrip() {
  const { data, isLoading } = useStats()
  return (
    <motion.div variants={stagger(0.06)} initial="hidden" animate="show" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {TILES.map((t) => (
        <motion.div
          key={t.key}
          variants={fadeUp}
          whileHover={{ y: -2 }}
          className="group relative overflow-hidden rounded-xl border bg-card/60 p-4"
        >
          <div className={cn("pointer-events-none absolute -top-10 -right-10 size-32 rounded-full bg-gradient-to-br to-transparent opacity-60 blur-2xl transition-opacity group-hover:opacity-100", t.glow)} />
          <div className="relative flex items-center justify-between">
            <p className="text-xs text-muted-foreground">{t.label}</p>
            <t.icon className={cn("size-4", t.tone)} />
          </div>
          <div className="relative mt-3 text-2xl font-semibold tracking-tight">
            {isLoading || !data ? (
              <span className="skeleton-shimmer inline-block h-7 w-14 rounded-md" />
            ) : (
              <NumberTicker value={data[t.key] ?? 0} />
            )}
          </div>
        </motion.div>
      ))}
    </motion.div>
  )
}
