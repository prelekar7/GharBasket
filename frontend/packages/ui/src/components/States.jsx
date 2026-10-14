import { ArrowClockwiseIcon, WarningIcon, WifiSlashIcon } from "@phosphor-icons/react";
import { Button } from "./Button.jsx";

/**
 * Grey placeholder while content loads (never a spinner for content). Pulses slowly; still under reduced motion.
 * @param {{ className?: string, lines?: number }} props
 */
export function Skeleton({ className = "h-4 w-full", lines }) {
  const base = "block animate-pulse rounded-xs bg-line motion-reduce:animate-none";
  if (!lines) return <span aria-hidden className={`${base} ${className}`} />;
  return (
    <span aria-hidden className="flex w-full flex-col gap-2">
      {Array.from({ length: lines }, (_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: static placeholder lines never reorder
        <span key={i} className={`${base} h-3.5 ${i === lines - 1 ? "w-3/5" : "w-full"}`} />
      ))}
    </span>
  );
}

/**
 * Nothing here yet: one spot illustration, a warm title, one or two lines, at most one action.
 * @param {{ title: string, body?: string, illustration?: import("react").ReactNode, action?: string, onAction?: () => void }} props
 */
export function EmptyState({ title, body, illustration, action, onAction }) {
  return (
    <section className="mx-auto flex max-w-[320px] flex-col items-center gap-2 px-4 py-6 text-center">
      {illustration}
      <h3 className="mt-2 font-display text-[22px] leading-7 font-semibold text-balance">
        {title}
      </h3>
      {body && <p className="text-sm leading-5 text-pretty text-fg-secondary">{body}</p>}
      {action && (
        <Button variant="primary" onPress={onAction} className="mt-2">
          {action}
        </Button>
      )}
    </section>
  );
}

/**
 * Content could not load: plain words, a retry, a support line. Never a raw error code.
 * @param {{ title: string, body?: string, retryLabel: string, onRetry?: () => void, support?: import("react").ReactNode }} props
 */
export function ErrorState({ title, body, retryLabel, onRetry, support }) {
  return (
    <section
      role="alert"
      className="mx-auto flex max-w-[320px] flex-col items-center gap-2 px-4 py-6 text-center"
    >
      <span className="grid size-14 place-items-center rounded-full bg-danger-soft text-danger">
        <WarningIcon aria-hidden size={28} />
      </span>
      <h3 className="mt-2 font-display text-[22px] leading-7 font-semibold text-balance">
        {title}
      </h3>
      {body && <p className="text-sm leading-5 text-fg-secondary">{body}</p>}
      <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
        <Button variant="primary" onPress={onRetry}>
          <ArrowClockwiseIcon aria-hidden size={20} />
          {retryLabel}
        </Button>
        {support && <span className="text-sm leading-5 text-fg-secondary">{support}</span>}
      </div>
    </section>
  );
}

/**
 * Field roles work offline: shows how many actions wait to sync. Pass translated text.
 * @param {{ message: string, retryLabel?: string, onRetry?: () => void }} props
 */
export function OfflineBanner({ message, retryLabel, onRetry }) {
  return (
    <div
      role="status"
      className="flex w-full items-center gap-2 rounded-sm bg-info-soft px-3 py-2 text-sm leading-5 font-medium text-fg"
    >
      <WifiSlashIcon aria-hidden size={20} className="text-info" />
      <span className="flex-1">{message}</span>
      {retryLabel && (
        <Button variant="quiet" onPress={onRetry} className="min-h-10 px-2 text-link underline">
          {retryLabel}
        </Button>
      )}
    </div>
  );
}
