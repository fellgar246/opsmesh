import { Suspense } from "react";

import { EvaluationsConsole } from "@/components/evaluations";
import { LoadingRows } from "@/components/states";

export default function EvaluationsPage() {
  return (
    <Suspense fallback={<LoadingRows />}>
      <EvaluationsConsole />
    </Suspense>
  );
}
