import { createFileRoute, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { InfoPage } from "@/components/InfoPage.jsx";

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
  return (
    <InfoPage title={t("help.title")}>
      <p className="text-fg-secondary">{t("help.lead")}</p>
      <ul>
        {TOPICS.map(({ key, to }) => (
          <li key={key}>
            <Link
              to={to}
              className="flex min-h-16 flex-col justify-center border-b border-line py-3 outline-none focus-visible:shadow-focus"
            >
              <span className="font-semibold">{t(`help.topics.${key}.q`)}</span>
              <span className="text-sm leading-5 text-fg-secondary">
                {t(`help.topics.${key}.a`)}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </InfoPage>
  );
}
