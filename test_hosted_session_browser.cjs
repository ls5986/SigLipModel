// Browser-test the real page layouts and injected account script.
// Business logic and backend authentication are covered by their dedicated suites.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {execFileSync} = require('node:child_process');
const ui = JSON.parse(execFileSync('python3', ['-c',
  'import json; from hosted_session import account_bar, SESSION_SCRIPT; print(json.dumps({"bar": account_bar("owner@example.test").decode(), "script": SESSION_SCRIPT}))'
], {cwd: __dirname, encoding: 'utf8'}));
const files = ['studio_home.html', 'training_studio.html', 'mls_validation_ui.html', 'review_ui.html', 'source_rows.html', 'hosted_status.html'];

(async () => {
  let html = '', apiMode = 'ok', logoutMethods = [];
  // Use actual HTTP redirects: page.route intercepts only the first URL of a redirect chain.
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    response.setHeader('Cache-Control', 'no-store');
    function json(status, value) {
      response.writeHead(status, {'Content-Type': 'application/json'});
      response.end(JSON.stringify(value));
    }
    if (pathname === '/') {
      response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
      return response.end(html);
    }
    if (pathname === '/api/session') return json(200, {authenticated: true, review_token: 'fresh', username: 'owner@example.test'});
    if (pathname === '/api/studio/check') {
      if (apiMode === 'expired') return json(401, {code: 'session_expired'});
      if (apiMode === 'storage') return json(503, {error: 'Database unavailable', request_id: 'example-reference'});
      return json(200, {ok: true});
    }
    if (pathname === '/logout') {
      logoutMethods.push(request.method);
      request.resume();
      response.writeHead(303, {Location: '/login'});
      return response.end();
    }
    if (pathname === '/login') {
      response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
      return response.end('<h1>Sign in</h1>');
    }
    response.writeHead(404);
    response.end();
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  let checks = 0;
  try {
    browser = await chromium.launch({headless: true, timeout: 20000});
    for (const width of [390, 1440]) {
      for (const file of files) {
        const page = await browser.newPage({viewport: {width, height: 1000}});
        const errors = [];
        apiMode = 'ok';
        logoutMethods = [];
        page.on('pageerror', error => errors.push(error.message));
        html = fs.readFileSync(path.join(__dirname, file), 'utf8')
          .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
          .replace(/<head\b[^>]*>/i, match => match + ui.script)
          .replace(/<body\b[^>]*>/i, match => match + ui.bar);
        await page.goto(origin + '/', {waitUntil: 'load'});
        const bar = page.getByRole('region', {name: 'Account'});
        assert.equal(await bar.count(), 1);
        assert.equal(await bar.isVisible(), true, file);
        const button = bar.getByRole('button', {name: 'Log out'});
        const box = await button.boundingBox();
        assert.ok(box && box.width > 0 && box.height >= 40, `${file}: accessible logout target`);
        assert.ok(box.x >= 0 && box.x + box.width <= width + 1, `${file}: logout within viewport at ${width}`);
        for (const selector of ['.acq-session-account', 'button']) {
          const unobscured = await bar.locator(selector).evaluate(element => {
            const rect = element.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
            return Boolean(hit && element.contains(hit));
          });
          assert.ok(unobscured, `${file}: ${selector} must not be obscured at ${width}px`);
        }
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
        await Promise.all([
          page.waitForURL(origin + '/login'),
          button.click(),
        ]);
        assert.deepEqual(logoutMethods, ['POST'], `${file}: exactly one logout submission`);
        assert.equal(await page.getByRole('heading', {name: 'Sign in'}).isVisible(), true);
        assert.deepEqual(errors, []);
        checks++;
        console.log(`PASS account layout and logout: ${file} at ${width}px`);
        await page.close();
      }
    }
    console.log(`Hosted account browser checks: ${checks} page/viewport cases passed`);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
