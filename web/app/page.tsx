import { Suspense } from "react";
import { Shell } from "@/components/Shell";
import { Landing } from "@/components/screens/Landing";

export default function Page() {
  return (
    <Shell wide>
      <Suspense>
        <Landing />
      </Suspense>
    </Shell>
  );
}
