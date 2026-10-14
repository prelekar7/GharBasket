import { PageSkeleton } from "@/components/PageSkeleton.jsx";

export default function Loading() {
  return <PageSkeleton kpis={4} panels={1} label="Loading the packing board" />;
}
