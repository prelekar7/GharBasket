import { Button, Chip, QtyStepper, SegmentedControl, Skeleton, toast } from "@gharbasket/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { dayLabel } from "@/features/kitchen/format.js";
import { useCapacity, useKitchenWrite, usePartnerMe } from "@/features/kitchen/queries.js";
import { Muted, Row } from "@/features/kitchen/ui.jsx";
import { request } from "@/lib/api.js";

/** Menu and capacity: the most tiffins per day and meal, and what I cook well (design KitchenMenu; M8 §3.4). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/kitchen/menu")({ component: KitchenMenu });

/** @typedef {import("@/features/kitchen/queries.js").Capacity} Capacity */

function KitchenMenu() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const me = usePartnerMe();
  const capacity = useCapacity();
  const write = useKitchenWrite();
  const client = useQueryClient();
  const [meal, setMeal] = useState("lunch");
  const [edits, setEdits] = useState(/** @type {Record<string, number>} */ ({}));
  const [skills, setSkills] = useState(/** @type {string[] | null} */ (null));

  if (me.isPending || capacity.isPending) return <Skeleton lines={10} />;
  if (me.isError || capacity.isError) {
    return (
      <ScreenError
        onRetry={() => {
          me.refetch();
          capacity.refetch();
        }}
      />
    );
  }
  const data = capacity.data;
  const keyOf = (/** @type {{ date: string, meal: string }} */ d) => `${d.date}|${d.meal}`;
  const chosen = skills ?? data.skills;
  const changed = data.days.filter((d) => keyOf(d) in edits && edits[keyOf(d)] !== d.max_portions);

  const save = async () => {
    // One PUT per date and meal, queued in order; each patches the screen at once.
    for (const d of changed) {
      const max = edits[keyOf(d)];
      write({
        name: "capacity",
        method: "PUT",
        path: "/v1/kitchen-capacity",
        body: { date: d.date, meal: d.meal, max_portions: max },
        update: (/** @type {Capacity} */ c) => ({
          ...c,
          days: c.days.map((x) => (keyOf(x) === keyOf(d) ? { ...x, max_portions: max } : x)),
        }),
      });
    }
    setEdits({});
    // Skills are the kitchen's own record (PATCH with If-Match), saved when there is signal.
    if (skills && skills.join() !== data.skills.join()) {
      try {
        await request("/v1/kitchens/me", {
          method: "PATCH",
          body: { skills },
          headers: { "if-match": `"${data.version}"` },
        });
        await client.invalidateQueries({ queryKey: ["kitchen", "capacity"] });
        setSkills(null);
      } catch {
        toast(t("kitchen.menu.skillsFailed"));
        return;
      }
    }
    toast(t("kitchen.menu.saved"));
  };

  return (
    <>
      <FieldHeader
        eyebrow={t("kitchen.whoHub", { name: me.data.display_name, hub: me.data.hub_name })}
        title={t("kitchen.menu.title")}
      />
      <div>
        <SegmentedControl
          label={t("kitchen.menu.meal")}
          value={meal}
          onChange={setMeal}
          options={["lunch", "dinner"].map((value) => ({
            value,
            label: t("kitchen.meal", { meal: value }),
          }))}
        />
      </div>
      <section className="flex flex-col">
        <h2 className="text-[19px] leading-[26px] font-bold">{t("kitchen.menu.capTitle")}</h2>
        <Muted>{t("kitchen.menu.capHelp")}</Muted>
        <ul>
          {data.days
            .filter((d) => d.meal === meal)
            .map((d) => {
              const value = edits[keyOf(d)] ?? d.max_portions;
              const day = dayLabel(d.date, lang);
              return (
                <Row
                  key={keyOf(d)}
                  tall
                  title={day}
                  sub={
                    d.locked
                      ? d.assigned > 0
                        ? t("kitchen.menu.locked", { n: d.assigned })
                        : t("kitchen.menu.closed")
                      : value === 0
                        ? t("kitchen.menu.dayOff")
                        : undefined
                  }
                  end={
                    <QtyStepper
                      label={day}
                      value={value}
                      // A day with tiffins already assigned can't change (the stepper is disabled).
                      min={d.locked ? value : 0}
                      max={d.locked ? value : data.max_portions_limit}
                      onChange={(v) => setEdits((e) => ({ ...e, [keyOf(d)]: v }))}
                      labels={{
                        decrease: t("kitchen.menu.decrease"),
                        increase: t("kitchen.menu.increase"),
                      }}
                    />
                  }
                />
              );
            })}
        </ul>
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="text-[19px] leading-[26px] font-bold">{t("kitchen.menu.skillsTitle")}</h2>
        <div className="flex flex-wrap gap-2">
          {data.skill_options.map((code) => (
            <Chip
              key={code}
              isSelected={chosen.includes(code)}
              onChange={(on) =>
                setSkills(on ? [...chosen, code] : chosen.filter((c) => c !== code))
              }
            >
              {t(`kitchen.skills.${code}`, { defaultValue: code })}
            </Chip>
          ))}
        </div>
      </section>
      <div className="mt-auto pt-2">
        <Button variant="primary" size="lg" block onPress={save}>
          {t("kitchen.menu.save")}
        </Button>
      </div>
    </>
  );
}
