import { ViewTransition } from "react"

import { AuthGate } from "@/components/auth-gate"
import { AppShell } from "@/components/shell/app-shell"

// Navigation into the dashboard may wait for the client-side auth check.
export const instant = false

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <AuthGate>
      <AppShell>
        <ViewTransition>{children}</ViewTransition>
      </AppShell>
    </AuthGate>
  )
}
