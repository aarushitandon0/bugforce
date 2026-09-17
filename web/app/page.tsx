import { Suspense } from "react";
import { Shell } from "@/components/Shell";
import { Landing } from "@/components/screens/Landing";

export default function Page() {
  return (
    <Shell>
      <Suspense>
        <Landing />
      </Suspense>
    </Shell>
  );
}
