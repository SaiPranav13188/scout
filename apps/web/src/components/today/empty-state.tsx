"use client"

import { motion } from "motion/react"

export function RadarEmpty({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) {
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      className="flex flex-col items-center rounded-2xl border border-dashed bg-card/30 px-6 py-16 text-center">
      <div className="relative size-28">
        {[0, 1, 2].map((i) => (
          <motion.span key={i} className="absolute inset-0 rounded-full border border-brand/30"
            initial={{ scale: 0.3, opacity: 0.8 }} animate={{ scale: 1.15, opacity: 0 }}
            transition={{ duration: 3, repeat: Infinity, delay: i, ease: "easeOut" }} />
        ))}
        <div className="absolute inset-4 rounded-full border border-border" />
        <div className="absolute inset-9 rounded-full border border-border" />
        <motion.div className="absolute inset-0 origin-center rounded-full"
          style={{ background: "conic-gradient(from 0deg, transparent 0deg, color-mix(in oklch, var(--brand) 45%, transparent) 50deg, transparent 60deg)" }}
          animate={{ rotate: 360 }} transition={{ duration: 3.2, repeat: Infinity, ease: "linear" }} />
        <span className="absolute top-1/2 left-1/2 size-2 -translate-1/2 rounded-full bg-brand shadow-[0_0_12px_var(--brand)]" />
      </div>
      <h3 className="mt-6 text-base font-semibold">{title}</h3>
      <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">{body}</p>
      {action && <div className="mt-5">{action}</div>}
    </motion.div>
  )
}

export function CardSkeleton() {
  return (
    <div className="rounded-2xl border bg-card/50 p-5">
      <div className="flex gap-4">
        <div className="skeleton-shimmer size-11 rounded-xl" />
        <div className="flex-1 space-y-2.5">
          <div className="skeleton-shimmer h-4 w-2/3 rounded" />
          <div className="skeleton-shimmer h-3 w-1/3 rounded" />
          <div className="flex gap-2 pt-1">
            <div className="skeleton-shimmer h-6 w-24 rounded-md" />
            <div className="skeleton-shimmer h-6 w-20 rounded-md" />
          </div>
          <div className="flex gap-2 pt-2">
            <div className="skeleton-shimmer h-8 w-24 rounded-lg" />
            <div className="skeleton-shimmer h-8 w-28 rounded-lg" />
          </div>
        </div>
      </div>
    </div>
  )
}
