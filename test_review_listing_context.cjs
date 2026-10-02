// Execute the real listing renderer without outer-function locals or a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const source = fs.readFileSync(__dirname + '/review_ui.html', 'utf8');
const start = source.indexOf(' function drawListingContext(');
const end = source.indexOf(' function photoList()', start);
assert.ok(start >= 0 && end > start);
function fixture() {
  const nodes = new Map();
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {hidden: false, textContent: '', replaceChildren(...items) {this.children = items;}});
    return nodes.get(id);
  };
  const render = new Function('$','present','whole','money','dateOnly','numberValue','label','el',
    source.slice(start,end) + '\nreturn drawListingContext;')(
    $, value => value !== null && value !== undefined && value !== '',
    String, String, String, value => value == null ? null : Number(value), String,
    (tag,text,classes) => ({tag,text,classes})
  );
  return {render,$};
}
test('MLS remarks and disclosure render using the passed property', () => {
  const {render,$} = fixture();
  render({property: {mls_remarks: 'Some photos virtually staged.', synthetic_evidence: {excluded: true}}}, {}, null);
  assert.equal($('remarks-block').hidden, false);
  assert.match($('listing-remarks').textContent, /Some photos virtually staged/);
  assert.match($('listing-remarks').textContent, /Photos excluded/);
  assert.equal($('listing-context').hidden, false);
});
test('legacy metadata remarks and empty properties render without globals', () => {
  const {render,$} = fixture();
  render({property: {}}, {PublicRemarks: 'Original kitchen.'}, null);
  assert.equal($('listing-remarks').textContent, 'Original kitchen.');
  render(undefined, {}, null);
  assert.equal($('remarks-block').hidden, true);
  assert.equal($('listing-remarks').textContent, '');
});
