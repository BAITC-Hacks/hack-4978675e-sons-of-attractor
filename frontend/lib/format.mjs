const numberFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 20 });
export const number = value => numberFormat.format(value);
export const money = value => `${number(value)} ₸`;
const months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
// ISO dates are calendar days, not instants. Do not convert them through a timezone.
export function dateLabel(iso, withYear = true) {
  const [year, month, day] = iso.split('-').map(Number);
  return `${day} ${months[month - 1]}${withYear ? ` ${year}` : ''}`;
}
function plural(count, one, few, many) {
  const ending = count % 100;
  if (ending >= 11 && ending <= 14) return many;
  return count % 10 === 1 ? one : count % 10 >= 2 && count % 10 <= 4 ? few : many;
}
export const variants = count => plural(count, 'вариант', 'варианта', 'вариантов');
export const profiles = count => plural(count, 'профиль', 'профиля', 'профилей');
export function fieldMessage(value, fallback) {
  // Error pages and infrastructure details must not become user-facing messages.
  if (typeof value !== 'string' || !value.trim() || value.length > 350 || /traceback|stack trace|[A-Za-z]:[\\/]|\/(?:home|users|app|tmp)\/|<[^>]+>/i.test(value)) return fallback;
  return value;
}
