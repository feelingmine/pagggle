// State/rendering regressions, not a substitute for browser layout verification.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function app() {
  const main = {innerHTML: ''}, handlers = {};
  const element = {setAttribute() {}, hidden: true, textContent: ''};
  const document = {
    querySelector: key => key === '#main' ? main : ['#notice','#demand-nav','#start-nav','#planning-nav'].includes(key) ? element : null,
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
  const markets = ui.run('intakeMarketField(state.intakeMarket)');
  const languages = ui.run('intakeSelect("language","目标语言",state.intakeLanguage,languageOptions)');
  assert.match(markets, /name="market" value=""/);
  assert.match(markets, /role="combobox"[^>]*required/);
  assert.match(markets, /aria-controls="market-options"/);
  assert.match(languages, /value="en" selected/);
  assert.equal(ui.run('marketOptions.length'), 121);
  assert.equal(ui.run('marketOptions.some(([code])=>code==="uk")'), true);
  assert.equal(ui.run('marketOptions.some(([code])=>code==="gb"||code==="mobile-us")'), false);
  assert.match(languages, /value="ar"/);
  assert.match(languages, /value="pt"/);
  const selected = ui.run('intakeMarketField("us")');
  assert.match(selected, /value="美国 · US"/);
  assert.match(selected, /name="market" value="us"/);
  const custom = ui.run('intakeMarketField("澳洲")');
  assert.match(custom, /name="market" value="other"/);
  assert.match(custom, /value="澳洲" required/);
});

test('first journey has one intake path and later unlocks incremental actions', () => {
  const ui = app();
  ui.run('state.id="A"; state.data={project:{},jobs:[],profiles:[],keyword_onboarding:null}; state.view="demands"; render()');
  assert.equal(ui.run('state.view'), 'intake');
  assert.match(ui.main.innerHTML, /导入你的关键词表/);
  assert.doesNotMatch(ui.main.innerHTML, /data-kind="customer_question"|data-kind="product_change"|data-kind="intents"/);
  ui.run('state.data.keyword_onboarding={run_id:"r1",selected_group_ids:[]}; state.view="demands"; render()');
  assert.match(ui.main.innerHTML, /添加客户问题/);
  assert.match(ui.main.innerHTML, /记录产品变化/);
  ui.run('state.data={project:{},jobs:[],profiles:[],keyword_onboarding:null}; state.view="intake"; state.intakeKind="customer_question"; render()');
  assert.equal(ui.run('state.intakeKind'), 'keyword');
  assert.doesNotMatch(ui.main.innerHTML, /role="tablist"/);
});

test('coverage uses every valid keyword while filtering searches secondary members', () => {
  const ui = app();
  ui.run(`state.data={keyword_onboarding:null};
    state.demands=[{id:"a",kind:"keyword",status:"pending"},{id:"b",kind:"keyword",status:"pending"},{id:"duplicate",kind:"keyword",status:"duplicate"}];
    state.clusterData={runs:[{id:"r1",payload:{groups:[
      {target_page_id:"g1",status:"needs_review",members:[{demand_id:"a",keyword:"Silicone"},{demand_id:"b",keyword:"Industrial seal"}]}
    ]}}]};`);
  assert.equal(ui.run('clusterCoversAll(clusterRun())'), true);
  assert.equal(ui.run('state.clusterQuery="SEAL"; filteredGroups().length'), 1);
  assert.equal(ui.run('state.clusterStatus="needs_evidence"; filteredGroups().length'), 0);
  assert.equal(ui.run('clusterCoversAll(clusterRun())'), true);
  assert.equal(ui.run('state.demands.push({id:"c",kind:"keyword",status:"pending"}); clusterCoversAll(clusterRun())'), false);
});

test('import pagination renders every page without reducing the analysis input', () => {
  const ui = app();
  ui.run(`state.view="intake";
    state.demands=Array.from({length:1005},(_,i)=>({id:String(i),original:"keyword-"+i,kind:"keyword",status:"pending"}));
    state.preview={records:state.demands};`);
  for (const size of [10,50,100]) {
    const first = ui.run(`state.demandPageSize=${size};state.demandPages.intake=1;demandTable(state.preview.records)`);
    assert.equal((first.match(/<td data-label="原始需求">/g)||[]).length, size);
    assert.match(first, /共 1005 条/);
    assert.match(first, /data-direction="prev"[^>]*disabled/);
    const last = ui.run('state.demandPages.intake=9999; demandTable(state.preview.records)');
    assert.equal((last.match(/<td data-label="原始需求">/g)||[]).length, 5);
    assert.match(last, />keyword-1004<\/td>/);
    assert.match(last, /data-direction="next"[^>]*disabled/);
    assert.equal(ui.run('state.preview.records.length'), 1005);
    assert.equal(ui.run('eligibleKeywords().length'), 1005);
  }
  assert.doesNotMatch(ui.run('demandTable([])'), /<td data-label="原始需求">/);
  assert.match(ui.run('demandTable([])'), /显示 0–0 条 · 第 1 \/ 1 页/);
});
