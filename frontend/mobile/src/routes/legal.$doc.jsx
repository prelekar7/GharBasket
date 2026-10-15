import { createFileRoute, notFound } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { InfoPage } from "@/components/InfoPage.jsx";

/** Terms and the Privacy notice, linked from the phone and consent screens (Hf02, Hf12; DPDP). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/legal/$doc")({
  beforeLoad: (/** @type {{ params: { doc: string } }} */ { params }) => {
    if (params.doc !== "terms" && params.doc !== "privacy") throw notFound();
  },
  component: Legal,
});

function Legal() {
  const { t } = useTranslation();
  const { doc } = Route.useParams();
  const sections = /** @type {{ h: string, p: string }[]} */ (
    t(`legal.${doc}.sections`, { returnObjects: true })
  );
  return (
    <InfoPage title={t(`legal.${doc}.title`)}>
      <p role="note" className="rounded-md border border-accent bg-accent-soft px-3 py-2 text-sm">
        {t("legal.draft")}
      </p>
      {sections.map((s) => (
        <section key={s.h} className="flex flex-col gap-1">
          <h2 className="text-lg leading-[26px] font-semibold">{s.h}</h2>
          <p className="leading-[26px] text-fg-secondary">{s.p}</p>
        </section>
      ))}
    </InfoPage>
  );
}
