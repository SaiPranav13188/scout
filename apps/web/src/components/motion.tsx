"use client"

import { animate, motion, useInView, useMotionValue, useReducedMotion, useTransform } from "motion/react"
import { useEffect, useRef } from "react"

import { cn } from "@/lib/utils"

/** Counts up to `value` with a spring-like ease the first time it scrolls into view, and on change. */
export function NumberTicker({ value, className }: { value: number; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true })
  const reduce = useReducedMotion()
  const mv = useMotionValue(0)
  const rounded = useTransform(mv, (v) => Math.round(v).toLocaleString("en-IN"))

  useEffect(() => {
    if (!inView) return
    if (reduce) {
      mv.set(value)
      return
    }
    const controls = animate(mv, value, { duration: 1.1, ease: [0.16, 1, 0.3, 1] })
    return () => controls.stop()
  }, [inView, value, mv, reduce])

  return <motion.span ref={ref} className={cn("tabular-nums", className)}>{rounded}</motion.span>
}

export const fadeUp = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { type: "spring" as const, stiffness: 300, damping: 30 } },
}

export const stagger = (step = 0.05, delay = 0) => ({
  hidden: {},
  show: { transition: { staggerChildren: step, delayChildren: delay } },
})

/** Live dot with an expanding ring. */
export function PulseDot({ tone = "success", className }: { tone?: "success" | "warning" | "danger" | "brand"; className?: string }) {
  const color = { success: "bg-success", warning: "bg-warning", danger: "bg-danger", brand: "bg-brand" }[tone]
  return (
    <span className={cn("relative inline-flex size-2", className)}>
      <span className={cn("absolute inline-flex h-full w-full animate-ping-slow rounded-full opacity-70", color)} />
      <span className={cn("relative inline-flex size-2 rounded-full", color)} />
    </span>
  )
}
