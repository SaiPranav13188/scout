import type { Metadata } from "next"

import { ComingSoon } from "@/components/coming-soon"

export const metadata: Metadata = { title: "Resume" }

export default function ResumePage() {
  return (
    <ComingSoon title="Resume Manager" phase={3} points={[
      "Upload your master resume (PDF or DOCX); it's parsed into editable sections.",
      "You review and correct the parsed data once.",
      "Tailored resumes per job, using only facts from your master resume (Phase 4).",
      "Version history of every tailored resume, with the validator's report.",
    ]} />
  )
}
