import type { Metadata } from "next";
import { Shell } from "@/components/Shell";
import { Repos } from "@/components/screens/Repos";

export const metadata: Metadata = { title: "repos" };

export default function Page() {
  return (
    <Shell>
      <Repos />
    </Shell>
  );
}
