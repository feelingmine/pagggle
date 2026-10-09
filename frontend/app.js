"use strict";
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const labels = {business:"供货与定制",product:"核心产品",capability:"能力边界",scenario:"客户场景",market:"目标市场",case:"案例与产品",tone:"内容基调",contact:"询盘路径",stated:"原文陈述",inferred:"推断待核对",pending:"待确认",conflict:"来源冲突",confirmed:"企业已确认",read:"已读取",failed:"未成功读取",uncovered:"尚未覆盖",excluded:"已排除",keyword:"关键词",customer_question:"客户问题",product_change:"产品变化"};
const state = {id:"",data:null,projects:[],view:"start",startDraft:null,startDirty:false,starting:false,selectedPageIds:new Set(),pageQuery:"",sitemapFilter:"",selectionLimit:undefined,selectedFact:null,historyVersion:null,demands:[],demandPages:{},demandPageSize:10,intents:[],clusterData:null,clusterVersion:null,clusterQuery:"",clusterStatus:"",selectedGroupIds:new Set(),selectionRun:null,skipGroups:false,intakeFileName:"",intakeKind:"keyword",intakeFormat:"lines",intakeText:"",intakeMarket:"",intakeLanguage:"en",showOriginal:false,preview:null,pendingImport:null,reviewDirty:false,modalDirty:false,poll:null,generation:0};
async function api(path,data) {
  const response=await fetch("/api"+path,data===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
  const result=await response.json();if(!response.ok)throw new Error(result.detail||"暂时无法完成，请重试");return result;
}
function notify(message=""){ $("#notice").textContent=message;$("#notice").hidden=!message; }
function badge(status,text){return `<span class="badge ${esc(status)}">${esc(text||labels[status]||status)}</span>`;}
function latest(){return state.data?.profiles[0];}
function visibleProfile(){return state.data?.profiles.find(p=>p.version===state.historyVersion)||latest();}
function activeJob(){return state.data?.jobs.find(j=>["queued","running"].includes(j.status));}
function formatTime(value){return new Date(value).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});}
function field(name,label,value="",extra=""){return `<label class="field"><span>${label}</span><input name="${name}" value="${esc(value)}" ${extra}></label>`;}
function breadcrumb(current,extra=""){return `<div class="breadcrumb"><a href="#understanding">业务资料</a><span>/</span><strong>${current}</strong>${extra}</div>`;}
function closeModal(force=false){
  if(state.modalDirty&&!force){if(!$("#discard-warning"))$("#modal-content").insertAdjacentHTML("afterbegin",'<div class="unsaved-warning" id="discard-warning">有尚未保存的修改。<button type="button" data-action="discard-modal">放弃修改并关闭</button></div>');return;}
  state.modalDirty=false;$("#modal").close();
}
function modal(title,body,button,submit){
  state.modalDirty=false;
  $("#modal-content").innerHTML=`<form id="modal-form"><div class="dialog-head"><h2 id="dialog-title">${esc(title)}</h2><button type="button" class="text-button" data-action="close-modal">关闭</button></div><div class="form-body">${body}</div><div class="form-error" role="alert"></div>${button?`<div class="dialog-foot"><button type="button" data-action="close-modal">取消</button><button type="submit" class="primary">${esc(button)}</button></div>`:""}</form>`;
  $("#modal").showModal();$("#modal-form").addEventListener("input",()=>{state.modalDirty=true;});
  $("#modal-form").onsubmit=async event=>{event.preventDefault();if(!submit)return;const form=event.currentTarget,button=form.querySelector('[type="submit"]');button.disabled=true;
    try{await submit(new FormData(form));closeModal(true);await refresh();}catch(error){form.querySelector(".form-error").textContent=error.message;}finally{button.disabled=false;}};
}
async function loadProjects(preferred){
  state.projects=await api("/projects");$("#project-select").innerHTML=state.projects.length?state.projects.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join(""):'<option value="">尚未接入网站</option>';
  state.id=state.projects.some(p=>p.id===preferred)?preferred:state.projects[0]?.id||"";$("#project-select").value=state.id;
  if(state.id)localStorage.setItem("pagggle-project",state.id);await refresh();
}
async function refresh(){
  clearTimeout(state.poll);if(!state.id){render();return;}const id=state.id,generation=++state.generation;
  const data=await api(`/projects/${id}`);if(id!==state.id||generation!==state.generation)return;state.data=data;
  if(["demands","clusters","intake"].includes(state.view)){const [rows,intents,clusters]=await Promise.all([api(`/projects/${id}/demands`),data.keyword_onboarding?api(`/projects/${id}/intents`):[],api(`/projects/${id}/clusters`)]);if(id!==state.id||generation!==state.generation)return;state.demands=rows;state.intents=intents;if(clusters)state.clusterData=clusters;}
  if(!state.reviewDirty&&!(state.view==="start"&&state.startDirty)&&!state.starting&&!$("#modal").open){rememberIntake();render();}if(activeJob())state.poll=setTimeout(()=>refresh().catch(e=>notify(e.message)),1800);
}
function render(){
  if(state.data&&initialKeywordJourney()){
    if(state.view==="demands")state.view=eligibleKeywords().length?"clusters":"intake";
    if(state.view==="clusters"&&!eligibleKeywords().length)state.view="intake";
    if(["intake","clusters"].includes(state.view)){state.intakeKind="keyword";window.history.replaceState(null,"",`#${state.view}`);}
  }
  $("#demand-nav").textContent=initialKeywordJourney()?"关键词分析":"内容需求";
  document.title=`pagggle · ${{start:"网站优化",analysis:"执行进度",selection:"筛选页面",understanding:"业务理解",sources:"资料与页面",demands:"内容需求",clusters:"关键词分析",intake:initialKeywordJourney()?"导入关键词":"添加内容需求"}[state.view]||"业务理解"}`;
  $("#demand-nav").setAttribute("aria-current",["demands","intake","clusters"].includes(state.view)?"page":"false");
  $("#start-nav").setAttribute("aria-current",["start","analysis","selection"].includes(state.view)?"page":"false");
  if(state.view==="start"||!state.id){renderOnboarding();return;}if(!state.data)return;
  if(state.view==="selection")renderSelection();else if(state.view==="analysis")renderAnalysis();else if(state.view==="sources")renderSources();else if(state.view==="demands")renderDemands();else if(state.view==="clusters")renderClusters();else if(state.view==="intake")renderIntake();else if(!latest())renderAnalysis();else renderUnderstanding();
}
function renderOnboarding(){
  const draft=state.startDraft||{site_url:state.data?.project.site_url||"",name:"",limit_mode:"unlimited",crawl_max_pages:""};
  $("#main").innerHTML=`<div class="start-layout"><section class="start-main"><div class="eyebrow">网站优化 · 第一步</div><h1>先完整认识你的网站</h1><p class="start-intro">输入你的网站，先通过 sitemap 查看页面清单。由你筛选要研究的页面，再采集正文、核对业务理解。</p><form id="onboarding-form">${field("site_url","你的网站地址",draft.site_url,'type="url" required maxlength="2000" placeholder="https://www.example.com"')}${field("name","项目名称（新网站选填）",draft.name,'maxlength="120" placeholder="例如：企业英文站"')}<label class="field"><span>采集网页数量</span><select name="limit_mode" id="crawl-limit-mode"><option value="unlimited" ${draft.limit_mode==="unlimited"?"selected":""}>不限数量（默认）</option><option value="custom" ${draft.limit_mode==="custom"?"selected":""}>指定数量上限</option></select></label><div id="crawl-limit-field" ${draft.limit_mode==="custom"?"":"hidden"}>${field("crawl_max_pages","最多读取多少页",draft.crawl_max_pages,'type="number" min="1" step="1" placeholder="填写正整数"')}</div><p class="form-help">该参数限制你下一步选中的采集页数，默认不限。现在只发现 sitemap 链接，不提取网页正文，也不调用模型。</p><div class="inline-error" role="alert"></div><div class="form-footer"><button class="accent-button" type="submit" ${activeJob()||state.starting?"disabled":""}>发现站点页面</button></div></form>${state.id?`<div class="start-return">${activeJob()?'<a href="#analysis">查看正在进行的分析</a>':latest()?'<a href="#understanding">查看上次业务理解</a>':'<a href="#analysis">查看上次执行结果</a>'}<a href="#sources">查看已采集页面</a></div>`:""}</section><aside class="start-guide"><h2>这一次，我们会一起完成</h2><ol><li><strong>查看 sitemap 页面清单</strong><p>先发现链接，了解网站的栏目和规模。</p></li><li><strong>筛选后采集选中页面</strong><p>按 sitemap 或路径搜索，勾选本轮需要的页面。</p></li><li><strong>核对后再生成业务理解</strong><p>检查采集结果，再手动发起全文分析。</p></li></ol><p class="form-help">同一网址继续使用原项目与历史版本；更换网址会创建独立项目。</p></aside></div>`;
  const form=$("#onboarding-form"),limit=form.elements.crawl_max_pages;
  const sync=()=>{limit.required=form.elements.limit_mode.value==="custom";limit.disabled=!limit.required;$("#crawl-limit-field").hidden=!limit.required;};sync();
  form.oninput=()=>{sync();state.startDraft=Object.fromEntries(new FormData(form));state.startDirty=true;};
  form.onchange=form.oninput;
  form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('[type="submit"]');button.disabled=true;state.starting=true;
    try{
      const data=Object.fromEntries(new FormData(form)),url=new URL(data.site_url);url.hash="";url.search="";
      const site_url=url.href,limit=data.limit_mode==="custom"?Number(data.crawl_max_pages):null;
      let project=state.projects.find(p=>p.site_url===site_url);
      if(!project){project=await api("/projects",{site_url,name:data.name.trim()||url.hostname});state.projects.push(project);}
      await api(`/projects/${project.id}/jobs`,{kind:"discover",crawl_max_pages:limit});
      state.startDirty=false;state.startDraft=null;state.starting=false;state.historyVersion=null;state.selectedFact=null;state.selectedPageIds.clear();state.selectionLimit=limit;state.pageQuery="";state.sitemapFilter="";state.view="analysis";window.history.replaceState(null,"","#analysis");
      await loadProjects(project.id);notify("页面发现任务已提交，下一步由你筛选页面。");window.scrollTo(0,0);
    }catch(error){form.querySelector(".inline-error").textContent=error.message;button.disabled=false;state.starting=false;}
  };
}
function coverageMarkup(profile=null){
  const pages=state.data.sources.filter(s=>s.kind==="page"),counts={read:0,failed:0,uncovered:0,excluded:0};
  for(const s of pages)counts[s.status]=(counts[s.status]||0)+1;
  let html=`<div class="coverage-summary"><span>已发现 <strong>${pages.length}</strong> 页</span><span>已读取 ${counts.read}</span><span>失败 ${counts.failed}</span><span>未覆盖 ${counts.uncovered}</span><span>已排除 ${counts.excluded}</span></div>`;
  if(profile){
    const run=state.data.model_runs.find(r=>r.job_id===profile.analysis_job_id),usage=run?.usage;
    const unread=state.data.sources.filter(s=>s.kind!=="discovery"&&s.status==="read"&&!usage?.source_ids?.includes(s.id)).length;
    html+=`<p class="coverage-note">${usage?.coverage==="full_stored_text"?`此版本已全文分析 ${usage.source_count} 份已存来源，共 ${usage.batch_count} 批。${unread?`另有 ${unread} 份已读取来源未纳入此版本。`:""}`:"此历史版本未记录全文覆盖，不能作为完整站点理解。"}${counts.failed||counts.uncovered?" 仍有失败或未覆盖页面，请先核对范围。":" 已发现链接的处理结果不代表网站不存在其他页面。"} <a href="#start">调整范围并重新分析</a></p>`;
  }
  return html;
}
function jobMarkup(kind=null){
  const jobs=kind?state.data.jobs.filter(j=>j.kind===kind):state.data.jobs,job=jobs.find(j=>["queued","running"].includes(j.status))||jobs[0];if(!job)return "";const status={queued:"等待分析",running:"正在处理",succeeded:"已完成",failed:"需要处理",cancelled:"已取消"}[job.status];
  return `<div class="task-status ${job.status}"><div><strong>${status}</strong><p>${esc(job.error||job.progress)}</p>${job.error?.includes("SiteGround 人机验证")?`<p><a class="accent-link" href="${esc(new URL("/robots.txt",state.data.project.site_url).href)}" target="_blank" rel="noopener noreferrer">在浏览器打开站点验证</a> · 完成后重试这一步；若仍被拦截，请联系站点管理员。</p>`:""}</div>${["queued","running"].includes(job.status)?`<button data-action="cancel-job" data-id="${job.id}">取消任务</button>`:job.status==="failed"?`<button data-action="run-job" data-kind="${esc(job.kind)}" data-job="${job.id}">重试这一步</button>`:""}</div>`;
}
function renderAnalysis(){
  const active=activeJob(),job=active||state.data.jobs[0],profile=latest();
  const scopeJob=state.data.jobs.find(j=>["discover","analyze","crawl"].includes(j.kind)),limit=scopeJob?.options?.crawl_max_pages;
  const done=job?.status==="succeeded"&&job.kind==="understand"&&!!profile,discovered=job?.kind==="discover",collected=job?.kind==="crawl"&&job.status==="succeeded";
  const selected=job?.options?.source_ids||[],read=state.data.sources.filter(s=>s.status==="read"&&selected.includes(s.id));
  $("#main").innerHTML=breadcrumb("执行进度")+`<div class="content-page analysis-page"><div class="eyebrow">${esc(state.data.project.name)}</div><h1>${active?(discovered?"正在发现站点页面":"正在执行你选择的步骤"):done?"业务理解已生成，等待你核对":collected?"选中页面已采集，请先检查结果":discovered?"先筛选页面，再采集内容":"网站分析工作区"}</h1><p class="start-intro">${esc(state.data.project.site_url)}</p><p class="form-help">本轮采集上限：${scopeJob?(limit==null?"不限数量":`${limit} 页`):"尚未设置"}。不会自动选中全部页面。</p><ol class="analysis-steps" aria-label="网站分析步骤"><li class="${discovered&&active?"current":""}">发现链接</li><li class="${discovered&&!active?"current":""}">筛选页面</li><li class="${job?.kind==="crawl"?"current":""}">采集与核对</li><li class="${job?.kind==="understand"?"current":""}">业务理解</li></ol>${jobMarkup()}${coverageMarkup()}<div class="page-actions">${!active?discovered?'<a class="button-link primary" href="#selection">筛选要采集的页面</a>':collected?`<a class="button-link primary" href="#sources">先查看采集结果</a>${read.length?`<button data-action="generate-selected" data-job="${job.id}">根据这 ${read.length} 页生成业务理解</button>`:""}`:done?'<a class="button-link primary" href="#understanding">核对业务理解</a>':'<a class="button-link primary" href="#start">从网站地址开始</a>':""}<a class="accent-link" href="#sources">页面清单与失败原因</a>${profile&&!done?'<a class="accent-link" href="#understanding">查看已保存版本</a>':""}</div><p class="data-note">发现链接不读取正文；采集只读取你选中的页面，完成后等待你检查。业务理解需要另行发起。</p></div>`;
}
function sitemapGroups(){
  return state.data.sources.filter(s=>s.kind==="discovery"&&s.status==="read"&&s.title==="站点地图").flatMap(s=>{
    const doc=new DOMParser().parseFromString(s.body,"application/xml");if(doc.documentElement.localName!=="urlset")return [];
    const urls=new Set([...doc.documentElement.children].filter(e=>e.localName==="url").flatMap(e=>[...e.children].filter(c=>c.localName==="loc").map(c=>c.textContent.trim())));
    return [{id:s.id,url:s.url,urls}];
  });
}
function selectionRows(){
  const group=sitemapGroups().find(g=>g.id===state.sitemapFilter),query=state.pageQuery.trim().toLowerCase();
  return state.data.sources.filter(s=>s.kind==="page"&&(!group||group.urls.has(s.url))&&(!query||(s.url+" "+s.title).toLowerCase().includes(query)));
}
function renderSelection(){
  const scope=state.data.jobs.find(j=>["discover","crawl"].includes(j.kind));
  if(state.selectionLimit===undefined&&scope?.options?.crawl_max_pages!=null)state.selectionLimit=scope.options.crawl_max_pages;
  if(state.selectionLimit===undefined)state.selectionLimit=null;
  const groups=sitemapGroups();
  $("#main").innerHTML=breadcrumb("筛选页面")+`<div class="content-page"><div class="page-title"><div><h1>这一轮，你想先了解哪些页面？</h1><p>按 sitemap 或网址路径筛选，再勾选需要的页面。默认不勾选任何页面。</p></div><a href="#start" class="accent-link">返回网站设置</a></div>${jobMarkup()}<div class="selection-filters"><label class="field"><span>站点地图</span><select id="sitemap-filter"><option value="">所有已发现链接</option>${groups.map(g=>`<option value="${g.id}" ${g.id===state.sitemapFilter?"selected":""}>${esc(new URL(g.url).pathname)}（${g.urls.size}）</option>`).join("")}</select></label>${field("page_query","搜索网址或路径",state.pageQuery,'id="page-query" type="search" placeholder="例如 product、about、contact"')}${field("selection_limit","本轮采集上限（留空不限）",state.selectionLimit??"",'id="selection-limit" type="number" min="1" step="1"')}</div><p class="form-help">清单来自 sitemap、输入网址及已有发现记录。筛选只使用网址信息；已有正文和历史版本会保留。</p><div class="page-actions"><button data-action="select-visible">选择当前筛选结果</button><button data-action="clear-pages">清空选择</button><span id="selection-count" role="status" aria-live="polite"></span></div><div id="selection-rows"></div><div class="selection-footer"><span id="selection-help"></span><button class="accent-button" data-action="collect-selected" id="collect-selected">采集选中页</button></div></div>`;
  $("#page-query").oninput=event=>{state.pageQuery=event.target.value;updateSelectionRows();};
  $("#sitemap-filter").onchange=event=>{state.sitemapFilter=event.target.value;updateSelectionRows();};
  $("#selection-limit").oninput=event=>{state.selectionLimit=event.target.value===""?null:Number(event.target.value);updateSelectionSummary();};
  updateSelectionRows();
}
function updateSelectionRows(){
  const rows=selectionRows();
  $("#selection-rows").innerHTML=rows.length?`<div class="table-scroll selection-table"><table><thead><tr><th>选择</th><th>页面网址</th><th>采集状态</th></tr></thead><tbody>${rows.map(s=>`<tr><td data-label="选择"><input type="checkbox" data-source-check="${s.id}" aria-label="选择 ${esc(s.url)}" ${state.selectedPageIds.has(s.id)?"checked":""} ${s.status==="excluded"||activeJob()?"disabled":""}></td><td data-label="页面网址">${esc(s.url)}</td><td data-label="采集状态">${s.status==="uncovered"?"待采集":esc(labels[s.status])}</td></tr>`).join("")}</tbody></table></div>`:'<div class="empty-state"><p>没有匹配的页面。请调整筛选条件，或查看站点地图的读取结果。</p><a href="#sources">查看发现记录</a></div>';
  $("#selection-rows").onchange=event=>{const id=event.target.dataset.sourceCheck;if(!id)return;event.target.checked?state.selectedPageIds.add(id):state.selectedPageIds.delete(id);updateSelectionSummary();};
  updateSelectionSummary();
}
function updateSelectionSummary(){
  const count=state.selectedPageIds.size,limit=state.selectionLimit,invalid=limit!==null&&(!Number.isSafeInteger(limit)||limit<1),over=limit!==null&&count>limit;
  $("#selection-count").textContent=`筛选匹配 ${selectionRows().length} 页 · 已选择 ${count} 页`;
  $("#selection-help").textContent=invalid?"请输入正整数，或留空不限。":over?"选择数量超过上限，请减少选择或调整上限。":"采集完成后先查看原文；不会自动生成业务理解。";
  $("#collect-selected").textContent=`采集选中 ${count} 页`;
  $("#collect-selected").disabled=!count||invalid||over||!!activeJob();
}
function originalQuotes(fact){
  return `<details class="original-quotes" data-original ${state.showOriginal?"open":""}><summary>网站原文 · ${fact.citations.length} 条依据</summary>${fact.citations.map(ref=>`<blockquote>${esc(ref.quote)}</blockquote><button class="text-button accent-link" data-action="source" data-id="${esc(ref.source_id)}">查看完整来源</button>`).join("")}</details>`;
}
function renderUnderstanding(){
  const profile=visibleProfile(),isHistory=profile.version!==latest().version,facts=profile.payload.facts;
  const categories=["product","business","capability","scenario","market","case","tone","contact"].filter(c=>facts.some(f=>f.category===c));
  const pending=facts.map((fact,index)=>({fact,index})).filter(({fact})=>fact.status!=="confirmed");
  if(!pending.some(p=>p.index===state.selectedFact))state.selectedFact=pending[0]?.index??null;
  const sources=state.data.sources.filter(s=>s.status==="read"&&s.kind!=="discovery"),citationIds=[...new Set(facts.flatMap(f=>f.citations.map(c=>c.source_id)))];
  let html=breadcrumb("业务理解",badge(profile.status,profile.status==="confirmed"?"版本已核对":isHistory?`历史版本 v${profile.version}`:"待核对版本"));
  html+='<div class="understanding-layout"><nav class="outline-nav" aria-label="业务理解章节"><a class="selected" href="#document-top" data-scroll="document-top">我们对你业务的理解</a>';
  html+=categories.map(c=>`<a href="#section-${c}" data-scroll="section-${c}">${esc(labels[c])}</a>`).join("");html+='<div class="nav-divider"></div><a href="#sources">资料与页面</a><a href="#history" data-action="history">历史版本</a></nav>';
  html+='<article class="document" id="document-top"><h1>我们对你业务的理解</h1><p class="document-intro">请先核对关键业务；不确定的信息可以稍后补充。</p>';
  html+=`<div class="coverage-line"><span>已读取 ${sources.length} 份页面与业务资料</span><a class="accent-link" href="#sources">查看页面范围</a><a class="accent-link" href="#analysis">分析进度</a></div>`+coverageMarkup(profile);
  if(activeJob()||state.data.jobs[0]?.status==="failed")html+=jobMarkup();if(isHistory)html+=`<div class="notice">正在查看历史版本 v${profile.version}。<button class="text-button" data-action="current-version">返回当前版本</button></div>`;
  html+=`<button class="text-button original-toggle" data-action="toggle-original" aria-pressed="${state.showOriginal}">${state.showOriginal?"收起网站原文":"显示网站原文"}</button>`;
  html+=categories.map(category=>`<section class="business-section" id="section-${category}"><h2>${esc(labels[category])}</h2>${facts.map((fact,index)=>fact.category===category?`<div class="fact ${index===state.selectedFact?"selected":""}"><div class="fact-line"><p>${esc(fact.statement)}${fact.citations.map(c=>`<button class="source-marker" data-action="source" data-id="${esc(c.source_id)}" aria-label="查看来源 ${citationIds.indexOf(c.source_id)+1}">[${citationIds.indexOf(c.source_id)+1}]</button>`).join("")}</p>${!isHistory?`<div class="fact-actions">${fact.status!=="confirmed"?`<button class="${index===state.selectedFact?"accent-button":"text-button"}" data-action="focus-review" data-index="${index}">核对</button>`:""}<button class="text-button" data-action="edit-fact" data-index="${index}">修改</button></div>`:""}</div><div class="fact-meta">${esc(fact.scope||"适用范围待确认")} · ${esc(labels[fact.status])}</div>${originalQuotes(fact)}</div>`:"").join("")}</section>`).join("");
  html+=`<section class="source-footnotes"><details data-original ${state.showOriginal?"open":""}><summary>网站来源 · ${citationIds.length} 份</summary>`;
  html+=citationIds.map((id,index)=>{const source=state.data.sources.find(s=>s.id===id);return `<p><button class="source-marker" data-action="source" data-id="${esc(id)}">[${index+1}]</button><button class="text-button accent-link" data-action="source" data-id="${esc(id)}">${esc(source?.title||"来源资料")}</button></p>`;}).join("");
  html+='</details>';
  html+=`</section><div class="document-footer"><div><div class="save-note">业务理解 v${profile.version} · ${profile.status==="confirmed"?"版本已核对":"等待核对"}</div><div class="save-note">最近保存：${formatTime(profile.updated_at)}</div></div>${!isHistory?`<button class="primary" data-action="finish-review">${initialKeywordJourney()?"下一步：导入关键词":"下一步：内容需求"}</button>`:""}</div></article>`;
  html+=`<aside class="review-panel" id="review-panel" aria-label="业务核对"><h2>需要你核对<span>${pending.length}</span></h2><p class="review-intro">请结合实际情况，确认以下关键业务信息。</p>`;
  if(!pending.length)html+='<div class="review-item"><p>当前主张已逐项确认。</p><p class="review-help">仍可修改业务理解，或继续下一步。</p></div>';
  html+=pending.map(({fact,index},position)=>`<section class="review-item ${index===state.selectedFact?"active":""}"><button class="review-item-head" data-action="select-review" data-index="${index}" aria-expanded="${index===state.selectedFact}"><span class="review-number">${position+1}</span><span class="review-label">${esc(labels[fact.category])}<span class="review-subtitle">${esc(fact.statement)}</span></span></button>${index===state.selectedFact&&!isHistory?`<form class="review-form" id="review-form" data-index="${index}"><label class="radio-option"><input type="radio" name="decision" value="confirmed" required>信息正确，可以确认</label><label class="radio-option"><input type="radio" name="decision" value="edit" required>需要修改</label><label class="radio-option"><input type="radio" name="decision" value="pending" required>暂时无法确认</label><div id="review-edit" hidden><label class="field"><span>正确的业务描述</span><textarea name="statement" maxlength="3000">${esc(fact.statement)}</textarea></label></div><details class="review-help"><summary>补充说明（选填）</summary><label class="field"><span class="sr-only">补充说明</span><input name="reason" maxlength="1000" placeholder="例如：已与产品负责人核对"></label></details><div class="inline-error" role="alert"></div><button class="accent-button" type="submit">保存此项</button><button class="text-button" type="button" data-action="discard-review" hidden>撤销本次选择</button></form>`:""}</section>`).join("");
  if(profile.payload.gaps.length)html+=`<section class="review-gaps"><h3>待补充的资料</h3>${profile.payload.gaps.map(g=>`<p>${esc(g)}</p>`).join("")}<button data-action="add-source">补充资料</button></section>`;
  html+='</aside></div>';$("#main").innerHTML=html;
  const form=$("#review-form");if(form){form.oninput=()=>{state.reviewDirty=true;form.querySelector('[data-action="discard-review"]').hidden=false;$("#review-edit").hidden=new FormData(form).get("decision")!=="edit";};
    form.onsubmit=async event=>{event.preventDefault();const data=new FormData(form),button=form.querySelector('[type="submit"]');button.disabled=true;const index=Number(form.dataset.index),decision=data.get("decision");
      try{await api(`/projects/${state.id}/profiles/review`,{expected_version:profile.version,fact_index:index,decision:decision==="pending"?"pending":"confirmed",...(decision==="edit"?{statement:data.get("statement")}:{}),reason:data.get("reason").trim()||(decision==="pending"?"用户暂时无法确认，保留待核对":"用户逐项核对业务信息")});state.reviewDirty=false;state.selectedFact=pending.find(p=>p.index>index)?.index??pending.find(p=>p.index<index)?.index??null;await refresh();notify(decision==="pending"?"已保留待确认状态，可稍后补充。":"已保存此项，旧版本和原始依据均保留。");
      }catch(error){form.querySelector(".inline-error").textContent=error.message;button.disabled=false;}};}
}
function renderSources(){
  const sources=state.data.sources;
  $("#main").innerHTML=breadcrumb("资料与页面")+`<div class="content-page"><div class="page-title"><div><h1>资料与页面</h1><p>读取结果和原始依据分开保留。失败不代表企业没有相关业务。</p></div><div class="page-actions"><button data-action="add-source">补充资料</button><a class="button-link primary" href="#selection">筛选采集页面</a><a class="accent-link" href="#analysis">返回执行进度</a></div></div>${jobMarkup()}${coverageMarkup()}${sources.length?sources.map(s=>`<article class="source-row"><header><h3>${esc(s.title)}</h3>${badge(s.status)}</header><p class="source-url">${s.kind==="manual"?"手动补充":"网站读取"} · ${formatTime(s.updated_at)}${s.url?`<br>${esc(s.url)}`:""}</p>${s.error?`<p class="inline-error">${esc(s.error)}</p>`:""}${s.body?`<details><summary>查看原始内容</summary><pre class="source-body">${esc(s.body)}</pre></details>`:""}</article>`).join(""):'<div class="empty-state"><h2>还没有业务资料</h2><p>读取网站或补充文本资料后，开始核对业务理解。</p><button class="primary" data-action="add-source">补充第一份资料</button></div>'}</div>`;
}
const intentLabels={ready:"已识别",needs_review:"待解释",unrelated:"无关输入",failed:"分析失败"};
const taskLabels={find_supplier:"寻找供应商",customize_product:"定制产品",compare_options:"比较方案",check_spec:"核对规格",troubleshoot:"解决故障",learn_concept:"了解概念",other:"其他任务"};
function demandTableRows(rows){return `<div class="table-scroll"><table><thead><tr><th>原始需求</th><th>类型</th><th>搜索量</th><th>KD</th><th>SERP</th><th>状态</th></tr></thead><tbody>${rows.map(r=>{const result=state.view==="demands"?state.intents.find(i=>i.demand_id===r.id):null;return `<tr><td data-label="原始需求">${esc(r.original)}${r.error?`<div class="inline-error">${esc(r.error)}</div>`:""}</td><td data-label="类型">${esc(labels[r.kind])}</td><td data-label="搜索量">${r.volume??"未知"}</td><td data-label="KD">${r.kd??"未知"}</td><td data-label="SERP">${r.serp?.length?`${r.serp.length} 个 URL`:"未提供"}</td><td data-label="状态">${result?`<button class="text-button" data-action="show-intent" data-id="${r.id}">${esc(intentLabels[result.status])}</button>${result.profile_version!==latest()?.version?'<div class="inline-error">业务理解已更新，需复核</div>':""}`:esc(state.view==="clusters"&&r.status==="pending"&&clusterRun()?.payload.groups.some(g=>g.members.some(m=>m.demand_id===r.id))?"已纳入分析":{pending:state.view==="demands"?"待识别意图":"待分析",duplicate:"重复记录",failed:"需修正"}[r.status])}</td></tr>`;}).join("")}</tbody></table></div>`;}
function demandTable(rows){
  const size=state.demandPageSize,pages=Math.max(1,Math.ceil(rows.length/size)),page=Math.min(Math.max(1,state.demandPages[state.view]||1),pages),start=(page-1)*size;
  state.demandPages[state.view]=page;
  return `<section data-demand-list aria-label="导入记录"><nav class="demand-pagination" aria-label="导入记录分页"><label>每页显示 <select data-demand-page-size aria-label="每页显示行数">${[10,50,100].map(n=>`<option value="${n}" ${n===size?"selected":""}>${n} 行</option>`).join("")}</select></label><span role="status">共 ${rows.length} 条 · 显示 ${rows.length?start+1:0}–${Math.min(start+size,rows.length)} 条 · 第 ${page} / ${pages} 页</span><div class="pagination-actions">${[["first","首页",1,page===1],["prev","上一页",page-1,page===1],["next","下一页",page+1,page===pages],["last","末页",pages,page===pages]].map(([direction,label,target,disabled])=>`<button type="button" data-action="demand-page" data-direction="${direction}" data-page="${target}" ${disabled?"disabled":""}>${label}</button>`).join("")}</div></nav>${demandTableRows(rows.slice(start,start+size))}</section>`;
}
function refreshDemandTable(){
  const host=$("[data-demand-list]");if(!host)return;
  const rows=state.view==="intake"?state.preview.records:state.view==="clusters"?state.demands.filter(r=>r.kind==="keyword"):state.demands;
  host.outerHTML=demandTable(rows);
}
function renderDemands(){
  const rows=state.demands,pending=rows.filter(r=>r.status==="pending"&&!state.intents.some(i=>i.demand_id===r.id&&i.status!=="failed")).length;
  const counts={"待识别意图":rows.filter(r=>r.status==="pending"&&!state.intents.some(i=>i.demand_id===r.id)).length,"重复":rows.filter(r=>r.status==="duplicate").length,"导入失败":rows.filter(r=>r.status==="failed").length};
  for(const [status,label] of Object.entries(intentLabels))counts[label]=state.intents.filter(i=>i.status===status).length;
  $("#main").innerHTML=breadcrumb("内容需求")+`<div class="content-page"><div class="page-title"><div><h1>持续补充内容需求</h1><p>首次关键词筛选已完成。接下来从客户问题和产品变化补充内容规划。</p></div><div class="page-actions"><a class="button-link" href="#clusters">查看关键词分析</a><button data-action="open-intake" data-kind="keyword">补充关键词</button><button class="primary" data-action="open-intake" data-kind="customer_question">添加客户问题</button><button data-action="open-intake" data-kind="product_change">记录产品变化</button></div></div>${jobMarkup()}${rows.length?`<div class="summary-line"><span>共 <strong>${rows.length}</strong> 条输入</span>${Object.entries(counts).filter(([,n])=>n).map(([label,n])=>`<span>${n} 条${label}</span>`).join("")}</div>${demandTable(rows)}<div class="page-actions">${pending?(latest()?.status==="confirmed"?`<button class="primary" data-action="run-job" data-kind="intents" ${activeJob()?"disabled":""}>${state.intents.some(i=>i.status==="failed")?"重试失败及未分析项":"识别需求意图"}</button>`:'<a class="accent-link" href="#understanding">先核对业务理解，再识别需求意图</a>'):""}</div><p class="data-note">点击结果查看候选解释与原词依据。歧义保留待解释；关键词聚类提供独立候选分组，正式页面建议仍需审核。</p>`:'<div class="empty-state"><h2>接下来，添加你想覆盖的内容需求</h2><p>有关键词表可以导入；没有也可以从客户经常问的一个问题开始。</p><button class="primary" data-action="open-intake">添加关键词或客户问题</button></div>'}</div>`;
}
function initialKeywordJourney(){return !state.data?.keyword_onboarding;}
function eligibleKeywords(){return state.demands.filter(r=>r.kind==="keyword"&&r.status==="pending");}
function keywordJourney(step){return `<ol class="analysis-steps keyword-steps" aria-label="首次关键词分析步骤">${["导入关键词","分析全部关键词","筛选并完成"].map((label,index)=>`<li class="${step===index+1?"current":""}" ${step===index+1?'aria-current="step"':""}>${label}</li>`).join("")}</ol>`;}
function clusterRun(){return (initialKeywordJourney()?null:state.clusterData?.runs.find(r=>r.version===state.clusterVersion))||state.clusterData?.runs[0];}
function clusterCoversAll(run){
  const expected=eligibleKeywords().map(r=>r.id),actual=run?.payload.groups.flatMap(g=>g.members.map(m=>m.demand_id))||[];
  return expected.length>0&&actual.length===expected.length&&new Set(actual).size===actual.length&&expected.every(id=>actual.includes(id));
}
function filteredGroups(){
  const query=state.clusterQuery.trim().toLocaleLowerCase();
  return (clusterRun()?.payload.groups||[]).filter(g=>(!query||g.members.some(m=>m.keyword.toLocaleLowerCase().includes(query)))&&(!state.clusterStatus||g.status===state.clusterStatus)).sort((a,b)=>{
    const x=a.members[0],y=b.members[0];
    return (x.volume==null)-(y.volume==null)||(y.volume??0)-(x.volume??0)||(x.kd==null)-(y.kd==null)||(x.kd??0)-(y.kd??0);
  });
}
function renderClusters(){
  const first=initialKeywordJourney(),eligible=eligibleKeywords(),run=clusterRun(),data=run?.payload,settings=state.clusterData?.settings,covered=clusterCoversAll(run);
  if(state.selectionRun!==run?.id){state.selectionRun=run?.id;state.selectedGroupIds=new Set();state.skipGroups=false;}
  const counts={duplicate:state.demands.filter(r=>r.kind==="keyword"&&r.status==="duplicate").length,failed:state.demands.filter(r=>r.kind==="keyword"&&r.status==="failed").length};
  $("#main").innerHTML=breadcrumb(first?"首次关键词分析":"关键词分析")+`<div class="content-page">${first?keywordJourney(covered?3:2):""}<div class="page-title"><div><h1>${covered?"筛选值得继续研究的关键词":"分析你的全部关键词"}</h1><p>${covered?"搜索任意组内关键词，查看成员和依据，再保留有价值的候选组。":"先整理语义主题，再核对已有搜索结果；全部有效关键词都会参与。"}</p></div>${first?"":'<a class="accent-link" href="#demands">返回内容需求</a>'}</div>${!first||!covered?`<div class="page-actions"><button class="primary" data-action="run-job" data-kind="clusters" ${!eligible.length||activeJob()?"disabled":""}>${run?"重新分析全部关键词":"分析全部关键词"}</button><button data-action="open-intake" data-kind="keyword">${first?"继续补充关键词":"补充关键词"}</button></div>`:""}<p class="form-help">共 ${eligible.length} 条有效关键词 · ${counts.duplicate} 条重复 · ${counts.failed} 条导入失败。缺少搜索结果的词仍会分析，并标记待补证据。</p>${jobMarkup("clusters")}${run?`${!first?`<div class="cluster-toolbar"><label class="field"><span>结果版本</span><select id="cluster-version">${state.clusterData.runs.map(r=>`<option value="${r.version}" ${r.version===run.version?"selected":""}>v${r.version} · ${esc(formatTime(r.created_at))} · ${r.payload.summary.keywords} 词</option>`).join("")}</select></label></div>`:""}<p class="coverage-note">此版本已分析 ${data.summary.keywords} 个词，形成 ${data.summary.groups} 个候选组。${data.groups.some(g=>g.basis==="semantic_only")?`${data.groups.filter(g=>g.basis==="semantic_only").length} 个组按语义聚合，同页关系待验证。`:""}${covered?"当前全部有效关键词均已纳入。":"有新增或未覆盖的关键词，请重新分析后再完成。"}</p><div class="field-row cluster-filters"><label class="field"><span>检索关键词或组内成员</span><input id="cluster-query" type="search" value="${esc(state.clusterQuery)}" placeholder="例如：silicone"></label><label class="field"><span>核对状态</span><select id="cluster-status"><option value="">全部状态</option><option value="needs_review" ${state.clusterStatus==="needs_review"?"selected":""}>待审核</option><option value="needs_evidence" ${state.clusterStatus==="needs_evidence"?"selected":""}>待补证据</option></select></label></div>${first?'<div class="page-actions"><button data-action="select-visible-groups">选择当前筛选结果</button><button data-action="clear-groups">清空选择</button></div>':""}<p class="form-help" id="cluster-filter-count" aria-live="polite"></p><div id="cluster-results"></div>${first?`<div class="selection-footer"><div><p id="group-selection-count" aria-live="polite"></p><label class="radio-option"><input id="skip-groups" type="checkbox" ${state.skipGroups?"checked":""}>本轮暂不保留候选组</label><p class="form-help">保存的是后续研究候选，不会自动创建页面或生成内容。</p></div><button class="primary" data-action="complete-keywords" ${!covered||activeJob()?"disabled":""}>保存筛选，完成首次分析</button></div>`:state.data.keyword_onboarding?.run_id===run.id?`<p class="coverage-note">首次筛选已完成，保留 ${state.data.keyword_onboarding.selected_group_ids.length} 个候选组。原筛选记录保留。</p>`:""}`:'<div class="empty-state"><h2>关键词已就绪</h2><p>点击“分析全部关键词”，完成后在这里筛选结果。</p></div>'}<details class="cluster-method"><summary>查看导入对账（重复和失败均保留）</summary>${demandTable(state.demands.filter(r=>r.kind==="keyword"))}${counts.failed?'<p class="inline-error">失败记录未参与分析，请修正后重新导入；原记录保留。</p>':""}${first?'<button data-action="open-intake" data-kind="keyword">补充或修正关键词</button>':""}</details><details class="cluster-method"><summary>分析方法与范围</summary><p>原词语义向量 → 层次聚类 → 组内 SERP 匹配。语义距离阈值 ${data?.settings.distance_threshold??settings?.distance_threshold??"—"}；至少 ${data?.settings.serp_match_threshold??settings?.serp_match_threshold??"—"} 个相同结果 URL 才形成重合候选。</p><p>不同市场、语言、日期、设备或来源分开比较。搜索证据不足的词保留语义分组，不拆成单词组，也不加入已有搜索证据组；同页关系仍需验证。</p></details></div>`;
  if($("#cluster-version"))$("#cluster-version").onchange=event=>{state.clusterVersion=Number(event.target.value);renderClusters();};
  if(run){
    $("#cluster-query").oninput=event=>{state.clusterQuery=event.target.value;renderClusterRows();};
    $("#cluster-status").onchange=event=>{state.clusterStatus=event.target.value;renderClusterRows();};
    if($("#skip-groups"))$("#skip-groups").onchange=event=>{state.skipGroups=event.target.checked;updateGroupSelection();};
    renderClusterRows();
  }
}
function renderClusterRows(){
  const run=clusterRun(),groups=filteredGroups(),silos=[...new Set(run.payload.groups.map(g=>g.semantic_silo_id))],first=initialKeywordJourney();
  $("#cluster-filter-count").textContent=`显示 ${groups.length} / ${run.payload.groups.length} 个候选组；按主词搜索量从高到低，同搜索量按 KD 从低到高，缺失指标排后。`;
  $("#cluster-results").innerHTML=groups.length?`<div class="table-scroll cluster-table"><table><thead><tr>${first?"<th>保留</th>":""}<th>候选主关键词</th><th>语义主题</th><th>成员</th><th>主词搜索量 / KD</th><th>需要核对</th></tr></thead><tbody>${groups.map(g=>{const primary=g.members[0];return `<tr>${first?`<td data-label="保留"><input type="checkbox" data-group-id="${g.target_page_id}" aria-label="保留候选组 ${esc(primary.keyword)}" ${state.selectedGroupIds.has(g.target_page_id)?"checked":""}></td>`:""}<td data-label="候选主关键词"><button class="text-button accent-link" data-action="show-cluster" data-id="${g.target_page_id}">${esc(primary.keyword)}</button>${state.data.keyword_onboarding?.run_id===run.id&&state.data.keyword_onboarding.selected_group_ids.includes(g.target_page_id)?'<span class="saved-selection">已保留</span>':""}</td><td data-label="语义主题">主题 ${silos.indexOf(g.semantic_silo_id)+1}</td><td data-label="成员">${g.members.length} 词</td><td data-label="主词搜索量 / KD">${primary.volume??"未知"} / ${primary.kd??"未知"}</td><td data-label="需要核对">${badge(g.status,g.basis==="semantic_only"?"同页关系待验证":g.status==="needs_evidence"?"待补证据":"待审核")}${g.chain_overlap?'<div class="inline-error">存在链式关联</div>':""}${g.primary_provisional?'<div class="form-help">主词排序待补指标</div>':""}</td></tr>`;}).join("")}</tbody></table></div>`:'<div class="empty-state"><h2>没有符合筛选条件的候选组</h2><p>已有选择仍保留，调整检索词或状态即可继续。</p></div>';
  $("#cluster-results").onchange=event=>{const id=event.target.dataset.groupId;if(!id)return;event.target.checked?state.selectedGroupIds.add(id):state.selectedGroupIds.delete(id);if(state.selectedGroupIds.size)state.skipGroups=false;updateGroupSelection();};
  updateGroupSelection();
}
function updateGroupSelection(){
  if(!$("#group-selection-count"))return;
  $("#group-selection-count").textContent=`已保留 ${state.selectedGroupIds.size} 个候选组`;
  $("#skip-groups").checked=state.skipGroups;$("#skip-groups").disabled=state.selectedGroupIds.size>0;
  $('[data-action="complete-keywords"]').disabled=!clusterCoversAll(clusterRun())||!!activeJob()||(!state.selectedGroupIds.size&&!state.skipGroups);
}
function showCluster(id){
  const group=clusterRun()?.payload.groups.find(g=>g.target_page_id===id);if(!group)return;
  const names=Object.fromEntries(group.members.map(m=>[m.demand_id,m.keyword])),context=group.context;
  modal("候选组成员与依据",`<h3>${esc(group.members[0].keyword)}</h3><p class="form-help">${esc(marketOptions.find(([key])=>key===context.market)?.[1]||context.market||"市场未知")} · ${esc(context.language||"语言未知")} · ${esc(context.device||"设备未知")}<br>${esc(context.serp_source||"来源未知")} · ${esc(context.serp_date||"日期未知")}</p>${group.reasons.map(r=>`<p class="coverage-note">${esc(r)}</p>`).join("")}${group.primary_provisional?'<p class="form-help">存在缺失指标，主词仅作暂定排序。</p>':""}${group.members.map(m=>`<section class="source-row"><header><h3>${esc(m.keyword)}</h3>${badge("",m.keyword_role==="Primary"?"主关键词":"次关键词")}</header><p>搜索量 ${m.volume??"未知"} · KD ${m.kd??"未知"}</p><details><summary>自然搜索结果 ${m.serp?.length??0} 条${!m.serp?"（未提供）":""}</summary><ol class="serp-urls">${(m.serp||[]).map(url=>`<li>${esc(url)}</li>`).join("")}</ol></details></section>`).join("")}${group.pair_evidence.length?`<details class="cluster-method"><summary>查看成员间重合依据</summary>${group.pair_evidence.map(p=>`<section class="source-row"><p>${esc(names[p.left_id])} ↔ ${esc(names[p.right_id])}</p><p>相同 URL：${p.overlap} 个</p><ul class="serp-urls">${p.shared_urls.map(url=>`<li>${esc(url)}</li>`).join("")}</ul></section>`).join("")}</details>`:group.members.length===1?'<p class="form-help">当前只有一个成员；单词可保留为候选，不代表已确认单独建页。</p>':'<p class="form-help">本组按语义聚合，尚无足够的搜索结果重合证据。</p>'}`,null);
}
function showIntent(id){
  const result=state.intents.find(i=>i.demand_id===id),record=state.demands.find(r=>r.id===id);if(!result||!record)return;
  const data=result.payload,fieldNames={object:"对象",scenario:"场景",main_task:"主要任务",customer_question:"客户问题",decision_required:"待完成判断",answers_needed:"所需答案"};
  modal("需求意图",`<h3>${esc(record.original)}</h3><p class="form-help">依据业务理解 v${result.profile_version} · ${esc(intentLabels[result.status])}</p>${result.profile_version!==latest()?.version?'<p class="inline-error">业务理解已更新；此结果保留原版本依据，需要重新复核。</p>':""}${result.error?`<p class="inline-error">${esc(result.error)}</p>`:""}${data?`<p>${esc(data.reason)}</p><p>企业适配：${esc({fit:"适配",not_fit:"不适配",unknown:"未知"}[data.business_fit.status])} · ${esc(data.business_fit.reason)}</p>${data.candidates.map((c,index)=>`<section class="source-row"><h3>候选 ${index+1} · ${esc(taskLabels[c.main_task])}</h3><p>对象：${esc(c.object||"未知")}<br>场景：${esc(c.scenario||"未知")}</p><p><strong>客户问题</strong><br>${esc(c.customer_question)}</p><p><strong>待完成判断</strong><br>${esc(c.decision_required)}</p><p><strong>明确条件</strong><br>${c.explicit_conditions.map(x=>`${esc(x.text)} — 原词「${esc(x.quote)}」`).join("<br>")||"未明示"}</p><p><strong>所需答案</strong><br>${c.answers_needed.map(esc).join("<br>")}</p><p><strong>原词依据</strong><br>${c.evidence.map(esc).join(" · ")}</p><p class="form-help">推断字段：${c.inferred_fields.map(x=>fieldNames[x]).join("、")||"无"}<br>不确定项：${c.uncertainties.map(esc).join("；")||"模型未列出，仍需人工核对"}</p></section>`).join("")}`:""}`,null);
}
// Semrush SEO API desktop keyword databases, verified 2026-10-09. See docs/development/semrush-market-language.md.
const marketOptions=[
  ["us","美国 · US","United States"],
  ["uk","英国 · UK","United Kingdom"],
  ["ca","加拿大 · CA","Canada"],
  ["ru","俄罗斯 · RU","Russia"],
  ["de","德国 · DE","Germany"],
  ["fr","法国 · FR","France"],
  ["es","西班牙 · ES","Spain"],
  ["it","意大利 · IT","Italy"],
  ["br","巴西 · BR","Brazil"],
  ["au","澳大利亚 · AU","Australia"],
  ["ar","阿根廷 · AR","Argentina"],
  ["be","比利时 · BE","Belgium"],
  ["ch","瑞士 · CH","Switzerland"],
  ["dk","丹麦 · DK","Denmark"],
  ["fi","芬兰 · FI","Finland"],
  ["hk","中国香港特别行政区 · HK","Hong Kong"],
  ["ie","爱尔兰 · IE","Ireland"],
  ["il","以色列 · IL","Israel"],
  ["mx","墨西哥 · MX","Mexico"],
  ["nl","荷兰 · NL","Netherlands"],
  ["no","挪威 · NO","Norway"],
  ["pl","波兰 · PL","Poland"],
  ["se","瑞典 · SE","Sweden"],
  ["sg","新加坡 · SG","Singapore"],
  ["tr","土耳其 · TR","Turkey"],
  ["jp","日本 · JP","Japan"],
  ["in","印度 · IN","India"],
  ["hu","匈牙利 · HU","Hungary"],
  ["af","阿富汗 · AF","Afghanistan"],
  ["al","阿尔巴尼亚 · AL","Albania"],
  ["dz","阿尔及利亚 · DZ","Algeria"],
  ["ao","安哥拉 · AO","Angola"],
  ["am","亚美尼亚 · AM","Armenia"],
  ["at","奥地利 · AT","Austria"],
  ["az","阿塞拜疆 · AZ","Azerbaijan"],
  ["bh","巴林 · BH","Bahrain"],
  ["bd","孟加拉国 · BD","Bangladesh"],
  ["by","白俄罗斯 · BY","Belarus"],
  ["bz","伯利兹 · BZ","Belize"],
  ["bo","玻利维亚 · BO","Bolivia"],
  ["ba","波斯尼亚和黑塞哥维那 · BA","Bosnia and Herzegovina"],
  ["bw","博茨瓦纳 · BW","Botswana"],
  ["bn","文莱 · BN","Brunei"],
  ["bg","保加利亚 · BG","Bulgaria"],
  ["cv","佛得角 · CV","Cabo Verde"],
  ["kh","柬埔寨 · KH","Cambodia"],
  ["cm","喀麦隆 · CM","Cameroon"],
  ["cl","智利 · CL","Chile"],
  ["co","哥伦比亚 · CO","Colombia"],
  ["cr","哥斯达黎加 · CR","Costa Rica"],
  ["hr","克罗地亚 · HR","Croatia"],
  ["cy","塞浦路斯 · CY","Cyprus"],
  ["cz","捷克 · CZ","Czech Republic"],
  ["cd","刚果（金） · CD","Congo"],
  ["do","多米尼加共和国 · DO","Dominican Republic"],
  ["ec","厄瓜多尔 · EC","Ecuador"],
  ["eg","埃及 · EG","Egypt"],
  ["sv","萨尔瓦多 · SV","El Salvador"],
  ["ee","爱沙尼亚 · EE","Estonia"],
  ["et","埃塞俄比亚 · ET","Ethiopia"],
  ["ge","格鲁吉亚 · GE","Georgia"],
  ["gh","加纳 · GH","Ghana"],
  ["gr","希腊 · GR","Greece"],
  ["gt","危地马拉 · GT","Guatemala"],
  ["gy","圭亚那 · GY","Guyana"],
  ["ht","海地 · HT","Haiti"],
  ["hn","洪都拉斯 · HN","Honduras"],
  ["is","冰岛 · IS","Iceland"],
  ["id","印度尼西亚 · ID","Indonesia"],
  ["jm","牙买加 · JM","Jamaica"],
  ["jo","约旦 · JO","Jordan"],
  ["kz","哈萨克斯坦 · KZ","Kazakhstan"],
  ["kw","科威特 · KW","Kuwait"],
  ["lv","拉脱维亚 · LV","Latvia"],
  ["lb","黎巴嫩 · LB","Lebanon"],
  ["lt","立陶宛 · LT","Lithuania"],
  ["lu","卢森堡 · LU","Luxembourg"],
  ["mg","马达加斯加 · MG","Madagascar"],
  ["my","马来西亚 · MY","Malaysia"],
  ["mt","马耳他 · MT","Malta"],
  ["mu","毛里求斯 · MU","Mauritius"],
  ["md","摩尔多瓦 · MD","Moldova"],
  ["mn","蒙古 · MN","Mongolia"],
  ["me","黑山 · ME","Montenegro"],
  ["ma","摩洛哥 · MA","Morocco"],
  ["mz","莫桑比克 · MZ","Mozambique"],
  ["na","纳米比亚 · NA","Namibia"],
  ["np","尼泊尔 · NP","Nepal"],
  ["nz","新西兰 · NZ","New Zealand"],
  ["ni","尼加拉瓜 · NI","Nicaragua"],
  ["ng","尼日利亚 · NG","Nigeria"],
  ["om","阿曼 · OM","Oman"],
  ["py","巴拉圭 · PY","Paraguay"],
  ["pe","秘鲁 · PE","Peru"],
  ["ph","菲律宾 · PH","Philippines"],
  ["pt","葡萄牙 · PT","Portugal"],
  ["ro","罗马尼亚 · RO","Romania"],
  ["sa","沙特阿拉伯 · SA","Saudi Arabia"],
  ["sn","塞内加尔 · SN","Senegal"],
  ["rs","塞尔维亚 · RS","Serbia"],
  ["sk","斯洛伐克 · SK","Slovakia"],
  ["si","斯洛文尼亚 · SI","Slovenia"],
  ["za","南非 · ZA","South Africa"],
  ["kr","韩国 · KR","South Korea"],
  ["lk","斯里兰卡 · LK","Sri Lanka"],
  ["th","泰国 · TH","Thailand"],
  ["bs","巴哈马 · BS","Bahamas"],
  ["tt","特立尼达和多巴哥 · TT","Trinidad and Tobago"],
  ["tn","突尼斯 · TN","Tunisia"],
  ["ua","乌克兰 · UA","Ukraine"],
  ["ae","阿拉伯联合酋长国 · AE","United Arab Emirates"],
  ["uy","乌拉圭 · UY","Uruguay"],
  ["ve","委内瑞拉 · VE","Venezuela"],
  ["vn","越南 · VN","Vietnam"],
  ["zm","赞比亚 · ZM","Zambia"],
  ["zw","津巴布韦 · ZW","Zimbabwe"],
  ["ly","利比亚 · LY","Libya"],
  ["pa","巴拿马 · PA","Panama"],
  ["pk","巴基斯坦 · PK","Pakistan"],
  ["tw","台湾 · TW","Taiwan"],
  ["qa","卡塔尔 · QA","Qatar"]
];
// Languages are independent of country databases; these are Pagggle's common choices, not an official availability matrix.
const languageOptions=[["en","英语"],["ar","阿拉伯语"],["ru","俄语"],["de","德语"],["es","西班牙语"],["fr","法语"],["zh","汉语"],["ja","日语"],["ko","韩语"],["pt","葡萄牙语"],["it","意大利语"],["nl","荷兰语"],["tr","土耳其语"],["pl","波兰语"],["sv","瑞典语"],["da","丹麦语"],["no","挪威语"],["fi","芬兰语"],["cs","捷克语"],["ro","罗马尼亚语"],["el","希腊语"],["hu","匈牙利语"],["uk","乌克兰语"],["he","希伯来语"],["hi","印地语"],["bn","孟加拉语"],["ur","乌尔都语"],["id","印度尼西亚语"],["ms","马来语"],["th","泰语"],["vi","越南语"],["tl","菲律宾语"]];
function scopeOptions(name,value,options,query=""){
  const custom=!!value&&!options.some(([key])=>key===value),needle=query.trim().toLocaleLowerCase();
  return (name==="market"?`<option value="" ${!value?"selected":""}>请选择国家/地区</option>`:"")+options.filter(option=>option[0]===value||option.join(" ").toLocaleLowerCase().includes(needle)).map(([key,text])=>`<option value="${key}" ${value===key?"selected":""}>${text}</option>`).join("")+`<option value="other" ${custom?"selected":""}>${name==="market"?"其它国家/地区（手动填写）":"其它语言（手动填写）"}</option>`;
}
function intakeSelect(name,label,value,options){
  const custom=!!value&&!options.some(([key])=>key===value);
  return `<div><label class="field"><span>${label}</span><select name="${name}" required data-scope-select="${name}">${scopeOptions(name,value,options)}</select></label><label class="field" id="${name}-custom-field" ${custom?"":"hidden"}><span>具体${label}</span><input name="${name}_custom" maxlength="100" value="${custom?esc(value):""}" ${custom?"required":"disabled"}></label></div>`;
}
function rememberIntake(){const form=$("#intake-form");if(!form)return;const data=new FormData(form);if(state.intakeFormat!=="xlsx"||state.intakeKind!=="keyword")state.intakeText=String(data.get("text")||"");state.intakeMarket=String(data.get("market")==="other"?data.get("market_custom")||"":data.get("market")||"").trim();state.intakeLanguage=String(data.get("language")==="other"?data.get("language_custom")||"":data.get("language")||"").trim();}
function renderIntake(){
  const first=initialKeywordJourney();if(first)state.intakeKind="keyword";
  let html=breadcrumb(first?"导入关键词":"添加内容需求")+`<div class="content-page">${first?keywordJourney(1):""}<div class="page-title"><div><h1>${state.preview?"检查导入结果":first?"导入你的关键词表":`添加${labels[state.intakeKind]}`}</h1><p>${state.preview?"每条输入都有处理结果。确认后保存到当前项目。":first?"先导入一批关键词，随后分析全部有效词并筛选结果。":"保留真实问题或变化及上下文，作为后续内容规划的输入。"}</p></div>${first?"":'<a class="accent-link" href="#demands">返回需求列表</a>'}</div>`;
  if(state.preview){const counts=state.preview.summary;html+=`<div class="summary-line"><span>输入 <strong>${counts.input}</strong> 条</span><span>有效 ${counts.valid} 条</span><span>重复 ${counts.duplicate} 条</span><span>失败 ${counts.failed} 条</span></div>${demandTable(state.preview.records)}<div class="page-actions"><button data-action="edit-intake">返回修改</button><button class="primary" data-action="commit-intake">${first?"确认导入，继续分析":"确认保存"}</button></div><p class="data-note">重复与失败记录也会保留用于对账；有效记录进入待分析状态。</p>`;
  }else{html+=`${first?"":`<div class="tabs" role="tablist" aria-label="内容需求类型">${[["keyword","关键词"],["customer_question","客户问题"],["product_change","产品变化"]].map(([kind,label])=>`<button type="button" role="tab" aria-selected="${state.intakeKind===kind}" class="${state.intakeKind===kind?"active":""}" data-action="intake-kind" data-kind="${kind}">${label}</button>`).join("")}</div>`}<form class="intake-form" id="intake-form">${state.intakeKind==="keyword"?`<label class="field"><span>输入方式</span><select name="format" id="intake-format"><option value="lines" ${state.intakeFormat==="lines"?"selected":""}>粘贴关键词，每行一个</option><option value="csv" ${state.intakeFormat==="csv"?"selected":""}>导入 CSV 表格</option><option value="xlsx" ${state.intakeFormat==="xlsx"?"selected":""}>导入 XLSX 表格</option></select></label>${["csv","xlsx"].includes(state.intakeFormat)?`<label class="field"><span>选择 ${state.intakeFormat.toUpperCase()} 文件</span><input type="file" id="keyword-file" accept=".${state.intakeFormat}"><small>${esc(state.intakeFileName)} · 支持 Keyword、Search Volume、Keyword Difficulty、SERP Results，也兼容 keyword、volume、kd、serp。未知指标留空，额外列保留原始值。</small></label><details class="intake-schema"><summary>SERP 和市场信息怎样填写？</summary><p>每词提供前 10 个自然结果 URL，用逗号或单元格换行分隔；没有结果可留空。SERP Features 不是 URL 清单。</p><p>可选列：market、language、source、data_date、serp_source、serp_date、device。日期 YYYY-MM-DD；device 填 desktop / mobile / tablet。XLSX 仅一个工作表，公式请先转为值。</p></details>`:""}`:""}<label class="field" ${state.intakeKind==="keyword"&&state.intakeFormat==="xlsx"?"hidden":""}><span>${state.intakeKind==="keyword"?(state.intakeFormat==="csv"?"CSV 内容预览":"关键词"):state.intakeKind==="customer_question"?"客户原始问题":"产品变化说明"}</span><textarea name="text" ${state.intakeKind==="keyword"&&state.intakeFormat==="xlsx"?"disabled":"required"} rows="8" placeholder="${state.intakeKind==="keyword"?"custom silicone gasket\nsilicone vs EPDM gasket":"保留真实问题、使用条件和上下文。"}">${state.intakeKind==="keyword"&&state.intakeFormat==="xlsx"?"":esc(state.intakeText)}</textarea><small>${state.intakeKind==="keyword"?"不自动填充搜索量或 KD；型号、材料、尺寸与限制条件保留。":"整段内容作为一条需求保存，无需生成虚构关键词或搜索量。"}</small></label><label class="field scope-search"><span>查找国家/地区</span><input type="search" data-scope-search="market" placeholder="国家中文名、英文名或代码"></label><div class="field-row">${intakeSelect("market","国家/地区数据库",state.intakeMarket,marketOptions)}${intakeSelect("language","目标语言",state.intakeLanguage,languageOptions)}</div><p class="form-help">国家/地区按 Semrush 数据库划分；语言独立选择，例如沙特市场也可使用英语。表格各行已有 market / language 优先，空白才使用所选值。</p><div class="inline-error" role="alert"></div><div class="form-footer"><button type="button" data-action="clear-intake">清空输入</button><button class="primary" type="submit">预览并检查</button></div></form>`;}
  $("#main").innerHTML=html+'</div>';const form=$("#intake-form");if(form)form.onsubmit=async event=>{event.preventDefault();rememberIntake();const button=form.querySelector('[type="submit"]');button.disabled=true;
    try{if(!state.intakeMarket||!state.intakeLanguage)throw new Error("请选择国家/地区和语言；选择其它时请填写具体内容。");state.pendingImport={kind:state.intakeKind,format:state.intakeKind==="keyword"?state.intakeFormat:"lines",text:state.intakeText,market:state.intakeMarket||null,language:state.intakeLanguage||null,request_id:crypto.randomUUID()};state.preview=await api(`/projects/${state.id}/demands/preview`,state.pendingImport);state.demandPages.intake=1;renderIntake();}catch(error){form.querySelector(".inline-error").textContent=error.message;button.disabled=false;}};
  const search=$("[data-scope-search]");if(search)search.oninput=()=>{const select=form.elements.market;select.innerHTML=scopeOptions("market",select.value,marketOptions,search.value);};
  document.querySelectorAll("[data-scope-select]").forEach(select=>{select.onchange=()=>{const field=document.getElementById(`${select.name}-custom-field`),input=field.querySelector("input"),custom=select.value==="other";field.hidden=!custom;input.disabled=!custom;input.required=custom;if(custom)input.focus();};});
  if($("#intake-format"))$("#intake-format").onchange=event=>{rememberIntake();const next=event.target.value;if(state.intakeText&&(next==="xlsx"||state.intakeFormat==="xlsx")){event.target.value=state.intakeFormat;notify("请先完成导入或清空当前输入，再切换文件类型。");return;}state.intakeFormat=next;state.intakeFileName="";renderIntake();};
  if($("#keyword-file"))$("#keyword-file").onchange=async event=>{rememberIntake();const file=event.target.files[0];if(!file)return;const id=state.id,format=state.intakeFormat;
    try{let content;if(format==="xlsx"){content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(new Error("文件读取失败"));reader.readAsDataURL(file);});}else content=await file.text();
      if(state.id!==id||state.intakeFormat!==format)return;state.intakeText=content;state.intakeFileName=file.name;renderIntake();
    }catch(error){notify(error.message);}};
}
function addSource(){const id=state.id;modal("补充业务资料",field("title","资料标题","",'required maxlength="200" placeholder="产品规格、能力说明或可披露案例"')+'<label class="field"><span>原始内容</span><textarea name="body" required minlength="20" maxlength="100000" rows="8"></textarea><small>请保留适用范围。资料只用于当前项目。</small></label>',"保存资料",async data=>{await api(`/projects/${id}/sources`,Object.fromEntries(data));notify("资料已保存。可根据新资料重新提取画像，原版本仍保留。");});}
function showSource(id){const source=state.data.sources.find(s=>s.id===id);if(!source)return;modal("原始依据",`<h3>${esc(source.title)}</h3><p class="form-help">${esc(source.url||"用户补充资料")}<br>保存时间：${formatTime(source.updated_at)}</p><pre class="source-body">${esc(source.body)}</pre>`,null);}
function editFact(index){const p=latest(),fact=p.payload.facts[index],id=state.id;modal("修正业务理解",`<label class="field"><span>正确的业务描述</span><textarea name="statement" required maxlength="3000">${esc(fact.statement)}</textarea></label>${field("scope","适用范围",fact.scope,'maxlength="2000"')}${field("reason","修订依据","",'required maxlength="1000" placeholder="请说明企业确认或资料修订依据"')}<p class="form-help">保存为新的待核对版本，保留旧版本及原始引用；不会自动批准整个画像。</p>`,"保存修订",data=>api(`/projects/${id}/profiles/review`,{expected_version:p.version,fact_index:index,decision:"confirmed",...Object.fromEntries(data)}));}
function finishReview(){if(state.reviewDirty){notify("请先保存右侧正在核对的内容，再进入下一步。");return;}const profile=latest(),id=state.id;if(profile.status==="confirmed"){navigate(initialKeywordJourney()?"intake":"demands");return;}const unresolved=profile.payload.facts.filter(f=>f.status!=="confirmed").length;
  modal("保存本次业务核对",`<p>${unresolved?`还有 ${unresolved} 条主张尚未确认，将继续保留独立状态。`:"所有业务主张已逐项确认。"}</p><p class="form-help">这里只确认你已核对这一版业务理解，不会把未知信息变成事实。下一步进入关键词分析与内容需求。</p><label class="radio-option"><input type="checkbox" name="scope_checked" value="yes" required>所处理的业务范围已核对，关键误解已修正</label>`,initialKeywordJourney()?"保存并导入关键词":"保存并继续",async ()=>{await api(`/projects/${id}/profiles/confirm`,{expected_version:profile.version,reason:"用户已核对本版业务范围"});state.view=initialKeywordJourney()?"intake":"demands";location.hash=state.view;});}
function showHistory(){modal("业务理解版本",state.data.profiles.map(p=>`<div class="history-entry"><div><strong>v${p.version} · ${p.status==="confirmed"?"版本已核对":"待核对"}</strong><p>${esc(p.reason)}<br>${formatTime(p.updated_at)}</p></div><button type="button" data-action="view-version" data-version="${p.version}">查看</button></div>`).join(""),null);}
function projectMenu(){modal("项目与设置",`<p>${esc(state.data?.project.name||"尚未接入网站")}</p><p class="form-help">${esc(state.data?.project.site_url||"")}</p><div class="source-row"><button type="button" data-action="new-project">接入另一个网站</button></div>${latest()?'<div class="source-row"><button type="button" data-action="regenerate">重新分析业务理解</button><p class="form-help">生成新的画像版本，旧版本保留。完成后需要重新核对。</p></div>':""}<p class="form-help">模型配置仅保留在本地服务端。当前为单机工作区。</p>`,null);}
function navigate(view){if(state.reviewDirty){notify("请先保存当前核对内容，或撤销本次选择。");return;}rememberIntake();if(location.hash===`#${view}`){route().catch(e=>notify(e.message));}else location.hash=view;}
async function route(){if(state.starting){window.history.replaceState(null,"","#start");notify("正在发起分析，请稍候。");return;}const view=location.hash.slice(1)||"start";if(!["start","analysis","selection","understanding","sources","demands","intake","clusters"].includes(view))return;if(state.reviewDirty){window.history.replaceState(null,"",`#${state.view}`);notify("请先保存当前核对内容，或撤销本次选择。");return;}rememberIntake();state.view=view;
  if(["demands","clusters","intake"].includes(view)&&state.id)await refresh();else render();window.scrollTo(0,0);}


document.addEventListener("click",async event=>{
  const scroll=event.target.closest("[data-scroll]");if(scroll){event.preventDefault();document.getElementById(scroll.dataset.scroll)?.scrollIntoView({behavior:"smooth"});return;}
  const button=event.target.closest("[data-action]");if(!button||button.disabled)return;event.preventDefault();const action=button.dataset.action;
  try{
    if(action==="close-modal")closeModal();else if(action==="discard-modal")closeModal(true);else if(action==="project-menu")projectMenu();
    else if(action==="new-project"){if(state.reviewDirty){notify("请先保存当前核对内容。");return;}rememberIntake();if(state.intakeText){notify("请先完成或清空当前需求输入，再创建其他项目。");return;}closeModal(true);clearTimeout(state.poll);state.startDraft={site_url:"",name:"",limit_mode:"unlimited",crawl_max_pages:""};state.startDirty=false;state.view="start";window.history.replaceState(null,"","#start");renderOnboarding();}
    else if(action==="add-source")addSource();else if(action==="source")showSource(button.dataset.id);else if(action==="edit-fact"){if(state.reviewDirty){notify("请先保存当前核对内容，或撤销本次选择。");return;}editFact(Number(button.dataset.index));}else if(action==="finish-review")finishReview();else if(action==="history")showHistory();
    else if(action==="view-version"){if(state.reviewDirty){notify("请先保存当前核对内容。");return;}state.historyVersion=Number(button.dataset.version);closeModal(true);render();}else if(action==="current-version"){state.historyVersion=null;render();}
    else if(action==="discard-review"){state.reviewDirty=false;render();notify();}
    else if(["focus-review","select-review"].includes(action)){if(state.reviewDirty){notify("请先保存当前核对内容，或撤销本次选择。");return;}state.selectedFact=Number(button.dataset.index);render();if(action==="focus-review")$("#review-panel").scrollIntoView({behavior:"smooth",block:"start"});}
    else if(action==="run-job"||action==="regenerate"){button.disabled=true;const kind=action==="regenerate"?"understand":button.dataset.kind==="analyze"?"discover":button.dataset.kind,previous=state.data.jobs.find(j=>j.id===button.dataset.job);await api(`/projects/${state.id}/jobs`,{kind,...(previous?.options||{}),...(["crawl","discover"].includes(kind)?{crawl_max_pages:previous?.options?.crawl_max_pages??null}:{})});closeModal(true);if(kind==="clusters"){state.view="clusters";state.clusterVersion=null;window.history.replaceState(null,"","#clusters");}else if(kind!=="intents"){state.view="analysis";window.history.replaceState(null,"","#analysis");}await refresh();}
    else if(action==="select-visible"){for(const s of selectionRows())if(s.status!=="excluded")state.selectedPageIds.add(s.id);updateSelectionRows();}
    else if(action==="clear-pages"){state.selectedPageIds.clear();updateSelectionRows();}
    else if(action==="collect-selected"){button.disabled=true;await api(`/projects/${state.id}/jobs`,{kind:"crawl",source_ids:[...state.selectedPageIds],crawl_max_pages:state.selectionLimit});state.view="analysis";window.history.replaceState(null,"","#analysis");await refresh();}
    else if(action==="generate-selected"){button.disabled=true;const previous=state.data.jobs.find(j=>j.id===button.dataset.job),ids=state.data.sources.filter(s=>previous?.options?.source_ids?.includes(s.id)&&s.status==="read").map(s=>s.id);await api(`/projects/${state.id}/jobs`,{kind:"understand",source_ids:ids});state.view="analysis";window.history.replaceState(null,"","#analysis");await refresh();}
    else if(action==="cancel-job"){await api(`/projects/${state.id}/jobs/${button.dataset.id}/cancel`,{});await refresh();}
    else if(action==="toggle-original"){state.showOriginal=!state.showOriginal;document.querySelectorAll("[data-original]").forEach(details=>{details.open=state.showOriginal;});button.setAttribute("aria-pressed",String(state.showOriginal));button.textContent=state.showOriginal?"收起网站原文":"显示网站原文";}
    else if(action==="show-cluster")showCluster(button.dataset.id);
    else if(action==="show-intent")showIntent(button.dataset.id);
    else if(action==="demand-page"){state.demandPages[state.view]=Number(button.dataset.page);refreshDemandTable();const next=$(`[data-direction="${button.dataset.direction}"]`);(next.disabled?$("[data-demand-page-size]"):next).focus();}
    else if(action==="clear-intake"){state.intakeText="";state.intakeFileName="";state.preview=null;state.pendingImport=null;renderIntake();}
    else if(action==="open-intake"){rememberIntake();const kind=initialKeywordJourney()?"keyword":button.dataset.kind||"keyword";if(state.intakeText&&kind!==state.intakeKind){notify("请先保存或清空当前输入，再切换需求类型。");return;}state.intakeKind=kind;state.preview=null;navigate("intake");}
    else if(action==="intake-kind"){if(initialKeywordJourney())return;rememberIntake();if(state.intakeKind==="keyword"&&state.intakeFormat==="xlsx"){if(state.intakeText){notify("请先完成表格导入或清空输入，再切换需求类型。");return;}state.intakeFormat="lines";}state.intakeKind=button.dataset.kind;renderIntake();}else if(action==="edit-intake"){state.preview=null;renderIntake();}
    else if(action==="commit-intake"){button.disabled=true;const first=initialKeywordJourney();await api(`/projects/${state.id}/demands/import`,state.pendingImport);state.preview=null;state.pendingImport=null;state.intakeText="";state.intakeFileName="";state.demandPages={};notify("已保存输入记录及导入结果。");state.view=first?"clusters":"demands";window.history.replaceState(null,"",`#${state.view}`);await refresh();}
    else if(action==="select-visible-groups"){for(const g of filteredGroups())state.selectedGroupIds.add(g.target_page_id);if(state.selectedGroupIds.size)state.skipGroups=false;renderClusterRows();}
    else if(action==="clear-groups"){state.selectedGroupIds.clear();state.skipGroups=false;renderClusterRows();}
    else if(action==="complete-keywords"){button.disabled=true;await api(`/projects/${state.id}/keyword-onboarding/complete`,{run_id:clusterRun().id,selected_group_ids:[...state.selectedGroupIds]});state.view="demands";window.history.replaceState(null,"","#demands");await refresh();notify("首次关键词筛选已保存，可以继续补充客户问题和产品变化。");}
  }catch(error){notify(error.message);button.disabled=false;}
});
document.addEventListener("change",event=>{if(!event.target.matches("[data-demand-page-size]"))return;const size=Number(event.target.value);if(![10,50,100].includes(size))return;state.demandPageSize=size;state.demandPages[state.view]=1;refreshDemandTable();$("[data-demand-page-size]").focus();});
$("#modal").addEventListener("cancel",event=>{event.preventDefault();closeModal();});
$("#project-select").onchange=async event=>{rememberIntake();if(state.reviewDirty||state.modalDirty||state.intakeText||state.startDirty||state.starting||(initialKeywordJourney()&&state.selectedGroupIds.size)){event.target.value=state.id;notify("请先保存当前修改、完成导入或清空未保存的筛选，再切换项目。");return;}state.id=event.target.value;state.data=null;state.historyVersion=null;state.selectedFact=null;state.preview=null;state.pendingImport=null;state.intakeText="";state.intakeMarket="";state.intakeLanguage="en";state.showOriginal=false;state.demands=[];state.demandPages={};state.demandPageSize=10;state.intents=[];state.clusterData=null;state.clusterVersion=null;state.clusterQuery="";state.clusterStatus="";state.selectedGroupIds.clear();state.selectionRun=null;state.skipGroups=false;state.intakeKind="keyword";state.intakeFileName="";state.selectedPageIds.clear();state.selectionLimit=undefined;state.pageQuery="";state.sitemapFilter="";state.startDraft=null;state.startDirty=false;$("#main").innerHTML='<div class="initial-loading">正在切换项目…</div>';localStorage.setItem("pagggle-project",state.id);notify();try{await refresh();if(state.view==="demands")await route();}catch(error){notify(error.message);}};
window.addEventListener("hashchange",()=>route().catch(error=>notify(error.message)));
window.addEventListener("beforeunload",event=>{rememberIntake();if(state.reviewDirty||state.modalDirty||state.intakeText||state.startDirty||(initialKeywordJourney()&&state.selectedGroupIds.size)){event.preventDefault();event.returnValue="";}});
state.view=["start","analysis","selection","understanding","sources","demands","intake","clusters"].includes(location.hash.slice(1))?location.hash.slice(1):"start";
loadProjects(localStorage.getItem("pagggle-project")).catch(error=>notify(error.message));
