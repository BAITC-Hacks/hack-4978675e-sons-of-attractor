import test from 'node:test';
import assert from 'node:assert/strict';
import { parseForm, validateMeta, validateResponse, dateChanges, queryKey, isCalendarDate } from '../lib/contracts.mjs';
import { dateLabel, fieldMessage } from '../lib/format.mjs';
import { meta, baseQuery, recommendation } from './fixtures.mjs';

const values = () => Object.fromEntries(Object.entries(baseQuery).map(([key, value]) => [key, String(value)]));

test('form preserves calendar days, safe integers and absent optional filters', () => {
  const result = parseForm({ ...values(), budget_kzt: '7 000 000', duration_hours: '', language: '' }, meta);
  assert.deepEqual(result.errors, {});
  assert.equal(result.query.budget_kzt, 7000000);
  assert.equal(result.query.duration_hours, null);
  assert.equal(result.query.language, null);
  assert.equal(result.query.date, '2026-11-13');
  assert.equal(parseForm({ ...values(), duration_hours: '2,5' }, meta).query.duration_hours, 2.5);
});
test('invalid and unsafe numeric values cannot silently become optional filters', () => {
  for (const budget_kzt of ['', '0', '-1', '1.5', 'NaN', 'Infinity', '1e20', '9007199254740992']) {
    assert.ok(parseForm({ ...values(), budget_kzt }, meta).errors.budget_kzt, budget_kzt);
  }
  for (const duration_hours of ['-1', '0', 'abc', 'Infinity', '1e309']) {
    assert.ok(parseForm({ ...values(), duration_hours }, meta).errors.duration_hours, duration_hours);
  }
});
test('calendar validation respects both endpoints and rejects impossible dates', () => {
  for (const date of ['2026-09-23', '2026-12-31']) assert.equal(parseForm({ ...values(), date }, meta).errors.date, undefined);
  for (const date of ['2026-09-22', '2027-01-01', '2026-11-31', '2026-02-29', '']) assert.ok(parseForm({ ...values(), date }, meta).errors.date);
  assert.equal(isCalendarDate('2024-02-29'), true);
  assert.equal(dateLabel('2026-11-13'), '13 ноября 2026');
});
test('meta and every documented UI fixture conform to the same API schema', () => {
  assert.equal(validateMeta(meta), meta);
  for (const demo of meta.demo_queries) {
    const response = recommendation(demo.query);
    assert.equal(validateResponse(response, meta, demo.query), response);
  }
});
test('response must belong to the submitted query, contain at most three cards and unique IDs', () => {
  const response = recommendation(baseQuery);
  assert.throws(() => validateResponse(response, meta, { ...baseQuery, date: '2026-11-14' }));
  const duplicate = structuredClone(response); duplicate.cards[1] = duplicate.cards[0];
  assert.throws(() => validateResponse(duplicate, meta, baseQuery));
  const extra = structuredClone(response); extra.cards.push(extra.cards[0]);
  assert.throws(() => validateResponse(extra, meta, baseQuery));
  assert.deepEqual(validateResponse(response, meta, baseQuery).cards.map(card => card.id), ['HK-64395', 'HK-58236', 'HK-90011']);
});
test('suggestions must change exactly their declared field and improve the displayed count', () => {
  const query = { ...baseQuery, date: '2026-12-19' };
  const response = recommendation(query);
  response.suggestions[0].query.city = 'Астана';
  assert.throws(() => validateResponse(response, meta, query));
  const wrongValue = recommendation(query); wrongValue.suggestions[0].value = '2026-12-17';
  assert.throws(() => validateResponse(wrongValue, meta, query));
});
test('date comparison uses exclusions, not a guess based on absence from top three', () => {
  const previous = recommendation(baseQuery);
  const current = recommendation({ ...baseQuery, date: '2026-11-14' });
  assert.deepEqual(dateChanges(previous, current), [{ id: 'HK-58236', name: 'Шинобу Кочо', reasons: ['busy'] }]);
  current.diagnostics.exclusions = [];
  assert.deepEqual(dateChanges(previous, current)[0].reasons, []);
  current.query.budget_kzt++;
  assert.deepEqual(dateChanges(previous, current), []);
  current.query.budget_kzt--;
  current.versions = { ...current.versions, facts: 'different-facts' };
  assert.deepEqual(dateChanges(previous, current), []);
});
test('query key is independent of property insertion order', () => {
  assert.equal(queryKey(baseQuery), queryKey(Object.fromEntries(Object.entries(baseQuery).reverse())));
});
test('infrastructure details are never shown as friendly API errors', () => {
  assert.equal(fieldMessage('Traceback: C:\\Users\\secret.py', 'fallback'), 'fallback');
  assert.equal(fieldMessage('<html>Server failure</html>', 'fallback'), 'fallback');
  assert.equal(fieldMessage('Проверьте дату', 'fallback'), 'Проверьте дату');
});
