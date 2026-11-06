import type { Metadata, Viewport } from "next"
import { Geist, Geist_Mono } from "next/font/google"

import { Providers } from "@/components/providers"
import "./globals.css"

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] })
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] })

export const metadata: Metadata = {
  title: { default: "Scout", template: "%s · Scout" },
  description: "Personal job hunter: fresher and entry-level roles, filtered and ranked.",
  robots: { index: false, follow: false },
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#141419" },
    { media: "(prefers-color-scheme: light)", color: "#fbfbfd" },
  ],
}

// Runs before first paint so the saved theme never flashes. Dark is the default.
const themeScript = `try{var t=localStorage.getItem("scout-theme");if(t==="light"){document.documentElement.classList.remove("dark")}}catch(e){}`

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} dark h-full antialiased`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="grain min-h-full">
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
