import { createFileRoute } from "@tanstack/react-router";
import { FieldShell } from "@/components/FieldShell.jsx";

// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/packer")({
  component: () => <FieldShell userRole="packer" />,
});
