import Link from "next/link";
import { Shell } from "@/components/Shell";

export default function NotFound() {
  return (
    <Shell>
      <div className="pt-20">
        <p className="text-error">✗ 404</p>
        <h1 className="mt-2 text-[28px] font-bold text-text">nothing at this path.</h1>
        <p className="mt-4">
          <Link href="/" className="link">
            back to the forge →
          </Link>
        </p>
      </div>
    </Shell>
  );
}
