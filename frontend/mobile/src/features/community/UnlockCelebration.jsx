import { Button, SocietyTowerIcon } from "@gharbasket/ui";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

const DOTS = Array.from({ length: 14 }, (_, i) => i);

/**
 * "Green Valley Heights is unlocked": shown once when a society goes live (design Core-S4). The burst is
 * CSS only and stays still for people who ask for reduced motion.
 * @param {{ society: string, homes: number, start: string, days: string, captain: string | null, onDone: () => void }} props
 */
export function UnlockCelebration({ society, homes, start, days, captain, onDone }) {
  const { t } = useTranslation();
  const heading = useRef(/** @type {HTMLHeadingElement | null} */ (null));
  useEffect(() => heading.current?.focus(), []);
  return (
    <section
      aria-labelledby="unlock-title"
      className="fixed inset-0 z-40 flex flex-col items-center justify-center gap-4 bg-bg px-6 text-center"
    >
      <div aria-hidden className="relative size-36">
        <span className="absolute inset-6 grid place-items-center rounded-full bg-accent text-on-accent motion-safe:animate-[gb-pop_600ms_ease-out]">
          <SocietyTowerIcon size={56} />
        </span>
        {DOTS.map((i) => (
          <span
            key={i}
            className={`absolute top-1/2 left-1/2 size-2.5 rounded-full ${i % 3 === 0 ? "bg-primary" : i % 3 === 1 ? "bg-accent" : "bg-danger"} motion-safe:animate-[gb-burst_900ms_ease-out_forwards]`}
            style={
              /** @type {import("react").CSSProperties} */ ({
                "--a": `${(360 / DOTS.length) * i}deg`,
                transform: "rotate(var(--a)) translateY(-64px)",
              })
            }
          />
        ))}
      </div>
      <h1
        id="unlock-title"
        ref={heading}
        tabIndex={-1}
        className="font-display text-[28px] leading-[34px] font-semibold outline-none"
      >
        {t("society.unlock.title", { society })}
      </h1>
      <p className="max-w-[320px] text-fg-secondary">
        {t("society.unlock.body", { n: homes, start })}
      </p>
      <dl className="w-full max-w-[320px] text-left">
        <div className="flex justify-between gap-3 border-b border-line py-2.5">
          <dt className="text-sm text-fg-secondary">{t("society.unlock.days")}</dt>
          <dd className="font-semibold">{days}</dd>
        </div>
        {captain && (
          <div className="flex justify-between gap-3 border-b border-line py-2.5">
            <dt className="text-sm text-fg-secondary">{t("society.unlock.captain")}</dt>
            <dd className="font-semibold">{captain}</dd>
          </div>
        )}
      </dl>
      <Button variant="primary" size="lg" block onPress={onDone}>
        {t("society.unlock.cta")}
      </Button>
    </section>
  );
}
