import { CaretRightIcon, ChatCircleTextIcon } from "@phosphor-icons/react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { InfoPage } from "@/components/InfoPage.jsx";
import { useSession } from "@/lib/session.js";

/** Help: the common questions, each leading to the screen that answers it (handbook 09 §9.10). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/help")({ component: Help });

const TOPICS = [
  { key: "late", to: "/orders" },
  { key: "change", to: "/week" },
  { key: "money", to: "/wallet/statement" },
  { key: "data", to: "/legal/privacy" },
  { key: "ask", to: "/assistant" },
];

function Help() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // Help also opens before sign-in (login, legal links); the assistant only works for a customer.
  const customer = useSession((s) => s.signedIn && s.activeRole === "customer");
  return (
    <InfoPage title={t("help.title")}>
      <p className="text-fg-secondary">{t("help.lead")}</p>
      <ul className="flex flex-col gap-2.5">
        {TOPICS.map(({ key, to }) => (
          <li key={key}>
            <Link
              to={to}
              className="flex min-h-16 items-center gap-3 rounded-md bg-surface px-3.5 py-3 outline-none focus-visible:shadow-focus"
            >
              <span className="flex grow flex-col gap-0.5">
                <span className="font-semibold">{t(`help.topics.${key}.q`)}</span>
                <span className="text-sm leading-5 text-fg-secondary">
                  {t(`help.topics.${key}.a`)}
                </span>
              </span>
              <CaretRightIcon aria-hidden size={20} className="shrink-0 text-fg-muted" />
            </Link>
          </li>
        ))}
      </ul>
      {customer && (
        <button
          type="button"
          aria-label={t("assistant")}
          onClick={() => navigate({ to: "/assistant" })}
          className="fixed right-4 bottom-[calc(24px+env(safe-area-inset-bottom))] grid size-14 place-items-center rounded-full bg-primary text-on-primary shadow-sheet outline-none focus-visible:shadow-focus"
        >
          <ChatCircleTextIcon size={26} />
        </button>
      )}
    </InfoPage>
  );
}
