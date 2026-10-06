// Browser-test account controls using the real page CSS/layouts and exact injected script.
// Existing page business logic is covered by test_training_studio_browser.cjs.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const ui = JSON.parse(execFileSync('python3', ['-c',
  'import json; from hosted_session import account_bar, SESSION_SCRIPT; print(json.dumps({"bar": account_bar("owner@example.test").decode(), "script": SESSION_SCRIPT}))'
], {cwd: __dirname, encoding: 'utf8'}));
const files = ['studio_home.html', 'training_studio.html', 'mls_validation_ui.html', 'review_ui.html', 'source_rows.html', 'hosted_status.html'];

(async () => {
  const browser = await chromium.launch({headless: true, timeout: 20000});
  let checks = 0;
  try {
    for (const width of [390, 1440]) {
      for (const file of files) {
        const page = await browser.newPage({viewport: {width, height: 1000}});
        const errors = [];
        let apiMode = 'ok';
        page.on('pageerror', error => errors.push(error.message));
        const html = fs.readFileSync(path.join(__dirname, file), 'utf8')
          .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
          .replace(/<head\b[^>]*>/i, match => match + ui.script)
          .replace(/<body\b[^>]*>/i, match => match + ui.bar);
        await page.route('https://studio.test/**', async route => {
          const url = new URL(route.request().url());
          if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
          if (url.pathname === '/api/session') return route.fulfill({json: {authenticated: true, review_token: 'fresh', username: 'owner@example.test'}});
          if (url.pathname === '/api/studio/check') {
            if (apiMode === 'expired') return route.fulfill({status: 401, json: {code: 'session_expired'}});
            if (apiMode === 'storage') return route.fulfill({status: 503, json: {error: 'Database unavailable', request_id: 'example-reference'}});
            return route.fulfill({json: {ok: true}});
          }
          if (url.pathname === '/logout') {
            assert.equal(route.request().method(), 'POST');
            return route.fulfill({status: 303, headers: {location: '/login'}, body: ''});
          }
          if (url.pathname === '/login') return route.fulfill({contentType: 'text/html', body: '<h1>Sign in</h1>'});
          return route.fulfill({status: 404, body: ''});
        });
        await page.goto('https://studio.test/', {waitUntil: 'networkidle'});
        const bar = page.getByRole('region', {name: 'Account'});
        assert.equal(await bar.count(), 1);
        assert.equal(await bar.isVisible(), true, file);
        const button = bar.getByRole('button', {name: 'Log out'});
        const box = await button.boundingBox();
        assert.ok(box && box.width > 0 && box.height >= 40, `${file}: accessible logout target`);
        assert.ok(box.x >= 0 && box.x + box.width <= width + 1, `${file}: logout within viewport at ${width}`);
        assert.equal(await bar.locator('form').getAttribute('method'), 'post');
        assert.equal(await bar.locator('form').getAttribute('action'), '/logout');
        apiMode = 'storage';
        const status = await page.evaluate(async () => (await fetch('/api/studio/check')).status);
        assert.equal(status, 503);
        assert.match(await page.locator('#acq-session-notice').innerText(), /example-reference/);
        assert.equal(new URL(page.url()).pathname, '/');
        apiMode = 'expired';
        await page.evaluate(async () => fetch('/api/studio/check'));
        assert.equal(await bar.getByRole('link', {name: 'Sign in again'}).getAttribute('href'), '/login');
        await button.click();
        await page.waitForURL('https://studio.test/login');
        assert.equal(await page.getByRole('heading', {name: 'Sign in'}).isVisible(), true);
        assert.deepEqual(errors, []);
        checks++;
        await page.close();
      }
    }
    console.log(`Hosted account browser checks: ${checks} page/viewport cases passed`);
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
