import type { Metadata, Viewport } from "next";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/700.css";
import "./globals.css";
import { THEME_BOOT } from "@/lib/theme-boot";

export const metadata: Metadata = {
  title: { default: "BugForge", template: "%s · BugForge" },
  description:
    "Every repo is a debugging gym. Real mutations of real open-source repos, real stack traces, graded by the repo's own test suite.",
};

export const viewport: Viewport = {
  themeColor: "#0A0B0D",
  colorScheme: "dark light",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark">
      <head>
        {/* before first paint, so a light viewer never sees a dark flash */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className="min-h-dvh">{children}</body>
    </html>
  );
}
