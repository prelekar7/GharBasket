import { PageSkeleton } from "@/components/admin/Page.jsx";

export default function Loading() {
  return <PageSkeleton blocks={["h-12 w-80 max-w-full", "h-64 w-full"]} />;
}
