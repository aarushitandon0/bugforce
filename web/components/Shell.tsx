"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { apiConfigured } from "@/lib/api";
import { Cursor } from "./Cursor";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { href: "/", label: "forge", match: (p: string) => p === "/" },
  { href: "/repos/", label: "repos", match: (p: string) => p.startsWith("/repos") || p.startsWith("/repo/") },
  { href: "/gaps/", label: "gaps", match: (p: string) => p.startsWith("/gaps") },
];

export function SiteHeader({ wide = false }: { wide?: boolean }) {
  const pathname = usePathname() ?? "/";
  return (
    <header className="border-b border-line">
      <div className={`mx-auto flex h-11 items-center justify-between px-6 ${wide ? "max-w-[1400px]" : "max-w-[1120px]"}`}>
        <Link href="/" className="flex items-center gap-2 font-bold tracking-tight text-text">
          <Cursor className="!animate-none" />
          bugforge
        </Link>
        <div className="flex items-center gap-6">
        <nav className="flex gap-6" aria-label="primary">
          {NAV.map((item) => {
            const active = item.match(pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`border-b py-0.5 transition-colors duration-[120ms] ${
                  active ? "border-text text-text" : "border-transparent text-dim hover:text-text"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <ThemeToggle />
        </div>
      </div>
      {!apiConfigured && (
        <div className="border-t border-line bg-panel">
          <p className={`mx-auto px-6 py-1.5 text-error ${wide ? "max-w-[1400px]" : "max-w-[1120px]"}`}>
            ! NEXT_PUBLIC_API_URL was not set when this site was built. Nothing can load.
          </p>
        </div>
      )}
    </header>
  );
}

/**
 * `wide` gives the landing page room for a two-column hero; the reading pages
 * stay at 1120px, which is about as wide as a line of 13px mono should get.
 */
export function Shell({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  const width = wide ? "max-w-[1400px]" : "max-w-[1120px]";
  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader wide={wide} />
      <main className={`mx-auto w-full flex-1 px-6 ${width} ${wide ? "pb-10" : "pb-24"}`}>{children}</main>
      <footer className="border-t border-line">
        <p className={`mx-auto px-6 py-3 text-[11px] text-dim ${width}`}>
          mutations by AST · grading by each repo&apos;s own test suite · nothing here was written by hand
        </p>
      </footer>
    </div>
  );
}
