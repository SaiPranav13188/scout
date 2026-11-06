"use client"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MotionConfig } from "motion/react"
import { useState } from "react"
import { Toaster } from "sonner"

import { TooltipProvider } from "@/components/ui/tooltip"
import { ApiError } from "@/lib/api"

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: true,
            retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
          },
        },
      }),
  )
  return (
    <QueryClientProvider client={client}>
      {/* reducedMotion="user": every Motion animation respects the OS "reduce motion" setting */}
      <MotionConfig reducedMotion="user" transition={{ type: "spring", stiffness: 380, damping: 32, mass: 0.8 }}>
        <TooltipProvider delayDuration={250}>
          {children}
          <Toaster
            position="bottom-center"
            theme="dark"
            toastOptions={{
              classNames: {
                toast: "!bg-popover/95 !backdrop-blur-xl !border-border !text-foreground !rounded-xl !shadow-2xl",
                description: "!text-muted-foreground",
                actionButton: "!bg-brand !text-brand-foreground !rounded-md !font-medium",
              },
            }}
          />
        </TooltipProvider>
      </MotionConfig>
    </QueryClientProvider>
  )
}
