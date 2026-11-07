import type { Metadata } from "next"

import { PipelineBoard } from "@/components/pipeline/pipeline-board"

export const metadata: Metadata = { title: "Pipeline" }

export default function PipelinePage() {
  return <PipelineBoard />
}
