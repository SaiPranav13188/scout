import type { Metadata } from "next"
import { Suspense } from "react"

import { CardSkeleton } from "@/components/today/empty-state"
import { TodayView } from "@/components/today/today-view"

export const metadata: Metadata = { title: "Today" }

export default function TodayPage() {
  return (
    // useSearchParams() inside TodayView needs a Suspense boundary.
    <Suspense fallback={<div className="mx-auto max-w-5xl space-y-3 px-6 py-8"><CardSkeleton /><CardSkeleton /></div>}>
      <TodayView />
    </Suspense>
  )
}
