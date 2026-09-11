// Formatting for rupees (Indian digit grouping), dates in India time, and durations.

const IST = "Asia/Kolkata";

export function rupees(paise) {
  if (paise === null || paise === undefined) return "";
  const negative = paise < 0;
  const amount = Math.abs(Math.round(paise));
  const whole = Math.floor(amount / 100).toLocaleString("en-IN");
  const fraction = amount % 100;
  return `${negative ? "-" : ""}₹${whole}${fraction ? `.${String(fraction).padStart(2, "0")}` : ""}`;
}

export function dateTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-IN", { timeZone: IST, day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export function dateOnly(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-IN", { timeZone: IST, day: "numeric", month: "short", year: "numeric" });
}

export function dayLabel(isoDate) {
  if (!isoDate) return "";
  return new Date(`${isoDate.slice(0, 10)}T12:00:00+05:30`).toLocaleDateString("en-IN", { timeZone: IST, weekday: "short", day: "numeric", month: "short" });
}

export function sessionLabel(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-IN", { timeZone: IST, weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export function durationText(seconds) {
  const minutes = Math.round((seconds || 0) / 60);
  if (minutes < 1) return "";
  if (minutes < 60) return `${minutes} min`;
  const rest = minutes % 60;
  return rest ? `${Math.floor(minutes / 60)} h ${rest} min` : `${Math.floor(minutes / 60)} h`;
}

export function clock(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

export function bytes(n) {
  if (!n) return "0 KB";
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function relative(iso) {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} d ago`;
  return dateOnly(iso);
}

export const firstName = (user) => ((user && (user.full_name || user.email)) || "").split(/[\s@]/)[0];
