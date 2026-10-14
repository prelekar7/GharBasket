const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** "2026-09" → "September 2026". @param {string} period */
export function monthLabel(period) {
  const [year, month] = period.split("-").map(Number);
  return `${MONTHS[month - 1]} ${year}`;
}

/**
 * The months the P&L can show, newest first: this month and the 23 before it (M12 section 3.6 allows 24). The
 * month is the one in Indian time.
 * @param {number} nowMs
 * @returns {{ value: string, label: string }[]}
 */
export function monthOptions(nowMs) {
  const ist = new Date(nowMs + 5.5 * 3_600_000);
  return Array.from({ length: 24 }, (_, back) => {
    const value = new Date(Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth() - back, 1))
      .toISOString()
      .slice(0, 7);
    return { value, label: monthLabel(value) };
  });
}

/** Whether `period` is one of the months on offer. @param {string | undefined} period @param {{ value: string }[]} options */
export const isOffered = (period, options) => options.some((o) => o.value === period);
