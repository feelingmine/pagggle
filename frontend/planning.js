/* Persistent planning UI; all evidence and decisions come from the project API. */
const planning={project:null,setup:null,run:null,runId:null,filter:'',query:'',topic:null,memberPage:1,memberSize:10,detail:null,files:null};
const planActions={keep:'保留现有内容',optimize:'优化现有页面',new:'新增候选',review:'待核对'};
const planStatus={queued:'等待执行',running:'进行中',succeeded:'已完成',failed:'失败',cancelled:'已停止'};
const planSteps=['固定输入','核对网站与竞品','形成内容主题','评估页面动作','交付建议与大纲'];
async function loadPlanning(id){
  if(planning.project!==id){Object.assign(planning,{project:id,runId:null,run:null,topic:null,detail:null,files:null});}
  const setup=await api(`/projects/${id}/content-plans`);if(id!==state.id)return;
  planning.setup=setup;
  planning.runId=setup.runs.some(r=>r.id===planning.runId)?planning.runId:setup.runs[0]?.id;
  const run=planning.runId?await api(`/projects/${id}/content-plans/${planning.runId}`):null;
  if(id===state.id)planning.run=run;
}
function planLink(url,label){return /^https?:\/\//.test(url||'')?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label||url)}</a>`:esc(label||url||'—');}
function planNumber(n){return n===null||n===undefined?'未知':Number(n).toLocaleString('zh-CN');}
function renderPlanning(){
  const s=planning.setup,r=planning.run,out=r?.result||{},busy=r&&['queued','running'].includes(r.status),step=busy?Number(r.progress?.match(/^([1-5])/ )?.[1]||1):r?.status==='succeeded'?5:0;
  $('#main').innerHTML=`<div class="planning-workspace"><div class="plan-heading"><div><div class="eyebrow">从业务范围到可执行内容</div><h1>内容规划</h1><p>先找值得回答的问题，再决定保留、优化还是新增页面。</p></div><a class="text-button" href="#understanding">查看业务资料 ↗</a></div><ol class="plan-steps">${planSteps.map((x,i)=>`<li class="${step>i?'complete':''}" ${busy&&step===i+1?'aria-current="step"':''}><span>${i+1}</span>${x}</li>`).join('')}</ol>
  ${!s?.configured?'<section class="plan-card"><h2>准备项目输入</h2><p>请在本地 config.json 中配置本项目的基础词、关键词文件和竞品地址。</p></section>':`<details class="plan-card plan-inputs" ${!r?'open':''}><summary>创建规划的输入 <span>${s.files.length} 个真实文件 · ${esc(s.market?.toUpperCase())} / ${esc(s.language)} · ${esc(s.seed_file)}</span></summary><div class="plan-input-grid"><div><h3>关键词文件</h3><p class="form-help">全部读取，不限制行数。跨文件重复词合并并保留来源；不改动已有导入记录。</p>${s.files.map(f=>`<label class="plan-file"><input type="checkbox" data-plan-file value="${esc(f.name)}" ${!planning.files||planning.files.includes(f.name)?'checked':''} ${busy?'disabled':''}><span>${esc(f.name)}<small>${(f.bytes/1024/1024).toFixed(1)} MB</small></span></label>`).join('')}</div><div><h3>页面证据</h3><p>复用本站已读页面与完整 URL 清单。</p><p class="form-help">补读 ${s.own_evidence_urls.length} 个相关页面；已缓存的证据继续复用。</p><h3>竞品对照</h3>${[...new Set(s.competitor_urls.map(u=>new URL(u).origin))].map(u=>`<p>${planLink(u)}</p>`).join('')}<details><summary>查看补充采集范围</summary>${[...s.own_evidence_urls,...s.competitor_urls].map(u=>`<p class="form-help">${planLink(u)}</p>`).join('')}</details></div></div><div class="plan-toolbar"><button class="primary" data-plan="start" ${activeJob()?'disabled':''}>${r?'用当前输入创建新规划':'开始内容规划'}</button><span class="form-help">结果与证据会保存为独立版本。</span></div></details>`}
  ${r?`<div class="plan-toolbar"><label>规划版本 <select id="plan-version">${s.runs.map(x=>`<option value="${x.id}" ${x.id===r.id?'selected':''}>${formatTime(x.created_at)} · ${x.replay_of?'快照重放':'新规划'} · ${planStatus[x.status]||x.status}</option>`).join('')}</select></label>${r.status==='succeeded'?`<button data-plan="replay" ${activeJob()||r.version!==s.current_version?'disabled':''}>${r.version===s.current_version?'按快照重放':'旧规则版本（仅查看）'}</button><a class="button" href="/api/projects/${state.id}/content-plans/${r.id}/export">导出完整结果</a>`:''}</div>`:''}
  ${busy?`<section class="plan-card plan-progress" role="status"><span class="plan-pulse"></span><h2>${esc(r.progress)}</h2><p>可以离开后再回来查看进度。</p><button data-action="cancel-job" data-id="${r.job_id}">停止本次规划</button></section>`:''}
  ${r&&['failed','cancelled'].includes(r.status)?`<section class="plan-card"><h2>${r.status==='failed'?'本次规划未完成':'本次规划已停止'}</h2><p>${esc(r.error||'已保留完成的采集缓存，可展开输入后重新运行。')}</p></section>`:''}
  ${r?.status==='succeeded'?`<section class="plan-stats"><div><strong>${planNumber(out.accounting.input)}</strong><span>原始数据行</span></div><div><strong>${planNumber(out.accounting.unique)}</strong><span>去重后关键词</span></div><div><strong>${planNumber(out.summary.topics)}</strong><span>内容主题</span></div><div><strong>${out.coverage.own_read} / ${out.coverage.own_discovered}</strong><span>本站已读 / 已发现页</span></div></section><div class="plan-context">${planNumber(out.accounting.duplicate_observations)} 条重复来源已归集 · ${out.accounting.failed} 条无效行保留记录 · <button class="text-button" data-plan="deferred">${planNumber(out.summary.deferred)} 个词待归类</button> · 竞品已读 ${out.coverage.competitor_read}/${out.coverage.competitor_total} 页</div><details class="plan-card"><summary>衍生词与未覆盖的业务方向 · ${out.expansions.length} 个检索建议</summary><p class="form-help">这部分是补充搜索方向。没有命中 Semrush 原始记录的词，不带搜索量，不参与实测词的优先级排序。</p><div class="plan-expansions">${out.expansions.map(e=>`<div><strong>${esc(e.keyword)}</strong><span>${esc(e.seed)} · ${e.observed?'已有实测词':'待补关键词数据'}</span></div>`).join('')}</div></details>
  <section class="plan-card"><div class="plan-toolbar"><div class="plan-filters" aria-label="页面动作筛选"><button data-plan-filter="" class="${!planning.filter?'selected':''}">全部 ${out.summary.topics}</button>${Object.entries(planActions).map(([k,v])=>`<button data-plan-filter="${k}" class="${planning.filter===k?'selected':''}">${v} ${out.summary.actions[k]||0}</button>`).join('')}</div><label class="sr-only" for="plan-search">搜索主题或主关键词</label><input id="plan-search" type="search" placeholder="搜索主题或主关键词" value="${esc(planning.query)}"></div><p class="form-help">搜索量从高到低、KD 从低到高；指标为主题主词值，不把变体流量相加。主题是一组客户问题，独立页面需求仍需验证。</p><div id="plan-table"></div></section><details class="plan-card"><summary>结果依据与可复现记录</summary><h3>本版本固定的输入</h3>${r.inputs.files.map(f=>`<p>${esc(f.name)} · ${planNumber(f.rows)} 行</p><p class="plan-hash">${esc(f.sha256)}</p>`).join('')}<p>基础词：${r.inputs.seeds.map(s=>esc(s.keyword)).join(' · ')}</p><p>规则版本：${esc(r.version)}</p><p class="plan-hash">输入指纹 ${esc(r.input_hash)}<br>结果指纹 ${esc(r.result_hash)}</p>${r.replay_of?'<p>本次按原始快照重放；结果指纹已与原版本校验一致。</p>':''}${out.limits.map(x=>`<p>${esc(x)}</p>`).join('')}<h3>采集状态</h3>${r.evidence.map(x=>`<p>${badge(x.status)} ${planLink(x.url,x.title)} ${esc(x.error||'')}</p>`).join('')}</details>`:''}</div>`;
  if(r?.status==='succeeded')renderPlanTable();
  $('#plan-search')?.addEventListener('input',e=>{planning.query=e.target.value;renderPlanTable();});
  $('#plan-version')?.addEventListener('change',async e=>{planning.runId=e.target.value;await refresh();});
}
function renderPlanTable(){
  const rows=planning.run.topics.filter(t=>(!planning.filter||t.action===planning.filter)&&(`${t.title} ${t.primary} ${t.seed}`).toLowerCase().includes(planning.query.toLowerCase()));
  $('#plan-table').innerHTML=`<div class="table-scroll"><table class="plan-table"><thead><tr><th>主题 / 主关键词</th><th>关键词</th><th>搜索量</th><th>KD</th><th>建议动作</th><th>判断依据</th></tr></thead><tbody>${rows.map(t=>`<tr><td><button class="text-button plan-topic" data-plan="topic" data-id="${t.id}">${esc(t.title)}</button><small>${esc(t.primary)}</small></td><td>${planNumber(t.keyword_count)}</td><td>${planNumber(t.volume)}</td><td>${planNumber(t.kd)}</td><td><span class="plan-action ${t.action}">${planActions[t.action]}</span><small>${esc(t.priority)}</small></td><td>${esc(t.reason)}<small>${t.target_url?planLink(t.target_url,'现有目标页面 ↗'):'待确认承接页面'}</small></td></tr>`).join('')||'<tr><td colspan="6">没有符合条件的主题，可清空搜索或切换动作。</td></tr>'}</tbody></table></div>`;
  document.querySelectorAll('#plan-table tbody tr').forEach(row=>[...row.children].forEach((td,i)=>{td.dataset.label=['主题 / 主关键词','关键词数','搜索量','KD','建议动作','判断依据'][i];}));
}
async function showPlanDetail(topic,reset=true){
  if(reset)planning.memberPage=1;
  planning.topic=topic;
  const id=state.id,runId=planning.runId;
  const d=await api(`/projects/${id}/content-plans/${runId}/keywords?${topic?'topic='+encodeURIComponent(topic):'deferred=true'}&page=${planning.memberPage}&size=${planning.memberSize}`);
  if(id!==state.id||runId!==planning.runId)return;
  planning.detail=d;const t=d.topic;
  const body=t?`<div class="plan-detail"><span class="plan-action ${t.action}">${planActions[t.action]}</span><p>${esc(t.reason)}</p><p>${t.target_url?planLink(t.target_url):'尚未确定目标 URL'}</p><h3>为什么值得核对</h3>${t.decision_basis.map(x=>`<p>${esc(x)}</p>`).join('')}<h3>现有内容覆盖</h3>${t.requirements.map(x=>`<div class="plan-evidence"><strong>${x.covered?'找到依据':'待补内容'} · ${esc(x.title)}</strong>${x.evidence?`<blockquote>${esc(x.evidence.quote)}</blockquote>${planLink(x.evidence.url,'查看原页面')}`:'<p>当前已读正文未定位到充分依据，不能据此认定企业没有能力。</p>'}${x.competitor_examples?.length?`<details><summary>竞品参考</summary>${x.competitor_examples.map(e=>`<blockquote>${esc(e.quote)}</blockquote>${planLink(e.url)}`).join('')}<p class="form-help">竞品自述，仅供内容结构对照。</p></details>`:''}</div>`).join('')}${t.brief.optimization.length?`<h3>具体优化方向</h3><ol>${t.brief.optimization.map(x=>`<li>${esc(x.suggestion)}</li>`).join('')}</ol>`:''}${t.brief.outline.length?`<h3>${t.action==='new'?'新增内容大纲':'建议补充的页面模块'}</h3>${t.brief.outline.map(x=>`<div class="plan-evidence"><strong>${esc(x.heading)}</strong><p>${esc(x.question)}</p><p>${esc(x.instructions)}</p></div>`).join('')}<h3>读完后的下一步</h3><p>${esc(t.brief.cta)}</p>`:''}${t.candidates.length?`<details><summary>相关现有页面 ${t.candidates.length}</summary>${t.candidates.map(p=>`<p>${badge(p.status)} ${planLink(p.url,p.title)}</p>`).join('')}</details>`:''}${t.related_pages.length?`<details><summary>建议关联的业务页面</summary>${t.related_pages.map(p=>`<p>${planLink(p.url,p.title)}</p>`).join('')}</details>`:''}<p class="form-help">${esc(t.brief.publication_gate)}</p></div>`:'<p>这些词仍保留全部原始数据及归类原因，不会被悄悄丢弃。</p>';
  modal(t?.title||'待归类关键词',`${body}<h3>关键词明细 · ${planNumber(d.total)} 个</h3><div id="plan-members"></div>`,null);
  $('#modal-form').addEventListener('input',()=>{state.modalDirty=false;});
  renderPlanMembers();
}
function renderPlanMembers(){
  const d=planning.detail;
  $('#plan-members').innerHTML=`<div class="table-scroll"><table class="plan-table"><thead><tr><th>关键词</th><th>搜索量</th><th>KD</th><th>来源与归类</th></tr></thead><tbody>${d.rows.map(r=>`<tr><td>${esc(r.keyword)}</td><td>${planNumber(r.volume)}${r.volume_conflict?'（来源冲突）':''}</td><td>${planNumber(r.kd)}${r.kd_conflict?'（来源冲突）':''}</td><td>${r.reason?esc(r.reason)+'<br>':''}${r.observations.map(o=>`${esc(o.file)} · 数据行 ${o.row}`).join('<br>')}</td></tr>`).join('')}</tbody></table></div><div class="plan-toolbar"><label>每页 <select id="plan-member-size">${[10,50,100].map(n=>`<option ${n===planning.memberSize?'selected':''}>${n}</option>`).join('')}</select> 行</label><span>第 ${d.page} / ${Math.max(1,Math.ceil(d.total/d.size))} 页</span><button data-plan="member-prev" ${d.page<=1?'disabled':''}>上一页</button><button data-plan="member-next" ${d.page*d.size>=d.total?'disabled':''}>下一页</button></div>`;
  document.querySelectorAll('#plan-members tbody tr').forEach(row=>[...row.children].forEach((td,i)=>{td.dataset.label=['关键词','搜索量','KD','来源与归类'][i];}));
  $('#plan-member-size').onchange=async e=>{planning.memberSize=Number(e.target.value);await updatePlanMembers(1);};
}
async function updatePlanMembers(page){
  planning.memberPage=page;
  planning.detail=await api(`/projects/${state.id}/content-plans/${planning.runId}/keywords?${planning.topic?'topic='+planning.topic:'deferred=true'}&page=${page}&size=${planning.memberSize}`);
  renderPlanMembers();
}
document.addEventListener('change',e=>{if(e.target.matches('[data-plan-file]'))planning.files=[...document.querySelectorAll('[data-plan-file]:checked')].map(x=>x.value);});
document.addEventListener('click',async e=>{
  const filter=e.target.closest('[data-plan-filter]');if(filter){planning.filter=filter.dataset.planFilter;renderPlanning();return;}
  const b=e.target.closest('[data-plan]');if(!b||b.disabled)return;e.preventDefault();
  try{
    if(b.dataset.plan==='start'){const files=[...document.querySelectorAll('[data-plan-file]:checked')].map(x=>x.value);if(!files.length)throw new Error('至少选择一个关键词文件');b.disabled=true;const r=await api(`/projects/${state.id}/content-plans`,{files});planning.runId=r.id;await refresh();}
    else if(b.dataset.plan==='replay'){b.disabled=true;const r=await api(`/projects/${state.id}/content-plans/${planning.runId}/replay`,{});planning.runId=r.id;await refresh();}
    else if(b.dataset.plan==='topic')await showPlanDetail(b.dataset.id);
    else if(b.dataset.plan==='deferred')await showPlanDetail(null);
    else if(b.dataset.plan==='member-prev')await updatePlanMembers(planning.memberPage-1);
    else if(b.dataset.plan==='member-next')await updatePlanMembers(planning.memberPage+1);
  }catch(error){notify(error.message);b.disabled=false;}
});
