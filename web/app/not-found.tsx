import { Shell } from "@/components/Shell";
import { ArrowLink } from "@/components/ui/ArrowLink";

export default function NotFound() {
  return (
    <Shell>
      <div className="pt-16">
        <p className="t-small text-gap">&#10007; 404</p>
        <h1 className="t-h1 mt-2 text-text">nothing at this path.</h1>
        <ArrowLink href="/" className="mt-4" tone="text-accent">
          back to the forge
        </ArrowLink>
      </div>
    </Shell>
  );
}
