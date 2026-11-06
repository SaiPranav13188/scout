"use client"

import { motion } from "motion/react"

export function PageHeader({ title, description, actions }: {
  title: string
  description?: string
  actions?: React.ReactNode
}) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-gradient text-2xl font-semibold tracking-tight md:text-3xl">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions}
    </motion.div>
  )
}

export function PageContainer({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto w-full max-w-5xl space-y-6 px-4 py-6 md:px-6 md:py-8">{children}</div>
}
