import {
  QUERY_FIELDS, REASONS, REASON_LABELS, parseForm, validateMeta, validateResponse,
  queryKey, changedFields, dateChanges,
} from './lib/contracts.mjs';
import { number, money, dateLabel, variants, profiles, fieldMessage } from './lib/format.mjs';

const $ = id => document.getElementById(id);
const form = $('search-form');
const fields = Object.fromEntries(QUERY_FIELDS.map(key => [key, $(key)]));
const state = { meta: null, active: null, requestId: 0, lastSuccess: null, displayed: null, dirty: false, retryQuery: null };
const originalEmpty = [...$('result-content').children].map(child => child.cloneNode(true));
const factsNotice = 'Сравниваем цены, условия и занятость. Дополнительные сведения из описаний пока недоступны.';
const factsWarnings = new Set([
  'AI-реестр отсутствует; используются только структурированные поля.',
  'AI-реестр повреждён, несовместим или не полностью проверен; он целиком отключён.',
  'Подбор работает по структурированным данным; факты из описаний недоступны.',
]);

function el(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = content;
  return element;
}
function button(label, action, className = 'button button-secondary') {
  const element = el('button', className, label);
  element.type = 'button';
  element.addEventListener('click', action);
  return element;
}
function announce(message) { $('announcer').textContent = message; }
function focusResult() {
  const heading = $('results-title');
  heading.focus({ preventScroll: true });
  const bounds = heading.getBoundingClientRect();
  if (bounds.top < 0 || bounds.bottom > window.innerHeight) {
    // Instant scrolling also respects reduced-motion preferences.
    heading.scrollIntoView({ block: 'start', behavior: 'instant' });
  }
}
function syncFactsNotice() {
  // Result warnings describe their own snapshot. Outside a result, show the
  // latest known mode without sharing the catalogue error container.
  const mode = (state.lastSuccess ?? state.meta)?.explanation_mode;
  const show = mode === 'structured_only' && !state.displayed;
  const notice = $('facts-feedback');
  notice.textContent = show ? factsNotice : '';
  notice.hidden = !show;
}
function readForm() { return Object.fromEntries(QUERY_FIELDS.map(key => [key, fields[key].value])); }
function fillForm(query) {
  for (const key of QUERY_FIELDS) fields[key].value = query[key] ?? '';
  clearErrors();
}
function clearErrors() {
  for (const key of QUERY_FIELDS) {
    fields[key].removeAttribute('aria-invalid');
    $(`${key}-error`).hidden = true;
    $(`${key}-error`).textContent = '';
  }
}
function showFieldErrors(errors) {
  let first;
  for (const key of QUERY_FIELDS) {
    if (!errors[key]) continue;
    const target = $(`${key}-error`);
    target.textContent = fieldMessage(errors[key], 'Проверьте значение этого поля.');
    target.hidden = false;
    fields[key].setAttribute('aria-invalid', 'true');
    first ||= fields[key];
  }
  if (first) {
    const details = first.closest('details');
    if (details) details.open = true;
    first.focus();
  }
}
function setBusy(busy) {
  $('results-panel').setAttribute('aria-busy', String(busy));
  $('submit-button').disabled = !state.meta || busy;
  $('submit-button').firstElementChild.textContent = busy ? 'Проверяем условия…' : 'Подобрать варианты';
}
function invalidateRequest() {
  state.requestId++;
  state.active?.abort();
  state.active = null;
  setBusy(false);
}
function hideFeedback() {
  $('request-feedback').replaceChildren();
  $('request-feedback').hidden = true;
}
function feedback(message, retry) {
  const box = el('div', 'notice error-notice');
  box.setAttribute('role', 'alert');
  box.append(el('p', '', message));
  if (retry) box.append(button('Повторить', retry));
  $('request-feedback').replaceChildren(box);
  $('request-feedback').hidden = false;
}
function showIdle() {
  state.displayed = null;
  $('result-content').replaceChildren(...originalEmpty.map(child => child.cloneNode(true)));
  $('result-counter').textContent = 'До 3 вариантов';
  syncFactsNotice();
}
function editForm() {
  const pending = Boolean(state.active);
  invalidateRequest();
  state.dirty = true;
  state.retryQuery = null;
  clearErrors();
  hideFeedback();
  if (pending || !state.displayed) showIdle();
  else {
    $('stale-notice').hidden = false;
    document.querySelectorAll('[data-result-actions]').forEach(section => { section.hidden = true; });
    $('date-comparison')?.remove();
  }
}

class ApiError extends Error {
  constructor(status, payload) { super('api_error'); this.status = status; this.payload = payload; }
}
async function fetchJson(url, options) {
  const response = await fetch(url, { ...options, headers: { Accept: 'application/json', ...options.headers } });
  let payload;
  try { payload = await response.json(); } catch { throw new Error('invalid_contract'); }
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

async function loadMeta() {
  $('query-fields').disabled = true;
  $('submit-button').disabled = true;
  $('meta-feedback').hidden = true;
  $('meta-feedback').removeAttribute('role');
  $('facts-feedback').hidden = true;
  $('catalog-state').textContent = 'Загружаем каталог';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const meta = validateMeta(await fetchJson('/api/meta', { signal: controller.signal, cache: 'no-store' }));
    state.meta = meta;
    for (const [key, values, placeholder] of [
      ['city', meta.cities, 'Выберите город'], ['category', meta.categories, 'Выберите категорию'],
      ['event_format', meta.event_formats, 'Выберите формат'], ['language', meta.languages, 'Неважно'],
    ]) {
      const option = el('option', '', placeholder); option.value = '';
      fields[key].replaceChildren(option, ...values.map(value => { const item = el('option', '', value); item.value = value; return item; }));
    }
    fields.date.min = meta.date_min;
    fields.date.max = meta.date_max;
    $('date-hint').textContent = `Календарь: ${dateLabel(meta.date_min)} — ${dateLabel(meta.date_max)}.`;
    $('query-fields').disabled = false;
    $('submit-button').disabled = false;
    $('catalog-state').textContent = 'Каталог готов к подбору';
    document.body.dataset.ready = 'true';
    if (meta.demo_queries.length) fillForm(meta.demo_queries[0].query);
    renderDemos(meta.demo_queries);
    syncFactsNotice();
    announce('Каталог загружен. Укажите условия или выберите пример.');
  } catch {
    state.meta = null;
    syncFactsNotice();
    document.body.dataset.ready = 'false';
    $('catalog-state').textContent = 'Каталог недоступен';
    const box = $('meta-feedback');
    box.className = 'notice error-notice service-notice';
    box.setAttribute('role', 'alert');
    box.replaceChildren(el('p', '', 'Не удалось загрузить каталог. Проверьте соединение и повторите попытку.'), button('Повторить загрузку каталога', loadMeta));
    box.hidden = false;
    announce('Не удалось загрузить каталог. Доступна повторная загрузка.');
  } finally { clearTimeout(timeout); }
}

function renderDemos(demos) {
  $('demo-buttons').replaceChildren(...demos.map(demo => button(`${demo.label} ↗`, () => {
    fillForm(demo.query);
    submitQuery({ ...demo.query });
  }, 'button demo-button')));
  $('demo-section').hidden = demos.length === 0;
  $('demo-link').hidden = demos.length === 0;
}

form.addEventListener('input', editForm);
form.addEventListener('change', editForm);
form.addEventListener('submit', event => {
  event.preventDefault();
  if (!state.meta || state.active) return;
  clearErrors();
  hideFeedback();
  const { query, errors } = parseForm(readForm(), state.meta);
  if (Object.keys(errors).length) {
    showFieldErrors(errors);
    announce('Проверьте выделенные поля формы.');
    return;
  }
  submitQuery(query);
});

async function submitQuery(query) {
  if (state.active && queryKey(query) === state.active.queryKey) return;
  invalidateRequest();
  clearErrors();
  hideFeedback();
  const id = state.requestId;
  const controller = new AbortController();
  controller.queryKey = queryKey(query);
  state.active = controller;
  state.retryQuery = query;
  state.dirty = false;
  state.displayed = null;
  syncFactsNotice();
  setBusy(true);
  const loading = el('div', 'loading-state');
  const heading = el('h2', '', 'Проверяем условия'); heading.id = 'results-title'; heading.tabIndex = -1;
  const spinner = el('span', 'spinner'); spinner.setAttribute('aria-hidden', 'true');
  loading.append(spinner, heading, el('p', '', 'Проверяем доступность и параметры запроса.'));
  $('result-content').replaceChildren(loading);
  $('result-counter').textContent = 'Подбираем варианты';
  announce('Проверяем условия.');
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 12000);
  try {
    const payload = await fetchJson('/api/recommendations', {
      method: 'POST', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(query),
    });
    if (id !== state.requestId) return;
    if (timedOut) throw new Error('timeout');
    const result = validateResponse(payload, state.meta, query);
    renderResult(result, state.lastSuccess);
    state.lastSuccess = result;
    state.displayed = result;
    state.retryQuery = null;
    state.dirty = false;
    syncFactsNotice();
    announce(`${$('results-title').textContent}. ${result.summary}`);
    focusResult();
  } catch (error) {
    if (id !== state.requestId) return;
    showIdle();
    if (error instanceof ApiError && error.status === 422) {
      const message = fieldMessage(error.payload?.error?.message, 'Проверьте параметры запроса.');
      feedback(message);
      showFieldErrors(error.payload?.error?.fields || {});
      announce(message);
    } else {
      const message = timedOut ? 'Сервис не ответил за 12 секунд. Повторите попытку.' :
        error instanceof ApiError && error.status === 503 ? 'Данные каталога временно недоступны. Попробуйте ещё раз позже.' :
        error.message === 'invalid_contract' ? 'Не удалось прочитать ответ сервиса. Повторите попытку.' :
        'Не удалось получить подборку. Проверьте соединение и повторите попытку.';
      feedback(message, () => {
        if (!state.dirty && state.retryQuery) submitQuery({ ...state.retryQuery });
      });
      announce(message);
    }
  } finally {
    clearTimeout(timeout);
    if (id === state.requestId) { state.active = null; setBusy(false); }
  }
}

function querySummary(query) {
  const list = el('ul', 'query-summary');
  list.setAttribute('aria-label', 'Параметры принятого запроса');
  const parts = [query.city, query.category, query.event_format, dateLabel(query.date), `Бюджет ${money(query.budget_kzt)}`,
    query.language ? `Язык: ${query.language}` : 'Язык: неважно',
    query.duration_hours === null ? 'Длительность: не указана' : `${number(query.duration_hours)} ч`];
  list.append(...parts.map(part => el('li', '', part)));
  return list;
}

function renderResult(result, previous) {
  const container = $('result-content');
  container.replaceChildren();
  const stale = el('p', 'notice stale-notice', 'Для предыдущих параметров. Нажмите «Подобрать варианты», чтобы обновить результат.');
  stale.id = 'stale-notice'; stale.hidden = true;
  const heading = el('h2', 'result-heading'); heading.id = 'results-title'; heading.tabIndex = -1;
  if (result.status === 'found') heading.textContent = result.counts.eligible_count > 3 ? `Показаны 3 из ${result.counts.eligible_count} подходящих` : `Подобрали ${result.counts.shown_count} ${variants(result.counts.shown_count)}`;
  else heading.textContent = result.status === 'no_category_in_city' ? 'В этом городе такой категории нет в каталоге' : 'Есть кандидаты, но никто не проходит условия';
  $('result-counter').textContent = result.status === 'found' ? `${result.counts.shown_count} ${variants(result.counts.shown_count)}` : 'Нет подходящих';
  container.append(stale, heading, querySummary(result.query), el('p', 'result-summary', result.summary));
  const warnings = [...new Set(result.warnings.map(value => factsWarnings.has(value) ? factsNotice : value))];
  if (result.explanation_mode === 'structured_only' && !warnings.includes(factsNotice)) warnings.unshift(factsNotice);
  if (warnings.length) {
    const box = el('div', 'notice mode-warning');
    box.append(...warnings.map(value => el('p', '', fieldMessage(value, 'Часть сведений недоступна; проверьте условия и источники.'))));
    container.append(box);
  }
  renderDateComparison(previous, result, container);
  if (result.cards.length) {
    container.append(el('p', 'pricing-note', 'Цены указаны «от». Итоговую стоимость уточняйте у подрядчика. Занятость — по календарю каталога.'));
    const cards = el('div', 'cards');
    cards.append(...result.cards.map(renderCard));
    container.append(cards);
  }
  renderSuggestions(result, container);
  renderCities(result, container);
  container.append(renderDiagnostics(result));
}

function renderCard(card, index) {
  const article = el('article', 'contractor-card'); article.dataset.contractorId = card.id;
  const top = el('div', 'card-top');
  const ordinal = el('span', 'card-index', String(index + 1).padStart(2, '0')); ordinal.setAttribute('aria-hidden', 'true');
  const identity = el('div', 'card-identity');
  const name = el('h3', '', card.anon_name);
  identity.append(name, el('p', 'card-meta', `${card.matched_category} · ${card.city}`));
  const extraCategories = card.categories.filter(value => value !== card.matched_category);
  if (extraCategories.length) identity.append(el('p', 'card-meta', extraCategories.join(' · ')));
  const price = el('div', 'card-price');
  price.append(el('p', 'price', `от ${money(card.price_from_kzt)}`), el('p', 'price-caption', 'за мероприятие'));
  top.append(ordinal, identity, price);
  const flags = el('div', 'card-flags');
  flags.append(el('span', card.data_flags.synthetic ? 'badge badge-warm' : 'badge', card.data_flags.synthetic ? 'Синтетический профиль' : 'Анонимизированный профиль'));
  if (card.data_flags.price_imputed) flags.append(el('span', 'badge badge-warm', 'Цена проставлена при подготовке данных'));
  if (card.data_flags.city_imputed) flags.append(el('span', 'badge badge-warm', 'Город проставлен при подготовке данных'));
  const facts = el('div', 'card-facts');
  facts.append(el('span', '', `Языки: ${card.languages.join(', ')}`));
  facts.append(el('span', '', card.max_hours === null ? 'Почасовое ограничение не применяется' : `До ${number(card.max_hours)} ч на площадке`));
  article.append(top, flags, el('p', 'explanation', card.explanation), facts,
    el('p', 'headroom', `В запасе ${money(card.budget_headroom_kzt)} от бюджета по цене «от»`));
  if (card.comparison_note) article.append(el('p', 'notice comparison-note', card.comparison_note));
  const details = el('details', 'evidence-details');
  details.append(el('summary', '', 'Детали и источники'));
  const evidenceList = el('ul', 'evidence-list');
  for (const evidence of card.evidence) {
    const item = el('li');
    const label = evidence.kind === 'description' ? 'Из описания в каталоге' : evidence.kind === 'derived' ? 'Расчёт по параметрам запроса' : 'Данные каталога';
    item.append(el('p', 'evidence-label', label), el('p', 'evidence-text', evidence.text));
    if (evidence.quote !== null) item.append(el('blockquote', '', evidence.quote));
    evidenceList.append(item);
  }
  if (!card.evidence.length) evidenceList.append(el('li', '', 'Сервис не передал подробные источники для этой карточки.'));
  details.append(evidenceList);
  article.append(details);
  return article;
}

function renderDateComparison(previous, result, container) {
  const changes = dateChanges(previous, result);
  if (!changes.length) return;
  const box = el('section', 'date-comparison'); box.id = 'date-comparison';
  box.setAttribute('aria-label', 'Что изменилось после смены даты');
  box.append(el('p', '', `Что изменилось на ${dateLabel(result.query.date, false)}`));
  for (const change of changes) {
    let reason;
    if (!change.reasons.length) reason = 'не вошёл в текущую тройку; это не означает занятость.';
    else if (change.reasons.length === 1 && change.reasons[0] === 'busy') reason = 'на новую дату занят по календарю.';
    else reason = `не проходит условия: ${change.reasons.map(value => REASON_LABELS[value].toLowerCase()).join('; ')}.`;
    box.append(el('p', '', `${change.name} — ${reason}`));
  }
  container.append(box);
}

function currentResultIs(result) {
  if (state.dirty || state.active || state.displayed !== result) return false;
  const { query, errors } = parseForm(readForm(), state.meta);
  return !Object.keys(errors).length && queryKey(query) === queryKey(result.query);
}
function highlightField(key) {
  document.querySelectorAll('.changed-field').forEach(field => field.classList.remove('changed-field'));
  const field = fields[key].closest('.field');
  field.classList.add('changed-field');
  setTimeout(() => field.classList.remove('changed-field'), 2400);
}
function applyQuery(result, query, field) {
  if (!currentResultIs(result)) return;
  const changes = changedFields(result.query, query);
  if (changes.length !== 1 || changes[0] !== field) return;
  fillForm(query);
  highlightField(field);
  submitQuery({ ...query });
}
function renderSuggestions(result, container) {
  if (!result.suggestions.length) return;
  const section = el('section', 'suggestions'); section.dataset.resultActions = 'true';
  section.append(el('h3', '', 'Что можно изменить'));
  for (const suggestion of result.suggestions) {
    const item = el('div', 'suggestion');
    item.append(el('p', '', suggestion.message), el('p', 'suggestion-count', `По этим условиям: ${suggestion.eligible_count} ${variants(suggestion.eligible_count)}.`));
    if (suggestion.field === 'budget_kzt') item.append(el('p', 'suggestion-caveat', 'Порог по начальной цене. Итоговую стоимость нужно уточнить.'));
    const label = suggestion.field === 'date' ? `Выбрать ${dateLabel(suggestion.value, false)}` : `Установить бюджет ${money(suggestion.value)}`;
    item.append(button(label, () => applyQuery(result, suggestion.query, suggestion.field)));
    section.append(item);
  }
  container.append(section);
}
function renderCities(result, container) {
  if (result.status !== 'no_category_in_city' || !result.city_alternatives.length) return;
  const section = el('section', 'city-alternatives'); section.dataset.resultActions = 'true';
  section.append(el('h3', '', 'Категория есть в другом городе'));
  for (const alternative of result.city_alternatives) {
    const item = el('div', 'suggestion');
    item.append(el('p', '', `${alternative.city}: ${alternative.catalog_count} ${profiles(alternative.catalog_count)} этой категории в каталоге.`),
      el('p', 'suggestion-caveat', 'Это наличие категории, а не число подходящих на вашу дату. Выезд в другой город не подтверждён.'),
      button(`Искать: ${alternative.city}`, () => applyQuery(result, { ...result.query, city: alternative.city }, 'city')));
    section.append(item);
  }
  container.append(section);
}
function renderDiagnostics(result) {
  const details = el('details', 'diagnostics');
  details.append(el('summary', '', 'Как устроен подбор'));
  const content = el('div', 'diagnostics-content');
  content.append(el('p', '', `В городе в этой категории: ${result.counts.catalog_count}. Прошли все условия: ${result.counts.eligible_count}. Показаны: ${result.counts.shown_count}.`));
  content.append(el('p', '', 'Проверяем город, услугу, дату, формат, бюджет, язык и длительность. Среди подходящих выше стоят те, у кого есть проверенное упоминание вашего формата в описании, затем — варианты с меньшей ценой «от». При равенстве сохраняем порядок по номеру профиля. Позиция в списке не означает оценку качества.'));
  const reasons = el('dl', 'reason-list');
  for (const reason of REASONS) reasons.append(el('dt', '', REASON_LABELS[reason]), el('dd', '', String(result.diagnostics.reason_counts[reason])));
  content.append(reasons, el('p', '', 'У одного профиля может быть несколько причин отказа. Эти числа нельзя складывать как количество подрядчиков.'));
  if (result.diagnostics.otherwise_eligible_but_busy > 0) content.append(el('p', '', `Подошли бы по всем остальным условиям, но заняты: ${result.diagnostics.otherwise_eligible_but_busy}.`));
  const technical = el('details', 'technical-details');
  technical.append(el('summary', '', 'Версии и признаки порядка'));
  const list = el('ul');
  for (const key of ['dataset', 'facts', 'algorithm']) {
    const item = el('li', '', `${{ dataset: 'Датасет', facts: 'Факты', algorithm: 'Алгоритм' }[key]}: `);
    item.append(el('code', '', result.versions[key] ?? 'недоступны'));
    list.append(item);
  }
  list.append(el('li', '', `Режим: ${result.explanation_mode === 'approved_facts' ? 'принятый реестр фактов' : 'только структурированные данные'}.`));
  for (const card of result.cards) list.append(el('li', '', `${card.anon_name}: свидетельство формата ${card.rank.format_evidence ? 'есть' : 'нет'}; начальная цена ${money(card.rank.starting_price_kzt)}${card.rank.tied_on_policy ? '; есть другой кандидат с теми же признаками — порядок по ID' : ''}.`));
  technical.append(list);
  content.append(technical);
  details.append(content);
  return details;
}

loadMeta();
