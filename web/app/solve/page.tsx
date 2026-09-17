import type { Metadata } from "next";
import { Suspense } from "react";
import { Solve } from "@/components/screens/Solve";

export const metadata: Metadata = { title: "solve" };

export default function Page() {
  return (
    <Suspense>
      <Solve />
    </Suspense>
  );
}
