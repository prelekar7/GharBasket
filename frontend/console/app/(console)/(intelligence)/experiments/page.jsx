import { NotAvailable } from "@/components/admin/Page.jsx";

export const metadata = { title: "Experiments" };

/** Experiments are cut (M12): no call is made and the page is out of the nav. */
export default function Page() {
  return (
    <NotAvailable title="Experiments">
      Experiments are not part of this version. Model health is under AI health.
    </NotAvailable>
  );
}
