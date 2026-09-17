import type { Metadata } from "next";
import { Suspense } from "react";
import { Shell } from "@/components/Shell";
import { Result } from "@/components/screens/Result";

export const metadata: Metadata = { title: "result" };

export default function Page() {
  return (
    <Shell>
      <Suspense>
        <Result />
      </Suspense>
    </Shell>
  );
}
