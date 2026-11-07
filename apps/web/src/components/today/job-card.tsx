"use client"

import { ArrowUpRight, Banknote, Check, Clock, Globe2, MapPin, PanelRightOpen, X } from "lucide-react"
import { motion, useMotionValue, useTransform } from "motion/react"
import { forwardRef, useRef } from "react"

import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { FAMILY_LABEL, postedLabel, SOURCE_LABEL, ycShort } from "@/lib/format"
import type { Job } from "@/lib/types"
import { cn } from "@/lib/utils"

import { CompanyMark, FitMeter, FresherBadge, TrustBadge } from "./badges"
import { LabelButtons } from "./label-buttons"
import { MatchRing } from "./match-ring"

interface Props {
  job: Job
  isNew: boolean
  active: boolean
  onOpen: () => void
  /** Called after the "Open job" link is clicked (the link itself opens the page). */
  onOpened: () => void
  onSkip: () => void
  onApplied: () => void
  onDetails: () => void
  onHover: () => void
}

const SWIPE = 110

export const JobCard = forwardRef<HTMLElement, Props>(function JobCard(
  { job, isNew, active, onOpen, onOpened, onSkip, onApplied, onDetails, onHover },
  ref,
) {
  const local = useRef<HTMLElement | null>(null)
  const x = useMotionValue(0)
  const skipOpacity = useTransform(x, [-SWIPE, -30], [1, 0])
  const openOpacity = useTransform(x, [30, SWIPE], [0, 1])
  const rotate = useTransform(x, [-200, 200], [-4, 4])

  const onMove = (e: React.MouseEvent) => {
    const el = local.current
    if (!el) return
    const r = el.getBoundingClientRect()
    el.style.setProperty("--mx", `${e.clientX - r.left}px`)
    el.style.setProperty("--my", `${e.clientY - r.top}px`)
  }

  const place = [job.location, ...(job.locations ?? []).slice(1, 3)].filter(Boolean).join(" · ") || "Location not stated"

  return (
    <motion.div layout className="relative" exit={{ opacity: 0, height: 0, marginBottom: 0, transition: { duration: 0.25 } }}>
      {/* Swipe affordances (mobile) */}
      <motion.div style={{ opacity: skipOpacity }} className="pointer-events-none absolute inset-0 flex items-center justify-end rounded-2xl bg-danger-soft pr-6 text-danger">
        <X className="size-6" />
      </motion.div>
      <motion.div style={{ opacity: openOpacity }} className="pointer-events-none absolute inset-0 flex items-center rounded-2xl bg-success-soft pl-6 text-success">
        <ArrowUpRight className="size-6" />
      </motion.div>

      <motion.article
        ref={(el) => {
          local.current = el
          if (typeof ref === "function") ref(el)
          else if (ref) ref.current = el
        }}
        data-active={active}
        onMouseMove={onMove}
        onMouseEnter={onHover}
        style={{ x, rotate }}
        drag="x"
        dragSnapToOrigin
        dragElastic={0.6}
        dragConstraints={{ left: 0, right: 0 }}
        onDragEnd={(_, info) => {
          if (info.offset.x < -SWIPE) onSkip()
          else if (info.offset.x > SWIPE) onOpen()
        }}
        initial={{ opacity: 0, y: 16, scale: 0.985 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        whileHover={{ y: -2 }}
        transition={{ type: "spring", stiffness: 380, damping: 30 }}
        className={cn(
          "spotlight group relative cursor-default touch-pan-y rounded-2xl border bg-card/70 p-4 backdrop-blur-sm transition-[border-color,box-shadow] sm:p-5",
          "hover:shadow-[0_8px_40px_-12px_var(--glow)]",
          active && "border-brand/40 shadow-[0_8px_40px_-12px_var(--glow)]",
        )}
      >
        <div className="flex gap-4">
          <CompanyMark name={job.company.name} />

          <div className="min-w-0 flex-1">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <button onClick={onDetails} className="text-left">
                  <h3 className="line-clamp-2 text-[15px] leading-snug font-semibold tracking-tight transition-colors group-hover:text-brand">
                    {job.title}
                  </h3>
                </button>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
                  <span className="text-foreground/85">{job.company.name}</span>
                  {job.company.in_yc_directory && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="rounded bg-[#f26522]/15 px-1.5 text-[10px] font-semibold text-[#f26522]">
                          YC{job.company.yc_batch ? ` ${ycShort(job.company.yc_batch)}` : ""}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>Y Combinator company{job.company.yc_batch ? ` · ${job.company.yc_batch}` : ""}</TooltipContent>
                    </Tooltip>
                  )}
                  {job.company.funding_stage && <span className="text-xs">{job.company.funding_stage} stage</span>}
                </p>
              </div>
              {isNew && (
                <span className="flex items-center gap-1.5 rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-medium text-brand">
                  <span className="size-1.5 animate-pulse rounded-full bg-brand" /> New
                </span>
              )}
            </div>

            <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                {job.location_bucket === "remote_global" ? <Globe2 className="size-3.5" /> : <MapPin className="size-3.5" />}
                <span className="max-w-[260px] truncate">{place}</span>
              </span>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="flex cursor-help items-center gap-1">
                    <Clock className="size-3.5" />
                    {postedLabel(job).short}
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">{postedLabel(job).tooltip}</TooltipContent>
              </Tooltip>
              {job.salary_text && (
                <span className="flex items-center gap-1 text-foreground/80"><Banknote className="size-3.5" />{job.salary_text}</span>
              )}
              <span>via {SOURCE_LABEL[job.source] ?? job.source}</span>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <FresherBadge job={job} />
              <TrustBadge trust={job.trust} />
              {job.role_family !== "other" && (
                <span className="inline-flex h-6 items-center rounded-md bg-secondary px-2 text-[11px] font-medium text-secondary-foreground">
                  {FAMILY_LABEL[job.role_family]}
                </span>
              )}
              {job.match?.missing_skills.slice(0, 3).map((s) => (
                <span key={`miss-${s}`} className="inline-flex h-6 items-center rounded-md border border-dashed border-warning/40 px-2 text-[11px] text-warning">
                  missing: {s}
                </span>
              ))}
              {job.relevance_matched.slice(0, 4).map((s) => (
                <span key={s} className="inline-flex h-6 items-center rounded-md border border-dashed px-2 text-[11px] text-muted-foreground">
                  {s}
                </span>
              ))}
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Button size="sm" asChild
                className="group/open h-8 bg-brand px-3 text-brand-foreground shadow-[0_4px_20px_-6px_var(--glow)] hover:bg-brand/90">
                {/* A real link: browsers never block it as a pop-up. */}
                <a href={job.apply_url} target="_blank" rel="noopener noreferrer" onClick={onOpened}
                  onPointerDownCapture={(e) => e.stopPropagation()}>
                  Open job
                  <ArrowUpRight className="transition-transform group-hover/open:translate-x-0.5 group-hover/open:-translate-y-0.5" />
                </a>
              </Button>
              <Button size="sm" variant="outline" onClick={onApplied} className="h-8">
                <Check /> Mark applied
              </Button>
              <Button size="sm" variant="ghost" onClick={onSkip} className="h-8 text-muted-foreground">
                <X /> Skip
              </Button>
              <Button size="sm" variant="ghost" onClick={onDetails} className="h-8 text-muted-foreground">
                <PanelRightOpen /> Details
              </Button>
              <div className="ml-auto flex items-center gap-3">
                <LabelButtons jobId={job.id} value={job.my_label} />
                {active && (
                  <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="hidden items-center gap-1 text-[11px] text-muted-foreground lg:flex">
                    <Kbd>o</Kbd> open <Kbd>a</Kbd> applied <Kbd>s</Kbd> skip
                  </motion.span>
                )}
                {job.match ? <MatchRing match={job.match} /> : <FitMeter score={job.relevance_score} />}
              </div>
            </div>
          </div>
        </div>
      </motion.article>
    </motion.div>
  )
})
