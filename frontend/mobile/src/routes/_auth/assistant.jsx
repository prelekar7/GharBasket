import { Button, Chip, IconButton, MoneyText } from "@gharbasket/ui";
import { ArrowLeftIcon, ArrowRightIcon, CheckIcon } from "@phosphor-icons/react";
import { createFileRoute, redirect, useRouter } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BrandMark } from "@/components/BrandMark.jsx";
import { RoleFrame } from "@/components/RoleFrame.jsx";
import { canRetry, isStale, useAssistant } from "@/features/assistant/chat.js";
import { shortDate, weekdayName } from "@/features/subscriptions/format.js";
import { HOME_BY_ROLE, useSession } from "@/lib/session.js";

/** Ghar Saathi: ask in your language; any change comes back as a card you confirm (design HcAssistant, M13, W12). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/assistant")({
  // Customer only (M13 §7): other roles go to their own home, like the customer shell does.
  beforeLoad: () => {
    const role = useSession.getState().activeRole;
    if (role && role !== "customer") throw redirect({ to: HOME_BY_ROLE[role] });
  },
  component: Assistant,
});

const SUGGESTIONS = ["lighter", "track"];

function Assistant() {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const role = useSession((s) => s.activeRole) ?? "customer";
  const { messages, send, ask, retry, askAgain, confirm, reject } = useAssistant();
  const [text, setText] = useState("");
  const end = useRef(/** @type {HTMLDivElement | null} */ (null));

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll whenever the conversation grows
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages.length, send.isPending]);

  const day = (/** @type {string} */ iso) => `${weekdayName(iso, lang)} ${shortDate(iso, lang)}`;
  const submit = (/** @type {string} */ value) => {
    const v = value.trim();
    if (!v || send.isPending) return;
    ask(v);
    setText("");
  };

  /** The error of a confirm or turn-down that failed for this very card, not for another one. */
  const failed = (/** @type {string} */ id) => {
    if (confirm.isError && confirm.variables?.id === id)
      return /** @type {{ code?: string, status?: number }} */ (confirm.error);
    if (reject.isError && reject.variables?.id === id)
      return /** @type {{ code?: string, status?: number }} */ (reject.error);
    return null;
  };

  return (
    <RoleFrame userRole={role}>
      <header className="flex items-center gap-2 border-b border-line py-2 pr-5 pl-2">
        <IconButton
          label={t("onboarding.back")}
          variant="quiet"
          onPress={() => router.history.back()}
        >
          <ArrowLeftIcon size={24} />
        </IconButton>
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-surface p-1 text-fg">
          <BrandMark className="size-full" />
        </span>
        <div className="flex flex-col">
          <h1 className="text-[22px] leading-7 font-semibold">{t("ghar.title")}</h1>
          <span className="text-sm leading-5 text-fg-secondary">{t("ghar.sub")}</span>
        </div>
      </header>

      <main aria-live="polite" className="flex flex-1 flex-col gap-3 overflow-y-auto px-5 py-4">
        <Bubble from="bot">{t("ghar.hello")}</Bubble>
        {messages.length === 0 && (
          <div className="flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <Chip key={s} onChange={() => submit(t(`ghar.suggest.${s}`))}>
                {t(`ghar.suggest.${s}`)}
              </Chip>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={m.id} className="contents">
            <Bubble from={m.from}>
              {m.error
                ? t(`ghar.errors.${m.error.replace(/\./g, "_")}`, {
                    defaultValue: t("ghar.errors.other"),
                  })
                : m.text}
            </Bubble>
            {m.error && i === messages.length - 1 && canRetry(m.error) && (
              <div>
                <Button variant="secondary" isPending={send.isPending} onPress={retry}>
                  {t("ghar.retry")}
                </Button>
              </div>
            )}
            {m.proposal && (
              <section
                aria-label={t("ghar.confirmTitle", { day: day(m.proposal.box.delivery_date) })}
                className="flex flex-col gap-1.5 rounded-md border-2 border-line-strong bg-bg px-4 py-3.5"
              >
                <strong>
                  {t("ghar.confirmTitle", { day: day(m.proposal.box.delivery_date) })}
                </strong>
                <ul>
                  {m.proposal.lines.map((l) => (
                    <li
                      key={l.kind === "meal" ? `${l.date}-${l.slot}` : l.name}
                      className="flex items-baseline justify-between gap-3 border-b border-line py-1.5 text-sm leading-5"
                    >
                      <span>
                        {l.kind === "meal"
                          ? t("ghar.meal", {
                              day: weekdayName(l.date, lang),
                              slot: t(`ghar.slot.${l.slot}`),
                              dish: l.dish,
                            })
                          : t(`ghar.line.${l.kind}`, { item: l.name, qty: l.qty_label })}
                      </span>
                      {l.delta_paise ? (
                        <MoneyText paise={l.delta_paise} delta />
                      ) : (
                        <span className="text-fg-secondary">{t("ghar.noPrice")}</span>
                      )}
                    </li>
                  ))}
                  <li className="flex items-baseline justify-between gap-3 py-1.5">
                    <strong>{t("ghar.total")}</strong>
                    <MoneyText paise={m.proposal.new_total_paise} />
                  </li>
                </ul>
                {m.proposal.status === "pending" ? (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="primary"
                      isPending={confirm.isPending && confirm.variables?.id === m.proposal.id}
                      isDisabled={
                        reject.isPending ||
                        (confirm.isPending && confirm.variables?.id !== m.proposal.id)
                      }
                      onPress={() => m.proposal && confirm.mutate(m.proposal)}
                    >
                      <CheckIcon aria-hidden size={20} weight="bold" />
                      {t("ghar.yes")}
                    </Button>
                    <Button
                      variant="secondary"
                      isDisabled={confirm.isPending}
                      onPress={() => m.proposal && reject.mutate(m.proposal)}
                    >
                      {t("ghar.no")}
                    </Button>
                  </div>
                ) : (
                  <p
                    role="status"
                    className={`text-sm font-semibold ${m.proposal.status === "confirmed" ? "text-primary" : "text-fg-secondary"}`}
                  >
                    {t(`ghar.status.${m.proposal.status}`, {
                      day: day(m.proposal.box.delivery_date),
                    })}
                  </p>
                )}
                {m.proposal.status === "pending" && failed(m.proposal.id) && (
                  <div className="flex flex-col items-start gap-2">
                    <p role="alert" className="text-sm font-semibold text-danger">
                      {t(
                        `ghar.errors.${failed(m.proposal.id)?.code?.replace(/\./g, "_") ?? "other"}`,
                        {
                          defaultValue: t("ghar.errors.other"),
                        },
                      )}
                    </p>
                    {isStale(failed(m.proposal.id)) && (
                      <Button
                        variant="secondary"
                        onPress={() => m.proposal && askAgain(m.proposal)}
                      >
                        {t("ghar.askAgain")}
                      </Button>
                    )}
                  </div>
                )}
              </section>
            )}
          </div>
        ))}
        {send.isPending && <Bubble from="bot">{t("ghar.thinking")}</Bubble>}
        {messages.some((m) => m.proposal) && (
          <p className="text-sm text-fg-secondary">{t("ghar.safe")}</p>
        )}
        <div ref={end} />
      </main>

      <form
        className="sticky bottom-0 flex items-center gap-2 border-t border-line bg-bg px-4 pt-2.5 pb-[calc(18px+env(safe-area-inset-bottom))]"
        onSubmit={(e) => {
          e.preventDefault();
          submit(text);
        }}
      >
        <label htmlFor="ghar-msg" className="sr-only">
          {t("ghar.placeholder")}
        </label>
        <input
          id="ghar-msg"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={t("ghar.placeholder")}
          autoComplete="off"
          enterKeyHint="send"
          className="min-h-12 min-w-0 flex-1 rounded-full border border-line-strong bg-surface px-4 text-fg outline-none placeholder:text-fg-muted focus-visible:shadow-focus"
        />
        <IconButton
          type="submit"
          label={t("ghar.send")}
          variant="primary"
          isDisabled={!text.trim() || send.isPending}
        >
          <ArrowRightIcon size={22} weight="bold" />
        </IconButton>
      </form>
    </RoleFrame>
  );
}

/** One chat bubble: yours on the right in ink, Ghar Saathi's on the left on kraft. @param {{ from: "me" | "bot", children: import("react").ReactNode }} props */
function Bubble({ from, children }) {
  return from === "me" ? (
    <p className="max-w-[82%] self-end rounded-[16px_16px_4px_16px] bg-fg px-3.5 py-2.5 text-bg">
      {children}
    </p>
  ) : (
    <p className="max-w-[86%] self-start rounded-[16px_16px_16px_4px] bg-surface px-3.5 py-2.5">
      {children}
    </p>
  );
}
