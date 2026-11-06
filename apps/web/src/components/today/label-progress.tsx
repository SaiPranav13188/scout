"use client"

import { Target } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"

import { useLabelSummary } from "@/lib/queries"

/** Progress toward the labelled set the match score is tested against (Phase 3). */
export function LabelProgress() {
  const { data } = useLabelSummary()
  if (!data) return null
  const done = data.total >= data.target
  const pct = Math.min(100, Math.round((data.total / data.target) * 100))
  return (
    <AnimatePresence>
      <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
        className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border bg-card/50 px-4 py-3 text-sm">
        <Target className="size-4 text-brand" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {done ? "Thanks! Enough jobs labelled to test the match score." : `Label ${data.target - data.total} more jobs to train your match score`}
          </p>
          <p className="text-xs text-muted-foreground">
            Use 👍 / 🤔 / 👎 on any job. So far: {data.apply} apply · {data.maybe} maybe · {data.no} no
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-28 overflow-hidden rounded-full bg-muted">
            <motion.div className="h-full rounded-full bg-brand" initial={{ width: 0 }} animate={{ width: `${pct}%` }}
              transition={{ type: "spring", stiffness: 120, damping: 20 }} />
          </div>
          <span className="text-xs tabular-nums text-muted-foreground">{data.total}/{data.target}</span>
        </div>
      </motion.div>
    </AnimatePresence>
  )
}
