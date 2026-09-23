// Contract fixtures for browser checks only. The application never imports this file.
export const baseQuery = {
  city: 'Алматы', date: '2026-11-13', event_format: 'корпоратив', category: 'Банкетный зал',
  budget_kzt: 7000000, duration_hours: 6, language: 'русский',
};
export const versions = { dataset: 'fixture-catalog', facts: 'fixture-facts', algorithm: 'selection-v1' };
export const meta = {
  cities: ['Алматы', 'Астана', 'Зарубежье'],
  categories: ['Банкетный зал', 'Флорист', 'Декоратор', 'Ведущий', 'Лайв-бэнд'],
  event_formats: ['корпоратив', 'свадьба', 'конференция'], languages: ['русский', 'казахский', 'английский'],
  date_min: '2026-09-23', date_max: '2026-12-31', versions, explanation_mode: 'approved_facts',
  demo_queries: [
    { id: 'venues', label: 'Зал · осенний корпоратив', query: { ...baseQuery } },
    { id: 'florist', label: 'Редкая категория · флорист', query: { ...baseQuery, category: 'Флорист', budget_kzt: 500000 } },
    { id: 'absent', label: 'Декоратор · Астана', query: { ...baseQuery, city: 'Астана', category: 'Декоратор', budget_kzt: 3000000 } },
    { id: 'busy', label: 'Зал · декабрь', query: { ...baseQuery, date: '2026-12-19' } },
    { id: 'budget', label: 'Ведущий · небольшой бюджет', query: { ...baseQuery, category: 'Ведущий', date: '2026-11-14', budget_kzt: 600000 } },
    { id: 'bands', label: 'Музыкальный состав', query: { ...baseQuery, category: 'Лайв-бэнд', language: 'казахский', budget_kzt: 1200000 } },
  ],
};

function card(query, id, name, price, quote, options = {}) {
  return {
    id, anon_name: name, matched_category: query.category, city: query.city,
    categories: [query.category], languages: ['русский', 'казахский'],
    price_from_kzt: price, budget_headroom_kzt: query.budget_kzt - price, max_hours: 8,
    explanation: `По календарю не отмечен занятым на выбранную дату, принимает корпоративы; начальная цена — ${price.toLocaleString('ru-RU')} ₸. В описании: «${quote}».`,
    evidence: [
      { id: 'price', kind: 'field', source_field: 'price_from_kzt', text: `Начальная цена из каталога: ${price.toLocaleString('ru-RU')} ₸.`, quote: null, fact_id: null },
      { id: 'date', kind: 'field', source_field: 'busy_dates', text: `Дата ${query.date} не отмечена занятой в окне 2026-09-23 — 2026-12-31.`, quote: null, fact_id: null },
      { id: 'quote', kind: 'description', source_field: 'description', text: 'Особенность, указанная в описании этого профиля.', quote, fact_id: `${id}-detail` },
    ],
    data_flags: { synthetic: false, price_imputed: true, city_imputed: true },
    rank: { format_evidence: false, starting_price_kzt: price, tied_on_policy: false },
    comparison_note: null,
    ...options,
  };
}
function empty(query, catalog_count = 7) {
  return {
    status: 'all_filtered', query: { ...query }, summary: 'Все кандидаты заняты на выбранную дату.', cards: [],
    counts: { catalog_count, eligible_count: 0, shown_count: 0 },
    diagnostics: { reason_counts: { busy: catalog_count, format: 0, budget: 0, language: 0, duration: 0 }, otherwise_eligible_but_busy: catalog_count, exclusions: [] },
    suggestions: [], city_alternatives: [], versions, explanation_mode: 'approved_facts', warnings: [],
  };
}
const venueCard = (query, id) => {
  if (id === 'HK-64395') return card(query, id, 'Иноскэ Хашибира', 2500000, 'стильная панорамная локация в Алматы с захватывающим видом на город и горы');
  if (id === 'HK-58236') return card(query, id, 'Шинобу Кочо', 3000000, 'Интерьер вдохновлён традиционной юртой и украшен национальными мотивами');
  if (id === 'HK-99701') return card(query, id, 'Сосукэ', 4000000, 'премиальный гольф-курорт и ресторанный комплекс в Алматы');
  return card(query, id, 'Гохан', 3200000, 'вместимость зала до 200 гостей, свой кейтеринг и парковка для гостей мероприятия', {
    data_flags: { synthetic: true, price_imputed: true, city_imputed: false },
  });
};
function found(query, cards, catalogCount, eligibleCount = cards.length, summary = 'Подходящие варианты по вашим условиям.') {
  const response = empty(query, catalogCount);
  return { ...response, status: 'found', cards, summary,
    counts: { catalog_count: catalogCount, eligible_count: eligibleCount, shown_count: cards.length },
    diagnostics: { ...response.diagnostics, reason_counts: { busy: 0, format: 0, budget: 0, language: 0, duration: 0 }, otherwise_eligible_but_busy: 0 },
  };
}

// Explicit canned responses for interaction tests, not a substitute selection engine.
export function recommendation(query) {
  if (query.category === 'Декоратор' && query.city === 'Астана') {
    return { ...empty(query, 0), status: 'no_category_in_city', summary: 'В Астане в этом датасете нет декораторов.', city_alternatives: [{ city: 'Алматы', catalog_count: 3 }] };
  }
  if (query.category === 'Декоратор') {
    return found(query, [card(query, 'HK-90003', 'Усопп', 1800000, 'Работаем под ключ: проект, монтаж, демонтаж.')], 3);
  }
  if (query.category === 'Флорист') {
    const response = found(query, [card(query, 'HK-39372', 'Тони Тони Чоппер', 200000, 'Ежемесячно реализуем более 1000 заказов.', { max_hours: null })], 2, 1, 'Подходит 1 из 2: второй профиль не берёт корпоративы.');
    response.diagnostics.reason_counts.format = 1;
    response.diagnostics.exclusions = [{ id: 'HK-90001', reasons: ['format'] }];
    return response;
  }
  if (query.category === 'Ведущий') {
    if (query.budget_kzt === 600000) {
      const response = empty(query, 10);
      response.summary = 'Свободны пять ведущих, четыре берут корпоративы, но их начальные цены выше бюджета.';
      response.diagnostics.reason_counts = { busy: 5, format: 2, budget: 9, language: 0, duration: 0 };
      response.diagnostics.otherwise_eligible_but_busy = 1;
      response.suggestions = [{ field: 'budget_kzt', value: 650000, query: { ...query, budget_kzt: 650000 }, eligible_count: 1, shown_count: 1, message: 'Увеличение бюджета на 50 000 ₸ допускает 1 вариант по начальной цене.' }];
      return response;
    }
    return found(query, [card(query, 'HK-44923', 'Мицури Канроджи', 650000, 'Разработанный ТОЛЬКО для Вас сценарий')], 10);
  }
  if (query.category === 'Лайв-бэнд') {
    return found(query, [
      card(query, 'HK-23752', 'Thunder Breath Band', 1150000, 'два вокалиста 🎤 вокалистка', { max_hours: 6 }),
      card(query, 'HK-83709', 'Eva Sound', 1150000, '4 вокалиста, струнный квартет', { max_hours: 6 }),
    ], 5);
  }
  if (query.date === '2026-12-19') {
    const response = empty(query);
    response.diagnostics.exclusions = ['HK-64395', 'HK-58236', 'HK-90011', 'HK-99701', 'HK-72785', 'HK-50695', 'HK-69010'].map(id => ({ id, reasons: ['busy'] }));
    response.suggestions = ['2026-12-18', '2026-12-20'].map(value => ({ field: 'date', value, query: { ...query, date: value }, eligible_count: 1, shown_count: 1, message: `На ${value} проходит 1 зал при прежних условиях.` }));
    return response;
  }
  if (query.date === '2026-12-18' || query.date === '2026-12-20') return found(query, [venueCard(query, query.date === '2026-12-18' ? 'HK-90011' : 'HK-99701')], 7);
  if (query.date === '2026-11-14') {
    const response = found(query, ['HK-64395', 'HK-90011'].map(id => venueCard(query, id)), 7, 2, 'Найдено 2; из 7 залов города 5 заняты на выбранную дату.');
    response.diagnostics.reason_counts.busy = 5;
    response.diagnostics.otherwise_eligible_but_busy = 4;
    response.diagnostics.exclusions = ['HK-58236', 'HK-69010', 'HK-99701', 'HK-72785'].map(id => ({ id, reasons: ['busy'] }));
    response.diagnostics.exclusions.push({ id: 'HK-50695', reasons: ['busy', 'format'] });
    return response;
  }
  return found(query, ['HK-64395', 'HK-58236', 'HK-90011'].map(id => venueCard(query, id)), 7, 6, 'Показаны 3 из 6 подходящих: один из семи залов города не берёт корпоративы.');
}
