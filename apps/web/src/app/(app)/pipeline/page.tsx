import type { Metadata } from "next"

import { ComingSoon } from "@/components/coming-soon"

export const metadata: Metadata = { title: "Pipeline" }

export default function PipelinePage() {
  return (
    <ComingSoon title="Pipeline" phase={5} points={[
      "Drag-and-drop board: Applied → Interview → Offer / Rejected.",
      "Notes and follow-up dates on every application.",
      "Follow-up reminders on Telegram and in the evening summary.",
      "Jobs you mark applied today already appear here once the board is built.",
    ]} />
  )
}
