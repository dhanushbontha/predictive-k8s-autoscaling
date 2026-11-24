/**
 * Timezone utilities for Predictive Kubernetes Autoscaling.
 * Internal timestamps and backend payloads are stored and communicated in UTC.
 * The display layer converts all timestamps to Asia/Kolkata (IST, UTC+5:30).
 */

export const TIMEZONE = 'Asia/Kolkata';
export const TIMEZONE_LABEL = 'IST';

/**
 * Parses any timestamp representation into a valid JavaScript Date object in UTC.
 * Handles:
 * - numeric epoch in seconds (e.g. 1764000000) or milliseconds
 * - ISO string with explicit timezone (e.g. "2026-11-24T13:28:45+00:00" or "2026-11-24T13:28:45Z")
 * - naive ISO string (e.g. "2026-11-24 13:28:45" or "2026-11-24T13:28:45") -> safely treated as UTC
 */
export function parseUtcTimestamp(ts) {
  if (ts === null || ts === undefined || ts === '') {
    return null;
  }

  if (ts instanceof Date) {
    return isNaN(ts.getTime()) ? null : ts;
  }

  // Numeric epoch
  if (typeof ts === 'number') {
    if (ts <= 0 || isNaN(ts)) return null;
    return new Date(ts < 1e11 ? ts * 1000 : ts);
  }

  if (typeof ts === 'string') {
    const trimmed = ts.trim();
    if (!trimmed) return null;

    // Numeric string (e.g. "1764000000")
    const num = Number(trimmed);
    if (!isNaN(num) && /^\d+(\.\d+)?$/.test(trimmed)) {
      return new Date(num < 1e11 ? num * 1000 : num);
    }

    // Replace space with T
    let iso = trimmed.replace(' ', 'T');
    // If no timezone offset is present, treat as UTC by appending 'Z'
    if (!iso.endsWith('Z') && !/[+-]\d{2}:?\d{2}$/.test(iso)) {
      iso += 'Z';
    }
    const d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }

  return null;
}

/**
 * Formats a timestamp into an IST time string (HH:mm:ss IST).
 */
export function formatToIstTime(ts, options = {}) {
  const d = parseUtcTimestamp(ts);
  if (!d) return '--';

  const defaultOptions = {
    timeZone: TIMEZONE,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    ...options,
  };

  try {
    return new Intl.DateTimeFormat('en-IN', defaultOptions).format(d);
  } catch (e) {
    return d.toLocaleTimeString([], defaultOptions);
  }
}

/**
 * Formats a timestamp into an IST date and time string (DD/MM/YYYY, HH:mm:ss IST).
 */
export function formatToIstDateTime(ts, options = {}) {
  const d = parseUtcTimestamp(ts);
  if (!d) return '--';

  const defaultOptions = {
    timeZone: TIMEZONE,
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    ...options,
  };

  try {
    return `${new Intl.DateTimeFormat('en-IN', defaultOptions).format(d)} ${TIMEZONE_LABEL}`;
  } catch (e) {
    return `${d.toLocaleString([], defaultOptions)} ${TIMEZONE_LABEL}`;
  }
}

/**
 * Formats chart X-axis labels in IST (HH:mm:ss).
 */
export function formatToIstChartTime(ts) {
  return formatToIstTime(ts, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

/**
 * Formats chart tooltip labels in IST with date and IST badge.
 */
export function formatToIstTooltipTime(ts) {
  return formatToIstDateTime(ts);
}

/**
 * Calculates freshness in seconds using numeric Unix timestamps.
 * Does not rely on formatted strings.
 */
export function calculateFreshnessSeconds(epochTimestamp) {
  if (epochTimestamp === null || epochTimestamp === undefined) {
    return null;
  }
  const epoch = typeof epochTimestamp === 'number' ? epochTimestamp : parseFloat(epochTimestamp);
  if (isNaN(epoch) || epoch <= 0) {
    return null;
  }
  const nowEpoch = Date.now() / 1000;
  return Math.max(0, Math.round(nowEpoch - epoch));
}
