import { PageSkeleton } from "@/components/franchise/PageSkeleton.jsx";

export default function Loading() {
  return <PageSkeleton tiles={5} blocks={[300]} />;
}
