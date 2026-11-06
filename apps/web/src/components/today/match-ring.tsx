"use client"

import { animate, motion, useMotionValue, useReducedMotion, useTransform } from "motion/react"
import { useEffect } from "react"

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { Match } from "@/lib/types"
import { cn } from "@/lib/utils"

export const BAND = {
  top: { label: "Top match", color: "var(--success)" },
  good: { label: "Good match", color: "var(--brand)" },
  stretch: { label: "Stretch", color: "var(--warning)" },
  low: { label: "Weak match", color: "var(--muted-foreground)" },
} as const

/** Animated 0-100 ring for the resume match score. */
export function MatchRing({ match, size = 44 }: { match: Match; size?: number }) {
  const reduce = useReducedMotion()
  const r = size / 2 - 4
  const c = 2 * Math.PI * r
  const mv = useMotionValue(0)
  const dash = useTransform(mv, (v) => `${(v / 100) * c} ${c}`)
  const label = useTransform(mv, (v) => Math.round(v).toString())
  useEffect(() => {
    if (reduce) { mv.set(match.score); return }
    const ctl = animate(mv, match.score, { duration: 1.1, ease: [0.16, 1, 0.3, 1] })
    return () => ctl.stop()
  }, [match.score, mv, reduce])
  const band = BAND[match.band]
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="relative grid shrink-0 cursor-help place-items-center" style={{ width: size, height: size }}
          aria-label={`Match score ${match.score} out of 100`}>
          <svg width={size} height={size} className="-rotate-90">
            <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--muted)" strokeWidth={4} />
            <motion.circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={band.color} strokeWidth={4}
              strokeLinecap="round" style={{ strokeDasharray: dash }} />
          </svg>
          <motion.span className="absolute text-[13px] font-semibold tabular-nums">{label}</motion.span>
        </div>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs p-3">
        <p className="font-medium" style={{ color: band.color }}>{band.label} · {match.score}/100</p>
        <ul className="mt-1.5 space-y-1 text-xs opacity-90">
          {match.reasons.slice(0, 3).map((r, i) => <li key={i}>• {r}</li>)}
        </ul>
        {match.info === "title_only" && (
          <p className="mt-2 text-xs opacity-70">Based on the title only: the alert email had no job description.</p>
        )}
      </TooltipContent>
    </Tooltip>
  )
}

export function BreakdownBar({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs"><span>{label}</span><span className="tabular-nums text-muted-foreground">{value}</span></div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted" title={hint}>
        <motion.div className={cn("h-full rounded-full bg-brand")} initial={{ width: 0 }} animate={{ width: `${value}%` }}
          transition={{ type: "spring", stiffness: 120, damping: 20 }} />
      </div>
    </div>
  )
}
