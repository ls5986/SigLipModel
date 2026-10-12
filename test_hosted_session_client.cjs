// Execute the exact injected browser script and test fetch behavior without dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/hosted_session.py', 'utf8');
const script = source.match(/SESSION_SCRIPT = r'''<script>\n([\s\S]*?)\n<\/script>'''/)[1];

function setup(responder) {
  const calls = [], events = {}, links = [];
  const banner = {hidden: true, textContent: '', appendChild: link => links.push(link)};
  const document = {getElementById: () => banner, createElement: () => ({}), addEventListener: (n, fn) => {events[n] = fn;}};
  const window = {location: {href: 'https://studio.test/studio', origin: 'https://studio.test', reload() {}},
    addEventListener() {}, fetch: async (input, init) => {
      const url = new URL(input instanceof Request ? input.url : input, window.location.href);
      const call = {url, init, input}; calls.push(call);
      return responder(call, calls);
    }};
  vm.runInNewContext(script, {window, document, URL, Request, Headers, Response});
  return {window, calls, banner, links, events};
}
const json = (status, value) => new Response(JSON.stringify(value), {status, headers: {'content-type': 'application/json'}});
const session = token => json(200, {authenticated: true, review_token: token});

(async () => {
  let checks = 0;
  {
    const h = setup(({url}) => url.pathname === '/api/session' ? session('fresh') : json(200, {saved: true}));
    const response = await h.window.fetch('/api/studio/label', {method: 'POST', headers: {'X-Review-Token': 'stale'}, body: '{}'});
    assert.equal(h.calls[1].init.headers.get('x-review-token'), 'fresh');
    assert.equal(h.calls[1].init.body, '{}'); assert.equal((await response.json()).saved, true); checks++;
  }
  {
    let token = 'first';
    const h = setup(({url}) => {
      if (url.pathname === '/api/session') return session(token);
      token = 'second'; return json(403, {code: 'review_token_expired'});
    });
    const response = await h.window.fetch('/api/studio/train', {method: 'POST', body: '{}'});
    assert.equal(response.status, 403);
    assert.equal(h.calls.filter(c => c.url.pathname === '/api/studio/train').length, 1);
    assert.equal(h.calls.filter(c => c.url.pathname === '/api/session').length, 2);
    assert.match(h.banner.textContent, /Retry your action/); checks++;
  }
  {
    const h = setup(({url}) => url.pathname === '/api/session' ? session('fresh') : json(503, {error: 'Database unavailable', request_id: 'abc'}));
    const response = await h.window.fetch('/api/studio/queue');
    assert.deepEqual(await response.json(), {error: 'Database unavailable', request_id: 'abc'});
    assert.match(h.banner.textContent, /Reference: abc/); assert.equal(h.links.length, 0); checks++;
  }
  {
    const h = setup(({url}) => url.pathname === '/api/session' ? session('fresh') : json(401, {code: 'session_expired'}));
    const response = await h.window.fetch('/api/studio/queue');
    assert.equal(response.status, 401); assert.equal(h.links.at(-1).href, '/login'); checks++;
  }
  {
    const h = setup(({url}) => url.pathname === '/api/session' ? session('secret') : json(200, {}));
    await h.window.fetch('https://external.test/api/studio/label', {method: 'POST', body: '{}'});
    assert.equal(h.calls[1].init.headers, undefined); checks++;
  }
  {
    const h = setup(({url}) => url.pathname === '/api/session' ? session('fresh') : json(200, {}));
    await h.window.fetch(new Request('https://studio.test/api/studio/label', {method: 'POST', headers: {'X-Keep': 'yes'}, body: '{}'}));
    assert.equal(h.calls[1].init.headers.get('x-review-token'), 'fresh');
    assert.equal(h.calls[1].init.headers.get('x-keep'), 'yes'); checks++;
  }
  console.log(`Hosted session client: ${checks} checks passed`);
})().catch(error => {console.error(error); process.exitCode = 1;});
