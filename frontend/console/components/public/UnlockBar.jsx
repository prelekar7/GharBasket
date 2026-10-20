import { ShareButton } from "./ShareButton.jsx";

/**
 * Server-rendered twin of SocietyUnlockBar from @gharbasket/ui (which is a client component on
 * React Aria): one square per home, filled for homes that joined, with the progress also as words.
 * Only the share button hydrates.
 * @param {{ society: string, joined: number, target: number, labels: { count: string, remaining: string, progress: string, invite: string, copied: string, unlocked: string, started: string } }} props
 * `count` takes {joined} and {target}; `remaining` takes {n}; `started` is the finished sentence for a live society.
 */
export function UnlockBar({ society, joined, target, labels }) {
  const done = joined >= target;
  const count = labels.count
    .replace("{joined}", String(joined))
    .replace("{target}", String(target));
  const filled = Math.min(joined, target);
  return (
    <section className="flex w-full flex-col items-start gap-3 rounded-md bg-surface p-4">
      <div className="flex w-full items-baseline justify-between gap-3">
        <h2 className="text-lg leading-[26px] font-semibold">{society}</h2>
        {done ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-line bg-bg px-2 py-0.5 text-xs leading-[18px] font-semibold">
            <span aria-hidden className="size-2 rounded-full bg-primary" />
            {labels.unlocked}
          </span>
        ) : (
          <span className="text-sm font-semibold whitespace-nowrap tabular-nums">{count}</span>
        )}
      </div>
      <div
        role="progressbar"
        aria-label={labels.progress}
        aria-valuemin={0}
        aria-valuemax={target}
        aria-valuenow={filled}
        aria-valuetext={count}
        className="grid w-full grid-cols-10 gap-1"
      >
        {Array.from({ length: target }, (_, i) => (
          <span
            // biome-ignore lint/suspicious/noArrayIndexKey: squares are positional and never reorder
            key={i}
            className={`h-[18px] rounded-[3px] ${i < filled ? "bg-primary" : "shadow-[inset_0_0_0_1.5px_var(--color-line-strong)]"}`}
          />
        ))}
      </div>
      <p className="text-sm leading-[22px] text-fg-secondary">
        {done ? labels.started : labels.remaining.replace("{n}", String(target - joined))}
      </p>
      {!done && (
        <ShareButton
          title={society}
          label={labels.invite}
          copiedLabel={labels.copied}
          className="inline-flex min-h-12 items-center justify-center gap-2 rounded-sm border border-line-strong bg-surface px-4 text-sm leading-5 font-semibold outline-none focus-visible:shadow-focus"
        />
      )}
    </section>
  );
}
