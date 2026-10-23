const NBSP = String.fromCharCode(160);
const compact = new Intl.NumberFormat('ru-RU', { notation: 'compact', maximumFractionDigits: 1 });
const decimal = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const signed = new Intl.NumberFormat('ru-RU', { signDisplay: 'exceptZero' });
const relative = new Intl.RelativeTimeFormat('ru', { numeric: 'auto', style: 'short' });
const dateTime = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
});
const plurals = new Intl.PluralRules('ru-RU');

export function formatRubles(value: number): string {
  return `${compact.format(value)}${NBSP}₽`;
}

export function formatScore(value: number): string {
  return decimal.format(value);
}

export function formatSigned(value: number): string {
  return signed.format(value).replace('-', '−');
}

export function formatAgo(iso: string, now = Date.now()): string {
  const minutes = Math.round((now - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return 'только что';
  if (minutes < 60) return relative.format(-minutes, 'minute');
  if (minutes < 24 * 60) return relative.format(-Math.round(minutes / 60), 'hour');
  return dateTime.format(new Date(iso));
}

export function formatDateTime(iso: string): string {
  return dateTime.format(new Date(iso));
}

export function plural(count: number, forms: { one: string; few: string; many: string }): string {
  const rule = plurals.select(count);
  return rule === 'one' ? forms.one : rule === 'few' ? forms.few : forms.many;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}
