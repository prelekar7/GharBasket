import { Button, RadioCards, Sheet, toast } from "@gharbasket/ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useReportIssue } from "./queries.js";

const ISSUES = ["missing", "damaged", "late", "other"];

/**
 * "Report a problem" with a delivery: what went wrong, and the hub calls back (design HcOrders).
 * @param {{ orderId: string | undefined, open: boolean, onOpenChange: (open: boolean) => void }} props
 */
export function ProblemSheet({ orderId, open, onOpenChange }) {
  const { t } = useTranslation();
  const [issue, setIssue] = useState("missing");
  const report = useReportIssue(orderId);
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={t("orders.problem")}
      description={t("orders.problemLead")}
      footer={
        <Button
          variant="primary"
          size="lg"
          block
          isPending={report.isPending}
          onPress={() =>
            report.mutate(issue, {
              onSuccess: () => {
                onOpenChange(false);
                toast(t("orders.problemSent"));
              },
              onError: () => toast(t("orders.problemFailed")),
            })
          }
        >
          {t("orders.problemSend")}
        </Button>
      }
    >
      <RadioCards
        label={t("orders.problem")}
        value={issue}
        onChange={setIssue}
        options={ISSUES.map((value) => ({ value, title: t(`orders.issues.${value}`) }))}
      />
    </Sheet>
  );
}
