import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '../.tools/node_modules/playwright/index.mjs';
import { meta, baseQuery, recommendation } from './fixtures.mjs';
import { validateMeta, validateResponse } from '../lib/contracts.mjs';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const artifactPath = resolve(root, '.artifacts');
await mkdir(artifactPath, { recursive: true });
const server = createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  const path = resolve(root, `.${pathname === '/' ? '/index.html' : pathname}`);
  if (!path.startsWith(root + sep) || pathname.startsWith('/api/') || pathname.includes('/.')) {
    response.writeHead(404); response.end(); return;
  }
  try {
    const body = await readFile(path);
    response.writeHead(200, { 'Content-Type': ({ '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8' })[extname(path)] || 'application/octet-stream' });
    response.end(body);
  } catch { response.writeHead(404); response.end(); }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const localUrl = `http://127.0.0.1:${server.address().port}`;
const liveIndex = process.argv.indexOf('--live');
const liveUrl = liveIndex >= 0 ? process.argv[liveIndex + 1] : null;
if (liveIndex >= 0 && !liveUrl) throw new Error('Usage: node frontend/tests/browser-check.mjs --live http://127.0.0.1:8000');
const browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'chrome' }).catch(error => {
  server.close();
  throw error;
});
const failures = [];
const cases = [];
const delay = ms => new Promise(done => setTimeout(done, ms));

async function run(name, fn) {
  try { await fn(); cases.push(name); console.log(`PASS ${name}`); }
  catch (error) { failures.push(name); console.error(`FAIL ${name}: ${error.stack}`); }
}
async function ready(page) { await page.waitForFunction(() => !document.getElementById('submit-button').disabled); }
async function submit(page) {
  await page.locator('#submit-button').click();
  await page.waitForFunction(() => document.querySelector('.contractor-card') || document.querySelector('#request-feedback:not([hidden])') || document.querySelector('#results-title')?.textContent.includes('кандидаты') || document.querySelector('#results-title')?.textContent.includes('категории нет'));
}
async function resultIds(page) { return page.locator('.contractor-card').evaluateAll(cards => cards.map(card => card.dataset.contractorId)); }

async function setup(options = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
  const requests = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  if (options.ignoreAbort) await page.addInitScript(() => {
    const fetchOriginal = window.fetch;
    window.fetch = (url, init = {}) => fetchOriginal(url, { ...init, signal: undefined });
  });
  let metaCalls = 0;
  await page.route('**/api/meta', route => {
    metaCalls++;
    if (options.metaFailOnce && metaCalls === 1) return route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: 'unavailable', fields: {} } } });
    return route.fulfill({ json: options.meta || meta });
  });
  await page.route('**/api/recommendations', async route => {
    const query = route.request().postDataJSON(); requests.push(query);
    if (options.respond) return options.respond(route, query, requests.length);
    return route.fulfill({ json: recommendation(query) });
  });
  await page.goto(localUrl);
  if (!options.metaFailOnce) await ready(page);
  return { page, requests, errors };
}

try {
  if (liveUrl) {
    await run('live API contract for every supplied demo', async () => {
      const response = await fetch(new URL('/api/meta', liveUrl));
      assert.equal(response.status, 200);
      const liveMeta = validateMeta(await response.json());
      assert.ok(liveMeta.demo_queries.length >= 3, 'The live API must provide demonstration presets.');
      for (const demo of liveMeta.demo_queries) {
        const result = await fetch(new URL('/api/recommendations', liveUrl), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(demo.query) });
        assert.equal(result.status, 200);
        validateResponse(await result.json(), liveMeta, demo.query);
      }
    });
    await run('live same-origin frontend and first preset', async () => {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.goto(liveUrl); await ready(page); await submit(page);
      assert.equal(await page.locator('#request-feedback').isVisible(), false);
      assert.deepEqual(errors, []);
      await page.screenshot({ path: resolve(artifactPath, 'live-result.png'), fullPage: true });
      await page.close();
    });
  } else {
    await run('initial state, live dictionary values, original card order and evidence', async () => {
      const { page, requests, errors } = await setup();
      assert.equal(requests.length, 0, 'Loading meta must not auto-run recommendations.');
      assert.equal(await page.locator('.contractor-card').count(), 0);
      assert.equal(await page.locator('.skip-link').evaluate(link => getComputedStyle(link).opacity), '0');
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => document.activeElement.className), 'skip-link');
      assert.equal(await page.locator('.skip-link').evaluate(link => getComputedStyle(link).opacity), '1');
      await page.keyboard.press('Tab');
      await page.screenshot({ path: resolve(artifactPath, 'desktop-idle.png'), fullPage: true });
      await submit(page);
      assert.deepEqual(requests[0], baseQuery);
      assert.deepEqual(await resultIds(page), ['HK-64395', 'HK-58236', 'HK-90011']);
      assert.match(await page.locator('#results-title').textContent(), /3 из 6/);
      assert.equal(await page.getByText('Синтетический профиль', { exact: true }).count(), 1);
      await page.locator('.evidence-details').first().locator('summary').click();
      assert.match(await page.locator('.evidence-details').first().textContent(), /Из описания в каталоге/);
      await page.screenshot({ path: resolve(artifactPath, 'desktop-result.png'), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      await page.screenshot({ path: resolve(artifactPath, 'mobile-result.png'), fullPage: true });
      assert.deepEqual(errors, []);
      await page.close();
    });
    await run('editing marks old results; date change reports actual busy exclusion', async () => {
      const { page, errors } = await setup();
      await submit(page);
      await page.locator('#date').fill('2026-11-14');
      assert.equal(await page.locator('#stale-notice').isVisible(), true);
      await submit(page);
      assert.deepEqual(await resultIds(page), ['HK-64395', 'HK-90011']);
      assert.match(await page.locator('#date-comparison').textContent(), /Шинобу Кочо — на новую дату занят/);
      assert.match(await page.locator('.query-summary').textContent(), /14 ноября 2026/);
      assert.deepEqual(errors, []);
      await page.close();
    });
    await run('all demo presets use the API; null hours and same-price bands render honestly', async () => {
      const { page, requests, errors } = await setup();
      for (let i = 0; i < meta.demo_queries.length; i++) {
        const before = requests.length;
        await page.locator('.demo-button').nth(i).click();
        await page.waitForFunction(() => document.getElementById('results-panel').getAttribute('aria-busy') === 'false');
        assert.equal(requests.length, before + 1);
        assert.deepEqual(requests.at(-1), meta.demo_queries[i].query);
        assert.equal(await page.locator('#request-feedback').isVisible(), false);
        if (i === 1) assert.match(await page.locator('.contractor-card').textContent(), /Ограничение по часам присутствия неприменимо/);
        if (i === 5) {
          assert.deepEqual(await resultIds(page), ['HK-23752', 'HK-83709']);
          assert.match(await page.locator('.explanation').nth(0).textContent(), /два вокалиста/);
          assert.match(await page.locator('.explanation').nth(1).textContent(), /струнный квартет/);
        }
      }
      assert.deepEqual(errors, []); await page.close();
    });
    await run('date and budget suggestions make one changed-field request; stale suggestions disappear', async () => {
      const { page, requests } = await setup();
      await page.locator('.demo-button').nth(3).click();
      await page.getByRole('button', { name: 'Выбрать 18 декабря' }).waitFor();
      await page.screenshot({ path: resolve(artifactPath, 'empty-result.png'), fullPage: true });
      await page.getByRole('button', { name: 'Выбрать 18 декабря' }).click();
      await page.locator('.contractor-card').waitFor();
      assert.deepEqual(requests.at(-1), { ...meta.demo_queries[3].query, date: '2026-12-18' });
      assert.deepEqual(await resultIds(page), ['HK-90011']);
      await page.locator('.demo-button').nth(4).click();
      await page.getByRole('button', { name: /Установить бюджет/ }).waitFor();
      await page.getByRole('button', { name: /Установить бюджет/ }).click();
      await page.locator('.contractor-card').waitFor();
      assert.deepEqual(requests.at(-1), { ...meta.demo_queries[4].query, budget_kzt: 650000 });
      assert.deepEqual(await resultIds(page), ['HK-44923']);
      await page.locator('.demo-button').nth(3).click();
      await page.getByRole('button', { name: 'Выбрать 18 декабря' }).waitFor();
      await page.locator('#budget_kzt').fill('8000000');
      assert.equal(await page.getByRole('button', { name: 'Выбрать 18 декабря' }).isVisible(), false);
      await page.close();
    });
    await run('city alternative changes only city and does not promise travel', async () => {
      const { page, requests } = await setup();
      await page.locator('.demo-button').nth(2).click();
      await page.getByRole('button', { name: 'Искать: Алматы' }).waitFor();
      assert.match(await page.locator('#results-title').textContent(), /категории нет в датасете/);
      assert.equal(await page.locator('.contractor-card').count(), 0);
      await page.getByRole('button', { name: 'Искать: Алматы' }).click();
      await page.locator('.contractor-card').waitFor();
      assert.deepEqual(requests.at(-1), { ...meta.demo_queries[2].query, city: 'Алматы' });
      await page.close();
    });
    await run('field validation blocks invalid requests and focuses the field; optional fields send null', async () => {
      const { page, requests } = await setup();
      await page.locator('#budget_kzt').fill(''); await page.locator('#submit-button').click();
      assert.equal(requests.length, 0);
      assert.equal(await page.locator('#budget_kzt').getAttribute('aria-invalid'), 'true');
      assert.equal(await page.evaluate(() => document.activeElement.id), 'budget_kzt');
      await page.locator('#budget_kzt').fill('7000000');
      await page.locator('#date').fill('2027-01-01'); await page.locator('#submit-button').click();
      assert.equal(requests.length, 0);
      assert.equal(await page.locator('#date').getAttribute('aria-invalid'), 'true');
      await page.locator('#date').fill('2026-11-13');
      await page.locator('#language').selectOption(''); await page.locator('#duration_hours').fill('');
      await submit(page);
      assert.equal(requests.at(-1).language, null); assert.equal(requests.at(-1).duration_hours, null);
      await page.close();
    });
    await run('meta failure retries without invented dictionaries', async () => {
      const { page, requests } = await setup({ metaFailOnce: true });
      await page.getByRole('button', { name: 'Повторить загрузку каталога' }).waitFor();
      assert.equal(await page.locator('#submit-button').isDisabled(), true);
      assert.equal(await page.locator('#city option').count(), 1);
      await page.getByRole('button', { name: 'Повторить загрузку каталога' }).click(); await ready(page);
      assert.equal(requests.length, 0); await page.close();
    });
    await run('503 retries; 422 binds field errors; server details stay private', async () => {
      const { page, requests } = await setup({ respond: (route, query, count) => {
        if (count === 1) return route.fulfill({ status: 503, json: { error: { message: 'Traceback C:\\Users\\secret.py', fields: {} } } });
        if (count === 2) return route.fulfill({ status: 422, json: { error: { message: 'Проверьте параметры запроса', fields: { duration_hours: 'Длительность недопустима' } } } });
        return route.fulfill({ json: recommendation(query) });
      } });
      await submit(page);
      assert.doesNotMatch(await page.locator('body').innerText(), /Traceback|secret.py/);
      await page.getByRole('button', { name: 'Повторить', exact: true }).click();
      await page.locator('#duration_hours-error:not([hidden])').waitFor();
      assert.equal(await page.evaluate(() => document.activeElement.id), 'duration_hours');
      await page.locator('#duration_hours').fill('5'); await submit(page);
      assert.equal(requests.length, 3); assert.equal(await page.locator('.contractor-card').count(), 3);
      await page.close();
    });
    await run('out-of-order responses cannot replace the newer result even if abort is ignored', async () => {
      const { page, requests } = await setup({ ignoreAbort: true, respond: async (route, query) => {
        if (query.date === '2026-11-13') await delay(700);
        return route.fulfill({ json: recommendation(query) });
      } });
      await page.locator('#submit-button').click();
      await page.waitForTimeout(100);
      assert.equal(await page.locator('#submit-button').isDisabled(), true);
      await page.locator('#date').fill('2026-11-14');
      await submit(page);
      await page.waitForTimeout(800);
      assert.deepEqual(await resultIds(page), ['HK-64395', 'HK-90011']);
      assert.match(await page.locator('.query-summary').textContent(), /14 ноября/);
      assert.equal(requests.length, 2); await page.close();
    });
    await run('network and 500 failures recover; frontend never reorders API cards', async () => {
      const { page } = await setup({ respond: (route, query, count) => {
        if (count === 1) return route.abort('failed');
        if (count === 2) return route.fulfill({ status: 500, json: { error: { message: 'Traceback /app/private.py', fields: {} } } });
        const response = recommendation(query);
        response.cards = [response.cards[2], response.cards[0], response.cards[1]];
        return route.fulfill({ json: response });
      } });
      await submit(page);
      await page.getByRole('button', { name: 'Повторить', exact: true }).click();
      await page.waitForFunction(() => document.getElementById('results-panel').getAttribute('aria-busy') === 'false');
      assert.doesNotMatch(await page.locator('body').innerText(), /Traceback|private.py/);
      await page.getByRole('button', { name: 'Повторить', exact: true }).click();
      await page.locator('.contractor-card').first().waitFor();
      assert.deepEqual(await resultIds(page), ['HK-90011', 'HK-64395', 'HK-58236']);
      await page.close();
    });
    await run('external text stays text, long content fits mobile and keyboard disclosures work', async () => {
      const malicious = '<img src=x onerror="window.injected=true">';
      const { page, errors } = await setup({ respond: (route, query) => {
        const response = recommendation(query);
        response.cards[0].anon_name = malicious + ' ДлинноеИмя'.repeat(8);
        response.cards[0].explanation += malicious;
        response.cards[0].evidence[0].text = malicious;
        response.cards[0].evidence[2].quote += malicious;
        return route.fulfill({ json: response });
      } });
      await page.setViewportSize({ width: 360, height: 780 }); await submit(page);
      const summary = page.locator('.evidence-details').first().locator('summary');
      await summary.focus(); await page.keyboard.press('Enter');
      assert.equal(await page.locator('.evidence-details').first().getAttribute('open'), '');
      assert.equal(await page.locator('.contractor-card img').count(), 0);
      assert.equal(await page.evaluate(() => window.injected), undefined);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert.deepEqual(errors, []); await page.close();
    });
    await run('structured_only warnings and comparison limits remain visible', async () => {
      const { page } = await setup({ meta: { ...meta, explanation_mode: 'structured_only', versions: { ...meta.versions, facts: null } }, respond: (route, query) => {
        const response = recommendation(query); response.explanation_mode = 'structured_only'; response.versions = { ...response.versions, facts: null };
        response.cards.forEach(card => { card.evidence = card.evidence.filter(item => item.kind !== 'description'); card.comparison_note = 'По доступным сведениям варианты не удаётся содержательно различить'; });
        return route.fulfill({ json: response });
      } });
      assert.equal(await page.locator('#meta-feedback').isVisible(), true);
      await submit(page);
      assert.match(await page.locator('.mode-warning').textContent(), /факты из описаний недоступны/);
      assert.equal(await page.locator('.comparison-note').count(), 3); await page.close();
    });
    await run('request timeout exits loading and permits retry', async () => {
      const { page } = await setup({ respond: async route => { await delay(1500); try { await route.abort(); } catch {} } });
      await page.clock.install();
      await page.locator('#submit-button').click();
      await page.clock.fastForward(12001);
      await page.getByRole('button', { name: 'Повторить', exact: true }).waitFor();
      assert.match(await page.locator('#request-feedback').textContent(), /12 секунд/);
      assert.equal(await page.locator('#submit-button').isDisabled(), false); await page.close();
    });
  }
} finally {
  await browser.close();
  server.closeAllConnections();
  await new Promise(done => server.close(done));
}
console.log(`${cases.length} browser checks passed; ${failures.length} failed. ${liveUrl ? 'LIVE API' : 'CONTRACT FIXTURES ONLY'}`);
if (failures.length) process.exitCode = 1;
