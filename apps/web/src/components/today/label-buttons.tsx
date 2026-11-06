"use client"

import { HelpCircle, ThumbsDown, ThumbsUp } from "lucide-react"
import { motion } from "motion/react"

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useSetLabel } from "@/lib/queries"
import type { JobLabel } from "@/lib/types"
import { cn } from "@/lib/utils"

const OPTIONS: { value: JobLabel; label: string; icon: typeof ThumbsUp; tone: string }[] = [
  { value: "apply", label: "I'd apply", icon: ThumbsUp, tone: "text-success" },
  { value: "maybe", label: "Maybe", icon: HelpCircle, tone: "text-warning" },
  { value: "no", label: "Not for me", icon: ThumbsDown, tone: "text-danger" },
]

/** My apply / maybe / no call on a job. These labels train and test the match score (Phase 3). */
export function LabelButtons({ jobId, value, size = "sm" }: { jobId: string; value: JobLabel | null; size?: "sm" | "md" }) {
  const setLabel = useSetLabel()
  return (
    <div className="flex items-center rounded-lg border bg-card/40 p-0.5" role="group" aria-label="Your call on this job">
      {OPTIONS.map((o) => {
        const active = value === o.value
        return (
          <Tooltip key={o.value}>
            <TooltipTrigger asChild>
              <button
                onClick={() => setLabel.mutate({ id: jobId, label: active ? null : o.value })}
                aria-pressed={active}
                aria-label={o.label}
                className={cn("relative grid place-items-center rounded-md transition-colors",
                  size === "md" ? "h-8 px-3" : "size-7",
                  active ? o.tone : "text-muted-foreground hover:text-foreground")}
              >
                {active && (
                  <motion.span layoutId={`label-${jobId}`} className="absolute inset-0 rounded-md bg-accent"
                    transition={{ type: "spring", stiffness: 500, damping: 35 }} />
                )}
                <span className="relative flex items-center gap-1.5">
                  <o.icon className="size-3.5" />
                  {size === "md" && <span className="text-xs font-medium">{o.label}</span>}
                </span>
              </button>
            </TooltipTrigger>
            <TooltipContent>{active ? "Clear my label" : `${o.label}: teaches Scout what fits you`}</TooltipContent>
          </Tooltip>
        )
      })}
    </div>
  )
}
