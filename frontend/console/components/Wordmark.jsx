/**
 * The GharBasket wordmark: "घर" in Mukta Bold, "Basket" in Fraunces, and "Console" beside or under it.
 * @param {{ size?: "md" | "lg", stacked?: boolean }} props
 */
export function Wordmark({ size = "md", stacked = false }) {
  return (
    <span className={stacked ? "flex flex-col" : "flex items-baseline gap-2"}>
      <span className={size === "lg" ? "text-[26px] leading-8" : "text-[22px] leading-7"}>
        <strong>घर</strong>
        <span className="font-display font-semibold">Basket</span>
      </span>
      <span className={size === "lg" ? "text-base text-fg-secondary" : "text-sm text-fg-secondary"}>
        Console
      </span>
    </span>
  );
}
