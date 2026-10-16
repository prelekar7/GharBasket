import { PageSkeleton } from "@/components/admin/Page.jsx";

export default function Loading() {
  return <PageSkeleton blocks={["h-12 w-96 max-w-full", "h-80 w-full"]} />;
}
