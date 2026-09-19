"use client";

import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

/**
 * The only three button treatments in the app.
 *
 * Before this there were nine, none of them filled, which is why no page ever
 * committed to a primary action. `primary` is a filled accent and there should
 * be at most one per screen; `secondary` is the bordered default; `ghost` is
 * text that happens to be clickable.
 *
 * Hover is a background shift and nothing else -- no transform, no lift, no
 * scale. Focus is the global 2px accent ring at 2px offset, so nothing here
 * sets `outline-none`.
 */

export type ButtonVariant = "primary" | "secondary" | "ghost";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-fg font-bold hover:bg-accent-hover disabled:bg-accent/50",
  secondary: "border border-line text-text hover:bg-surface-2 disabled:text-muted",
  ghost: "text-text hover:underline underline-offset-4 disabled:text-muted disabled:no-underline",
};

/* Heights are fixed so a row of mixed variants lines up on both edges. The
   ghost variant carries no box, so it takes padding but not a height. */
const SIZE: Record<ButtonSize, string> = {
  lg: "h-13 px-5 text-[15px]",
  md: "h-10 px-4 text-[13px]",
  sm: "h-8 px-3 text-[13px]",
};

const GHOST_SIZE: Record<ButtonSize, string> = {
  lg: "text-[15px]",
  md: "text-[13px]",
  sm: "text-[13px]",
};

export function buttonClass(variant: ButtonVariant, size: ButtonSize, className = ""): string {
  const box =
    variant === "ghost"
      ? `inline-flex items-center gap-2 ${GHOST_SIZE[size]}`
      : `inline-flex items-center justify-center gap-2 whitespace-nowrap rounded ${SIZE[size]}`;
  return `${box} ${VARIANT[variant]} transition-colors duration-[120ms] disabled:cursor-not-allowed ${className}`;
}

type ButtonProps = Omit<ComponentProps<"button">, "children"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** ghost only: the trailing arrow that marks a forward move */
  arrow?: boolean;
  children: ReactNode;
};

export function Button({
  variant = "secondary",
  size = "md",
  arrow = false,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button type="button" {...rest} className={buttonClass(variant, size, className)}>
      {children}
      {arrow && <span aria-hidden>&rarr;</span>}
    </button>
  );
}

type ButtonLinkProps = Omit<ComponentProps<typeof Link>, "children"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  arrow?: boolean;
  children: ReactNode;
};

/** The same three treatments, on a link. */
export function ButtonLink({
  variant = "secondary",
  size = "md",
  arrow = false,
  className = "",
  children,
  ...rest
}: ButtonLinkProps) {
  return (
    <Link {...rest} className={buttonClass(variant, size, className)}>
      {children}
      {arrow && <span aria-hidden>&rarr;</span>}
    </Link>
  );
}
