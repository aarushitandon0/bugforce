import type { Metadata } from "next";
import { Shell } from "@/components/Shell";
import { Profile } from "@/components/screens/Profile";

export const metadata: Metadata = { title: "profile" };

export default function Page() {
  return (
    <Shell>
      <Profile />
    </Shell>
  );
}
