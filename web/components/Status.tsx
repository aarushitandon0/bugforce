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
  children,
}: {
  eyebrow?: string;
  title: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <header className="pt-16 pb-8">
      {eyebrow && <p className="label mb-3">{eyebrow}</p>}
      <h1 className="t-h1 text-text">{title}</h1>
      {children && <div className="mt-3 max-w-[72ch] text-muted">{children}</div>}
    </header>
  );
}
