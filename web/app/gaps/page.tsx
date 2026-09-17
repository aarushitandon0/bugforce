import type { Metadata } from "next";
import { Suspense } from "react";
import { Shell } from "@/components/Shell";
import { Gaps } from "@/components/screens/Gaps";

export const metadata: Metadata = { title: "test gaps" };

export default function Page() {
  return (
    <Shell>
      <Suspense>
        <Gaps />
      </Suspense>
    </Shell>
  );
}
