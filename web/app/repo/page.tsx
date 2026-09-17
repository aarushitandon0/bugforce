import type { Metadata } from "next";
import { Suspense } from "react";
import { Shell } from "@/components/Shell";
import { Course } from "@/components/screens/Course";

export const metadata: Metadata = { title: "course" };

export default function Page() {
  return (
    <Shell>
      <Suspense>
        <Course />
      </Suspense>
    </Shell>
  );
}
