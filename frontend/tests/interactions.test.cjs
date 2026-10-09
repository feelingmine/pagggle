// State/rendering regressions, not a substitute for browser layout verification.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function app() {
  const main = {innerHTML: ''}, handlers = {};
  const element = {setAttribute() {}, hidden: true, textContent: ''};
  const document = {
    querySelector: key => key === '#main' ? main : ['#notice','#demand-nav','#start-nav'].includes(key) ? element : null,
    querySelectorAll: () => [],
    addEventListener: (name, handler) => {handlers[name] = handler;},
  };
  const context = vm.createContext({document, console, setTimeout, clearTimeout, URL, Set,
    window: {history: {replaceState() {}}, scrollTo() {}}, location: {hash: ''}});
  const source = fs.readFileSync(require.resolve('../app.js'), 'utf8');
  vm.runInContext(source.slice(0, source.indexOf('$("#modal").addEventListener')), context);
  return {main, context, handlers, run: code => vm.runInContext(code, context)};
}

test('original evidence is collapsed, complete, escaped and never translated', () => {
  const ui = app();
  const quote = 'Original <supplier> & specification. '.repeat(12);
  ui.context.fact = {citations: [{source_id: 'source-1', quote}]};
  const collapsed = ui.run('originalQuotes(fact)');
  assert.match(collapsed, /网站原文/);
  assert.doesNotMatch(collapsed, /data-original open/);
  assert.match(collapsed, /&lt;supplier&gt; &amp;/);
  assert.equal((collapsed.match(/specification\./g) || []).length, 12);
  assert.match(ui.run('state.showOriginal=true; originalQuotes(fact)'), /data-original open/);
  assert.equal(ui.context.fact.citations[0].quote, quote);
});

test('intake scope defaults and other option preserve explicit values', () => {
  const ui = app();
  const markets = ui.run('intakeSelect("market","目标市场",state.intakeMarket,marketOptions)');
  const languages = ui.run('intakeSelect("language","目标语言",state.intakeLanguage,languageOptions)');
  assert.match(markets, /value="中东" selected/);
  assert.match(languages, /value="en" selected/);
  assert.equal((markets.match(/<option /g) || []).length, 8);
  assert.equal((languages.match(/<option /g) || []).length, 9);
  const custom = ui.run('intakeSelect("market","目标市场","澳洲",marketOptions)');
  assert.match(custom, /value="other" selected/);
  assert.match(custom, /value="澳洲" required/);
});
