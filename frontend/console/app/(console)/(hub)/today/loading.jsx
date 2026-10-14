import { PageSkeleton } from "@/components/PageSkeleton.jsx";

export default function Loading() {
  return <PageSkeleton kpis={4} panels={2} label="Loading the ops board" />;
}
