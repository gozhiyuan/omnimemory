export type DateParts = {
  year: number;
  month: number;
  day: number;
};

const padNumber = (value: number) => `${value}`.padStart(2, '0');

export const getDateParts = (value: Date, timeZone: string): DateParts => {
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  const parts = formatter.formatToParts(value);
  const lookup: Record<string, string> = {};
  parts.forEach((part) => {
    if (part.type !== 'literal') {
      lookup[part.type] = part.value;
    }
  });
  return {
    year: Number(lookup.year),
    month: Number(lookup.month),
    day: Number(lookup.day),
  };
};

export const formatDateKey = (value: Date, timeZone: string) => {
  const { year, month, day } = getDateParts(value, timeZone);
  return `${year}-${padNumber(month)}-${padNumber(day)}`;
};

export const parseDateKey = (value: string): DateParts | null => {
  const [year, month, day] = value.split('-').map(Number);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) {
    return null;
  }
  return { year, month, day };
};

export const getTimeZoneOffsetMinutes = (value: Date, timeZone: string) => {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  const parts = formatter.formatToParts(value);
  const lookup: Record<string, string> = {};
  parts.forEach((part) => {
    if (part.type !== 'literal') {
      lookup[part.type] = part.value;
    }
  });
  const asUTC = Date.UTC(
    Number(lookup.year),
    Number(lookup.month) - 1,
    Number(lookup.day),
    Number(lookup.hour),
    Number(lookup.minute),
    Number(lookup.second)
  );
  return Math.round((value.getTime() - asUTC) / 60000);
};

export const buildZonedDate = (
  year: number,
  month: number,
  day: number,
  timeZone: string,
  hour = 12,
  minute = 0
) => {
  const baseUtc = Date.UTC(year, month - 1, day, hour, minute, 0);
  const offsetMinutes = getTimeZoneOffsetMinutes(new Date(baseUtc), timeZone);
  return new Date(baseUtc + offsetMinutes * 60000);
};

export const toZonedDate = (value: Date, timeZone: string) => {
  const { year, month, day } = getDateParts(value, timeZone);
  return buildZonedDate(year, month, day, timeZone);
};

export const dateKeyToDate = (value: string, timeZone: string) => {
  const parts = parseDateKey(value);
  if (!parts) {
    return null;
  }
  return buildZonedDate(parts.year, parts.month, parts.day, timeZone);
};

export const addDaysZoned = (value: Date, days: number, timeZone: string) => {
  const { year, month, day } = getDateParts(value, timeZone);
  return buildZonedDate(year, month, day + days, timeZone);
};
