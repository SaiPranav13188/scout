"use client"

import { motion } from "motion/react"

import { PageContainer, PageHeader } from "@/components/page-header"

/** Honest placeholder: says which phase builds the page and what it will do. No fake data. */
export function ComingSoon({ title, phase, points }: { title: string; phase: number; points: string[] }) {
  return (
    <PageContainer>
      <PageHeader title={title} description={`Built in Phase ${phase}. Here's what it will do:`} />
      <motion.ul initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.07 } } }}
        className="grid gap-3 sm:grid-cols-2">
        {points.map((p, i) => (
          <motion.li key={p} variants={{ hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0 } }}
            className="flex gap-3 rounded-xl border border-dashed bg-card/40 p-4 text-sm">
            <span className="grid size-6 shrink-0 place-items-center rounded-full bg-brand-soft text-xs font-semibold text-brand">{i + 1}</span>
            <span className="text-muted-foreground">{p}</span>
          </motion.li>
        ))}
      </motion.ul>
    </PageContainer>
  )
}
