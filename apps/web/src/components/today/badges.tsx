"use client"

import { AlertTriangle, BadgeCheck, CircleHelp, GraduationCap, ShieldAlert, ShieldQuestion } from "lucide-react"
import { motion } from "motion/react"

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { hueFor, initials, TRUST_META } from "@/lib/format"
import type { Job } from "@/lib/types"
import { cn } from "@/lib/utils"

const pill = "inline-flex h-6 items-center gap-1 rounded-md px-2 text-[11px] font-medium ring-1 ring-inset"

export function CompanyMark({ name, size = "md" }: { name: string; size?: "md" | "lg" }) {
  const h = hueFor(name)
  return (
    <div
      className={cn("grid shrink-0 place-items-center rounded-xl font-semibold text-white shadow-inner ring-1 ring-white/10",
        size === "lg" ? "size-14 text-lg" : "size-11 text-sm")}
      style={{ background: `linear-gradient(135deg, oklch(0.62 0.15 ${h}), oklch(0.45 0.13 ${(h + 40) % 360}))` }}
      aria-hidden
    >
      {initials(name) || "?"}
    </div>
  )
}

export function TrustBadge({ trust }: { trust: Job["trust"] }) {
  const meta = TRUST_META[trust.level]
  const Icon = { verified: BadgeCheck, unverified: ShieldQuestion, caution: AlertTriangle, scam_risk: ShieldAlert }[trust.level]
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn(pill, meta.tone, "cursor-help")}>
          <Icon className="size-3" /> {meta.label}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs p-3">
        <p className="font-medium">{meta.hint}</p>
        {trust.signals.length > 0 && (
          <ul className="mt-2 space-y-1">
            {trust.signals.map((s, i) => (
              <li key={i} className="flex gap-1.5 text-xs opacity-90">
                <span className={s.weight > 0 ? "text-success" : "text-danger"}>{s.weight > 0 ? "+" : "−"}</span>
                {s.detail}
              </li>
            ))}
          </ul>
        )}
      </TooltipContent>
    </Tooltip>
  )
}

const FRESHER = {
  high: { label: "Fresher fit", tone: "text-success bg-success-soft ring-success/20" },
  medium: { label: "Likely entry-level", tone: "text-info bg-info-soft ring-info/20" },
  low: { label: "Experience unclear", tone: "text-warning bg-warning-soft ring-warning/20" },
} as const

export function FresherBadge({ job }: { job: Job }) {
  const level = job.fresher_confidence ?? "low"
  const meta = FRESHER[level]
  const Icon = level === "low" ? CircleHelp : GraduationCap
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn(pill, meta.tone, "cursor-help")}>
          <Icon className="size-3" /> {meta.label}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        {job.fresher_reasons.length ? job.fresher_reasons.join(" · ") : "No experience requirement found in the posting."}
      </TooltipContent>
    </Tooltip>
  )
}

/** Plain-code role relevance (Phase 1). Not the Gemini match score, which arrives in Phase 3. */
export function FitMeter({ score }: { score: number }) {
  const tone = score >= 75 ? "bg-success" : score >= 55 ? "bg-brand" : score >= 35 ? "bg-info" : "bg-muted-foreground"
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="flex cursor-help items-center gap-2">
          <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
            <motion.div
              className={cn("h-full rounded-full", tone)}
              initial={{ width: 0 }}
              animate={{ width: `${score}%` }}
              transition={{ type: "spring", stiffness: 120, damping: 20, delay: 0.15 }}
            />
          </div>
          <span className="text-[11px] tabular-nums text-muted-foreground">{score}</span>
        </div>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        Role fit from the title and your target skills (plain rules). The resume match score arrives in Phase 3.
      </TooltipContent>
    </Tooltip>
  )
}
