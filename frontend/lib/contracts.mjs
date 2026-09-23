export const QUERY_FIELDS = ['city', 'date', 'event_format', 'category', 'budget_kzt', 'duration_hours', 'language'];
export const REASONS = ['busy', 'format', 'budget', 'language', 'duration'];
export const REASON_LABELS = {
  busy: 'Заняты на выбранную дату', format: 'Не берут этот формат',
  budget: 'Начальная цена выше бюджета', language: 'Нет выбранного языка',
  duration: 'Не покрывают нужную длительность',
};

export function isCalendarDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split('-').map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 &&
    day <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
}

export function queryKey(query) {
  return JSON.stringify(QUERY_FIELDS.map(key => query[key]));
}

export function changedFields(a, b) {
  return QUERY_FIELDS.filter(key => a[key] !== b[key]);
}

export function sameVersions(a, b) {
  return ['dataset', 'facts', 'algorithm'].every(key => a[key] === b[key]);
}

export function validateQuery(query, meta) {
  const errors = {};
  for (const [key, values, label] of [
    ['city', meta.cities, 'Выберите город из списка.'],
    ['event_format', meta.event_formats, 'Выберите тип мероприятия.'],
    ['category', meta.categories, 'Выберите категорию подрядчика.'],
  ]) if (!values.includes(query[key])) errors[key] = label;
  if (!isCalendarDate(query.date)) errors.date = 'Укажите корректную дату мероприятия.';
  else if (query.date < meta.date_min || query.date > meta.date_max) errors.date = 'На эту дату нет данных о занятости. Выберите дату в указанном окне.';
  if (!Number.isSafeInteger(query.budget_kzt) || query.budget_kzt <= 0) errors.budget_kzt = 'Введите положительную целую сумму до 9 007 199 254 740 991 ₸.';
  if (query.duration_hours !== null && (typeof query.duration_hours !== 'number' || !Number.isFinite(query.duration_hours) || query.duration_hours <= 0)) errors.duration_hours = 'Укажите число часов больше нуля или оставьте поле пустым.';
  if (query.language !== null && !meta.languages.includes(query.language)) errors.language = 'Выберите язык из списка или «Неважно».';
  return errors;
}

export function parseForm(values, meta) {
  const budget = values.budget_kzt.trim().replace(/[\s\u00a0\u202f]/g, '');
  const hours = values.duration_hours.trim();
  const query = {
    city: values.city, date: values.date, event_format: values.event_format, category: values.category,
    budget_kzt: /^\d+$/.test(budget) ? Number(budget) : NaN,
    duration_hours: hours === '' ? null : /^\d+(?:[.,]\d+)?$/.test(hours) ? Number(hours.replace(',', '.')) : NaN,
    language: values.language || null,
  };
  return { query, errors: validateQuery(query, meta) };
}

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string';
const nonempty = value => text(value) && value.trim().length > 0;
const strings = value => Array.isArray(value) && value.every(nonempty);
const count = value => Number.isSafeInteger(value) && value >= 0;
const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;
const modes = ['approved_facts', 'structured_only'];
const versions = value => object(value) && nonempty(value.dataset) && nonempty(value.algorithm) && (value.facts === null || nonempty(value.facts));
const queryShape = value => object(value) && Object.keys(value).length === QUERY_FIELDS.length && QUERY_FIELDS.every(key => Object.hasOwn(value, key));
const requireContract = condition => { if (!condition) throw new Error('invalid_contract'); };

// Validate the transport contract only. Selection and ranking belong to the API.
export function validateMeta(meta) {
  requireContract(object(meta));
  for (const key of ['cities', 'categories', 'event_formats', 'languages']) {
    requireContract(strings(meta[key]) && meta[key].length > 0 && new Set(meta[key]).size === meta[key].length);
  }
  requireContract(isCalendarDate(meta.date_min) && isCalendarDate(meta.date_max) && meta.date_min <= meta.date_max);
  requireContract(versions(meta.versions) && modes.includes(meta.explanation_mode) && Array.isArray(meta.demo_queries));
  const seen = new Set();
  for (const demo of meta.demo_queries) {
    requireContract(object(demo) && nonempty(demo.id) && nonempty(demo.label) && !seen.has(demo.id));
    requireContract(queryShape(demo.query) && Object.keys(validateQuery(demo.query, meta)).length === 0);
    seen.add(demo.id);
  }
  return meta;
}

export function validateResponse(response, meta, submittedQuery) {
  requireContract(object(response) && ['found', 'no_category_in_city', 'all_filtered'].includes(response.status));
  requireContract(queryShape(response.query) && Object.keys(validateQuery(response.query, meta)).length === 0);
  requireContract(queryKey(response.query) === queryKey(submittedQuery));
  requireContract(text(response.summary) && versions(response.versions) && modes.includes(response.explanation_mode));
  requireContract(Array.isArray(response.warnings) && response.warnings.every(text));
  requireContract(object(response.counts));
  const { catalog_count, eligible_count, shown_count } = response.counts;
  requireContract([catalog_count, eligible_count, shown_count].every(count));
  requireContract(catalog_count >= eligible_count && shown_count === Math.min(3, eligible_count));
  requireContract(Array.isArray(response.cards) && response.cards.length === shown_count);
  requireContract((response.status === 'found') === (eligible_count > 0));
  requireContract((response.status === 'no_category_in_city') === (catalog_count === 0));
  const ids = new Set();
  for (const card of response.cards) {
    requireContract(object(card) && ['id', 'anon_name', 'matched_category', 'city', 'explanation'].every(key => nonempty(card[key])));
    requireContract(!ids.has(card.id)); ids.add(card.id);
    requireContract(strings(card.categories) && strings(card.languages));
    requireContract(Number.isSafeInteger(card.price_from_kzt) && card.price_from_kzt > 0 && count(card.budget_headroom_kzt));
    requireContract(card.max_hours === null || positive(card.max_hours));
    requireContract(card.comparison_note === null || text(card.comparison_note));
    requireContract(object(card.data_flags) && ['synthetic', 'price_imputed', 'city_imputed'].every(key => typeof card.data_flags[key] === 'boolean'));
    requireContract(object(card.rank) && typeof card.rank.format_evidence === 'boolean' && typeof card.rank.tied_on_policy === 'boolean' && Number.isSafeInteger(card.rank.starting_price_kzt));
    requireContract(Array.isArray(card.evidence));
    const evidenceIds = new Set();
    for (const evidence of card.evidence) {
      requireContract(object(evidence) && nonempty(evidence.id) && !evidenceIds.has(evidence.id)); evidenceIds.add(evidence.id);
      requireContract(['field', 'derived', 'description'].includes(evidence.kind) && nonempty(evidence.source_field) && text(evidence.text));
      requireContract((evidence.quote === null || text(evidence.quote)) && (evidence.fact_id === null || text(evidence.fact_id)));
      if (evidence.kind === 'description') requireContract(nonempty(evidence.quote) && nonempty(evidence.fact_id) && evidence.source_field === 'description');
    }
  }
  const diagnostics = response.diagnostics;
  requireContract(object(diagnostics) && object(diagnostics.reason_counts) && REASONS.every(key => count(diagnostics.reason_counts[key])));
  requireContract(count(diagnostics.otherwise_eligible_but_busy) && Array.isArray(diagnostics.exclusions));
  const excludedIds = new Set();
  for (const exclusion of diagnostics.exclusions) {
    requireContract(object(exclusion) && nonempty(exclusion.id) && !excludedIds.has(exclusion.id) && !ids.has(exclusion.id));
    excludedIds.add(exclusion.id);
    requireContract(Array.isArray(exclusion.reasons) && exclusion.reasons.length > 0 && exclusion.reasons.every(reason => REASONS.includes(reason)));
  }
  requireContract(Array.isArray(response.suggestions));
  for (const suggestion of response.suggestions) {
    requireContract(object(suggestion) && ['date', 'budget_kzt'].includes(suggestion.field) && text(suggestion.message));
    requireContract(queryShape(suggestion.query) && Object.keys(validateQuery(suggestion.query, meta)).length === 0);
    const changes = changedFields(response.query, suggestion.query);
    requireContract(changes.length === 1 && changes[0] === suggestion.field && suggestion.value === suggestion.query[suggestion.field]);
    requireContract(count(suggestion.eligible_count) && suggestion.shown_count === Math.min(3, suggestion.eligible_count));
    requireContract(suggestion.shown_count > shown_count);
  }
  requireContract(Array.isArray(response.city_alternatives) && response.city_alternatives.every(item => object(item) && meta.cities.includes(item.city) && item.city !== response.query.city && count(item.catalog_count)));
  return response;
}

export function dateChanges(previous, current) {
  if (!previous || !sameVersions(previous.versions, current.versions)) return [];
  const changes = changedFields(previous.query, current.query);
  if (changes.length !== 1 || changes[0] !== 'date') return [];
  const currentIds = new Set(current.cards.map(card => card.id));
  return previous.cards.filter(card => !currentIds.has(card.id)).map(card => {
    const exclusion = current.diagnostics.exclusions.find(item => item.id === card.id);
    return { id: card.id, name: card.anon_name, reasons: exclusion?.reasons || [] };
  });
}
