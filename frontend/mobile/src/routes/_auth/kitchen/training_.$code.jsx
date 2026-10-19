import { Skeleton } from "@gharbasket/ui";
import { createFileRoute, Navigate, useNavigate } from "@tanstack/react-router";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useTrainings } from "@/features/partners/queries.js";
import { TrainingQuiz } from "@/features/partners/TrainingQuiz.jsx";

/** One module's quiz for a kitchen partner, the same quiz applicants take (M8 US-3). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/kitchen/training_/$code")({
  component: KitchenQuiz,
});

function KitchenQuiz() {
  const { code } = Route.useParams();
  const navigate = useNavigate();
  const trainings = useTrainings();
  const back = () => navigate({ to: "/kitchen/training" });

  if (trainings.isPending) return <Skeleton lines={8} />;
  if (trainings.isError) return <ScreenError onRetry={() => trainings.refetch()} />;
  const { modules } = trainings.data;
  const m = modules.find((x) => x.code === code);
  // An unknown or finished module goes back to the list.
  if (!m || m.result?.passed) return <Navigate to="/kitchen/training" />;
  return (
    // The shell pads 16px; the quiz is laid out for 20px.
    <div className="flex flex-1 flex-col gap-3.5 px-1">
      <TrainingQuiz
        key={m.code}
        module={m}
        position={{ n: modules.indexOf(m) + 1, total: modules.length }}
        onClose={back}
        onPassed={back}
      />
    </div>
  );
}
