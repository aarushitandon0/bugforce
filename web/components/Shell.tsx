"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { apiConfigured } from "@/lib/api";
import { Cursor } from "./Cursor";
import { SignIn } from "./SignIn";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { href: "/", label: "forge", match: (p: string) => p === "/" },
  { href: "/repos/", label: "repos", match: (p: string) => p.startsWith("/repos") || p.startsWith("/repo/") },
  { href: "/gaps/", label: "gaps", match: (p: string) => p.startsWith("/gaps") },
  { href: "/profile/", label: "profile", match: (p: string) => p.startsWith("/profile") },
];

export function SiteHeader({
  wide = false,
  compact = false,
  children,
}: {
  wide?: boolean;
  /** the solve screen: the same nav in a 36px bar, with room for breadcrumbs */
  compact?: boolean;
  children?: React.ReactNode;
}) {
  const pathname = usePathname() ?? "/";
  const width = wide ? "max-w-[1400px]" : "max-w-[1120px]";
  return (
    <header className={`border-b border-line ${compact ? "bg-surface-2" : ""}`}>
      {/*
       * One nav, every route. The solve screen used to substitute a back link
       * for the whole header, which meant sign-in state -- the thing that
       * decides whether a submit will be accepted -- vanished on the one
       * screen where you submit. Composition is fixed; only the box changes.
       */}
      <div
        className={
          compact
            ? "flex h-9 items-center gap-4 px-3 t-small"
            : `mx-auto flex h-12 items-center gap-6 px-5 lg:px-page ${width}`
        }
      >
        <Link href="/" className="flex shrink-0 items-center gap-2 font-bold tracking-tight text-text">
          <Cursor className="!animate-none" />
          bugforge
        </Link>
        {!compact && <span className="flex-1" />}
        <nav className={`flex shrink-0 gap-6 ${compact ? "gap-4" : ""}`} aria-label="primary">
          {NAV.map((item) => {
            const active = item.match(pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`border-b py-0.5 transition-colors duration-[120ms] ${
                  active ? "border-text text-text" : "border-transparent text-muted hover:text-text"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        {compact && <div className="flex min-w-0 flex-1 items-center gap-3">{children}</div>}
        <div className="flex shrink-0 items-center gap-6">
          <SignIn />
          <ThemeToggle />
        </div>
      </div>
      {!apiConfigured && (
        <div className="border-t border-line bg-surface-2">
          <p className={compact ? "px-3 py-2 t-small text-gap" : `mx-auto px-5 py-2 t-small text-gap lg:px-page ${width}`}>
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
      <main className={`mx-auto w-full flex-1 px-5 lg:px-page ${width} ${wide ? "pb-10" : "pb-20"}`}>{children}</main>
      <footer className="border-t border-line">
        <p className={`mx-auto px-5 py-4 t-small text-muted lg:px-page ${width}`}>
          bugs by AST · grading by each repo&apos;s own test suite · nothing here was written by hand
        </p>
      </footer>
    </div>
  );
}
