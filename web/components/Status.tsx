import { ArrowLeft } from "lucide-react";
import { ButtonLink } from "./ui/Button";
import { Cursor } from "./Cursor";

export function Loading({ text }: { text: string }) {
  return (
    <p className="py-6 text-muted" role="status">
      {text}… <Cursor className="!h-[0.95em] !w-[0.5em]" />
    </p>
  );
}

export function ErrorLine({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <p className="py-6 text-gap" role="alert">
      ✗ {message}
      {onRetry && (
        <button type="button" onClick={onRetry} className="link ml-4 text-text">
          retry
        </button>
      )}
    </p>
  );
}

export function PageHeader({
  eyebrow,
  title,
  back,
  children,
}: {
  eyebrow?: string;
  title: React.ReactNode;
  /** the way up one level, e.g. a repo page back to the repo list */
  back?: { href: string; label: string };
  children?: React.ReactNode;
}) {
  return (
    <header className="pt-16 pb-8">
      {back && (
        <ButtonLink variant="secondary" size="sm" href={back.href} className="mb-6">
          <ArrowLeft size={14} strokeWidth={1.5} aria-hidden />
          {back.label}
        </ButtonLink>
      )}
      {eyebrow && <p className="label mb-3">{eyebrow}</p>}
      <h1 className="t-h1 text-text">{title}</h1>
      {children && <div className="mt-3 max-w-[72ch] text-muted">{children}</div>}
    </header>
  );
}
