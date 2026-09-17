"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { apiConfigured } from "@/lib/api";
import { Cursor } from "./Cursor";

const NAV = [
  { href: "/", label: "forge", match: (p: string) => p === "/" },
  { href: "/repos/", label: "repos", match: (p: string) => p.startsWith("/repos") || p.startsWith("/repo/") },
  { href: "/gaps/", label: "gaps", match: (p: string) => p.startsWith("/gaps") },
];

export function SiteHeader() {
  const pathname = usePathname() ?? "/";
  return (
    <header className="border-b border-line">
      <div className="mx-auto flex h-11 max-w-[1120px] items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2 font-bold tracking-tight text-text">
          <Cursor className="!animate-none" />
          bugforge
        </Link>
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
      </div>
      {!apiConfigured && (
        <div className="border-t border-line bg-panel">
          <p className="mx-auto max-w-[1120px] px-6 py-1.5 text-error">
            ! NEXT_PUBLIC_API_URL was not set when this site was built. Nothing can load.
          </p>
        </div>
      )}
    </header>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader />
      <main className="mx-auto w-full max-w-[1120px] flex-1 px-6 pb-24">{children}</main>
      <footer className="border-t border-line">
        <p className="mx-auto max-w-[1120px] px-6 py-3 text-[11px] text-dim">
          mutations by AST · grading by each repo&apos;s own test suite · nothing here was written by hand
        </p>
      </footer>
    </div>
  );
}
