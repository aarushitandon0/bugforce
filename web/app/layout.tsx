import type { Metadata, Viewport } from "next";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/700.css";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "BugForge", template: "%s · BugForge" },
  description:
    "Every repo is a debugging gym. Real mutations of real open-source repos, real stack traces, graded by the repo's own test suite.",
};

export const viewport: Viewport = {
  themeColor: "#0A0B0D",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-dvh">{children}</body>
    </html>
  );
}
