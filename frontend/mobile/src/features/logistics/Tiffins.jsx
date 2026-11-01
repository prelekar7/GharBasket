import { ScanButton, StatusPill } from "@gharbasket/ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { NATIVE } from "@/lib/env.js";
import { parseScan, scanCode } from "@/lib/scan.js";
import { ScannerUnavailableError } from "@/shared/native/scanner.js";
import { problemText, useRiderActions } from "./queries.js";

/** @type {Record<import("./queries.js").TiffinItem["status"], "planned" | "out" | "delivered" | "issue">} */
const PILL = {
  to_collect: "planned",
  on_route: "out",
  with_captain: "out",
  delivered: "delivered",
  undelivered: "issue",
  left_behind: "issue",
};

/**
 * The tiffins of a stop: at a kitchen what to collect, at a drop what to deliver (M7 §3.7). Each
 * is told by the code printed on its label; a rider-mode drop also shows the flat.
 * @param {{ items: import("./queries.js").TiffinItem[] }} props
 */
export function TiffinList({ items }) {
  const { t } = useTranslation();
  return (
    <ul className="m-0 flex list-none flex-col gap-2 p-0">
      {items.map((x) => (
        <li
          key={x.order_id}
          className="flex items-center justify-between gap-2 rounded-md bg-surface px-3.5 py-2.5"
        >
          <span className="flex min-w-0 flex-col">
            <strong className="tabular-nums">{x.short_code}</strong>
            <span className="text-[15px] leading-[22px] text-fg-secondary">
              {[x.flat_label, t("rider.tiffin.portions", { n: x.portions })]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </span>
          <StatusPill status={PILL[x.status]}>{t(`rider.tiffin.status.${x.status}`)}</StatusPill>
        </li>
      ))}
    </ul>
  );
}

/**
 * Scanning the cook's code at a kitchen stop: what is ready moves on, the rest waits for the next
 * scan (M7 §3.7). The scanner is the phone app's; the web says to scan on the app.
 * @param {{ stop: import("./queries.js").Stop, open: boolean }} props `open`: a tiffin of the stop is still to collect
 */
export function KitchenScan({ stop, open }) {
  const { t } = useTranslation();
  const actions = useRiderActions();
  const [state, setState] = useState(/** @type {"idle" | "success" | "error"} */ ("idle"));
  const [message, setMessage] = useState("");
  const [result, setResult] = useState(
    /** @type {{ queued: boolean, data: import("./queries.js").Collected | null } | null} */ (null),
  );
  const codes = (/** @type {{ short_code: string }[]} */ list) =>
    list.map((x) => x.short_code).join(", ");
  const [preparing, setPreparing] = useState(false);
  const busy = useRef(false);
  const fail = (/** @type {string} */ text) => {
    setResult(null);
    setMessage(text);
    setState("error");
    setTimeout(() => setState("idle"), 3000);
  };
  const scan = async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      // On the mock API the scanner "reads" this kitchen's code, which is its stop's id here.
      const raw = await scanCode({
        expected: `GB1A:${stop.id.replaceAll("-", "")}`,
        onPreparing: setPreparing,
      });
      if (!raw) return NATIVE ? undefined : fail(t("rider.kitchen.noScanner")); // closed: not now
      const pickup = parseScan(raw);
      if (pickup?.kind !== "pickup") return fail(t("rider.kitchen.notKitchenCode"));
      setResult(await actions.collect(stop, pickup.value));
      setState("success");
      setTimeout(() => setState("idle"), 3000);
    } catch (e) {
      fail(
        e instanceof ScannerUnavailableError
          ? t("rider.kitchen.scannerUnavailable")
          : problemText(t, e),
      );
    } finally {
      busy.current = false;
      setPreparing(false);
    }
  };
  const out = result?.data;
  return (
    <section className="flex flex-col gap-2.5">
      {(open || state !== "idle") && (
        <ScanButton
          state={state}
          label={preparing ? t("scan.preparing") : t("rider.kitchen.scan")}
          successLabel={t(result?.queued ? "rider.kitchen.queued" : "rider.kitchen.scanned")}
          errorLabel={message}
          onPress={scan}
        />
      )}
      {out && (
        <div role="status" className="flex flex-col gap-1 text-[15px] leading-[22px]">
          {out.collected.length > 0 && (
            <p className="m-0 font-semibold">
              {t("rider.kitchen.got", { codes: codes(out.collected) })}
            </p>
          )}
          {out.already.length > 0 && (
            <p className="m-0 text-fg-secondary">
              {t("rider.kitchen.already", { n: out.already.length })}
            </p>
          )}
          {out.not_ready.length > 0 && (
            <p className="m-0 font-semibold text-danger">
              {t("rider.kitchen.notReady", { codes: codes(out.not_ready) })}
            </p>
          )}
          {out.not_on_route.length > 0 && (
            <p className="m-0 font-semibold text-danger">
              {t("rider.kitchen.notOnRoute", { n: out.not_on_route.length })}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
