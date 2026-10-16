// Times from M4 orders: timestamps are UTC, slot times and delivery dates are India's.

/** The India calendar date ("YYYY-MM-DD") of a UTC timestamp, so a late-night event lands on the right day. @param {string} at */
export const istDate = (at) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(at));

/** "10:00" from a slot's "10:00:00" (the API sends seconds; clockTime() wants HH:MM). @param {string} time */
export const hhmm = (time) => time.slice(0, 5);
