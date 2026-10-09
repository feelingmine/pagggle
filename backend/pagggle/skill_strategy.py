"""Dynamic content strategy from actual skill executions, with complete keyword accounting."""
import json
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel, ConfigDict, Field

from .content_strategy import digest, page_role
from .skill_execution import SkillOutputError

VERSION = 'skill-content-strategy-1'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class PageNote(Strict):
    id: int
    summary: str = Field(min_length=10, max_length=180)
    questions: list[str]
    quotes: list[str]


class PageNotes(Strict):
    pages: list[PageNote]


class Topic(Strict):
    title: str = Field(min_length=2, max_length=100)
    question: str = Field(min_length=5, max_length=240)
    seed: str
    buyer_stage: str
    intent: str


class Pillar(Strict):
    title: str
    rationale: str


class Expansion(Strict):
    keyword: str
    seed: str
    rationale: str


class Catalog(Strict):
    pillars: list[Pillar] = Field(min_length=1)
    topics: list[Topic] = Field(min_length=1)
    expansions: list[Expansion]
    unknowns: list[str]


class Assigned(Topic):
    topic_id: int = Field(ge=0)
    members: list[int] = Field(min_length=1)


class Deferred(Strict):
    members: list[int] = Field(min_length=1)
    reason: str = Field(min_length=5)


class Assignments(Strict):
    groups: list[Assigned]
    deferred: list[Deferred]


class Merged(Strict):
    representative: int
    groups: list[int] = Field(min_length=1)


class Merges(Strict):
    topics: list[Merged] = Field(min_length=1)


class Match(Strict):
    target_page: int | None
    related_pages: list[int]
    reason: str = Field(min_length=10)
    business_fit: str = Field(pattern='^(direct|supporting|uncertain|outside)$')


class Requirement(Strict):
    title: str
    question: str
    covered: bool
    quote: str | None
    explanation: str
    suggestion: str


class Score(Strict):
    score: float | None = Field(default=None, ge=1, le=10)
    reason: str


class Assessment(Strict):
    value: str
    value_reason: str
    priority: str
    requirements: list[Requirement] = Field(min_length=1)
    content_type: str
    cta: str
    customer_impact: Score
    content_market_fit: Score
    search_potential: Score
    resources: Score
    unknowns: list[str]


def complete(actual, expected):
    if len(actual)!=len(set(actual)) or set(actual)!=set(expected):
        duplicates=[key for key,count in Counter(actual).items() if count>1]
        raise ValueError(f'输入编号未恰好覆盖一次：缺少 {sorted(set(expected)-set(actual))}；重复 {duplicates}；多余 {sorted(set(actual)-set(expected))}')


def original_quote(body, quote):
    if quote and quote in body:
        return quote
    tokens=re.findall(r'\w+|[^\w\s]',quote or '')
    if not tokens:
        raise ValueError('引用为空')
    pattern=re.escape(tokens[0])
    for previous,token in zip(tokens,tokens[1:]):
        pattern+=(r'\s+' if previous[-1].isalnum() and token[0].isalnum() else r'\s*')+re.escape(token)
    match=re.search(pattern,body,re.I)
    if not match:
        raise ValueError('引用改写了原文，须逐字复制包含换行的原文片段')
    return match[0]


def unique_assignments(output, expected):
    counts=Counter(i for g in output['groups']+output['deferred'] for i in g['members'])
    unique={i for i,count in counts.items() if count==1}
    accepted={kind:[{**g,'members':[i for i in g['members'] if i in unique]} for g in output[kind]
                    if any(i in unique for i in g['members'])] for kind in ('groups','deferred')}
    return accepted,set(expected)-unique


def validate_assignments(value, ids, taxonomy, seeds):
    unexpected={i for g in [*value.groups,*value.deferred] for i in g.members}-ids
    if unexpected:
        raise ValueError(f'输出包含本批输入之外的编号：{sorted(unexpected)}，请删除这些编号')
    for g in value.groups:
        if g.topic_id and g.topic_id not in taxonomy:
            raise ValueError(f'未知目录主题 {g.topic_id}；新主题使用 0，已有主题使用输入 catalog 的 id')
        if g.seed not in seeds:
            raise ValueError('主题基础词不属于本轮输入。seed 必须逐字选择以下一个值：'+
                json.dumps(sorted(seeds),ensure_ascii=False)+'。不能把某个输入关键词当成新基础词；没有业务关联的成员进入 deferred')


def pack(items, budget, base=None, limit=None):
    """Bound requests by serialized size without dropping an item or limiting total input."""
    batches, current = [], []
    base = base or {}
    for item in items:
        if current and ((limit and len(current)>=limit) or len(json.dumps({**base,'items':current+[item]},ensure_ascii=False))>budget):
            batches.append(current)
            current=[]
        if len(json.dumps({**base,'items':[item]},ensure_ascii=False))>budget:
            raise ValueError('单条输入超出模型请求预算，未截断或跳过')
        current.append(item)
    if current:
        batches.append(current)
    return batches


def split_body(body, make_context, budget):
    """Split before submission, counting JSON escapes and all surrounding context."""
    if len(json.dumps(make_context(''),ensure_ascii=False))>=budget:
        raise ValueError('正文之外的上下文已超出输入预算，请增加 model_input_chars；未截断资料')
    parts=[]
    start=0
    while start<len(body):
        low,high=1,min(len(body)-start,budget)
        fit=0
        while low<=high:
            middle=(low+high)//2
            if len(json.dumps(make_context(body[start:start+middle]),ensure_ascii=False))<=budget:
                fit=middle
                low=middle+1
            else:
                high=middle-1
        if not fit:
            raise ValueError('正文分片没有可用输入空间；未截断资料')
        # Prefer a paragraph/line boundary, retaining every character including newlines.
        boundary=body.rfind('\n',start+fit//2,start+fit)
        if start+fit<len(body) and boundary>=0:
            fit=boundary-start+1
        parts.append(body[start:start+fit])
        start+=fit
    return parts


def keyword_batch_limit(settings):
    # Planning estimate, not a tokenizer guarantee. Keep reactive splitting for atypical outputs.
    return min(settings.strategy_keyword_batch_size,max(1,(settings.model_max_tokens-1024)//80))


def assessment_inputs(context, target, budget):
    full={**context,'target_full_text':target}
    if len(json.dumps(full,ensure_ascii=False))<=budget:
        return [full]
    if not target:
        raise ValueError('评估上下文超出输入预算，请增加 model_input_chars；未截断资料')
    def make_context(body):
        return {**context,'target_full_text':{**target,'body':body},'partial_target':True}
    return [make_context(body) for body in split_body(target['body'],make_context,budget)]


def assess_target(execution, settings, context, target, instruction, validate):
    inputs=assessment_inputs(context,target,settings.model_input_chars)
    if len(inputs)==1 and 'partial_target' not in inputs[0]:
        return execution.call('content_assessment',inputs[0],Assessment,instruction,validate,names=('content-strategy','seo-audit'))
    findings=[]
    for part in inputs:
        def validate_part(value):
            validate(value)
            for requirement in value.requirements:
                if requirement.quote:
                    requirement.quote=original_quote(part['target_full_text']['body'],requirement.quote)
        findings.append(execution.call('content_assessment_part',part,Assessment,instruction+
            '当前仅为目标正文的一段：未见的内容只表示本段未见，不能推断整页缺失。后续会合并所有分段证据。',
            validate_part,names=('content-strategy','seo-audit')))
    base={**context,'target_metadata':{k:v for k,v in target.items() if k!='body'}}
    while True:
        batches=pack(findings,settings.model_input_chars,base,limit=max(2,settings.model_max_tokens//1200))
        if len(findings)>1 and len(batches)==len(findings):
            raise ValueError('分段审核结果无法在当前预算下合并，请增加 model_input_chars；全部分段记录已保留')
        merged=[]
        for batch in batches:
            merged.append(execution.call('content_assessment_merge',{**base,'items':batch},Assessment,instruction+
                '输入为完整阅读各正文分段后的审核结果。合并同一读者问题并保留不同问题，不要机械拼接。'
                '其它分段已经回答的问题不能仍因某一段未见而判定整页缺失；部分证据不等于完整回答。'
                'quote 必须复制分段审核的原文引用，不能拼接成不存在的原句。保留尚未解决的未知事实。',
                validate,names=('content-strategy','seo-audit')))
        if len(merged)==1:
            return merged[0]
        findings=merged


def parallel(items, work, workers, checkpoint, label):
    results=[None]*len(items)
    stopped=threading.Event()
    failures=[]
    lock=threading.Lock()
    def run(item):
        if stopped.is_set():
            return None
        try:
            checkpoint(None)
            return work(item)
        except BaseException as error:
            with lock:
                failures.append(error)
            stopped.set()
            raise
    pool=ThreadPoolExecutor(max_workers=workers)
    try:
        futures={pool.submit(run,item):i for i,item in enumerate(items)}
        for count,future in enumerate(as_completed(futures),1):
            if failures:
                raise failures[0]
            results[futures[future]]=future.result()
            checkpoint(f'{label} {count}/{len(items)}')
    finally:
        stopped.set()
        pool.shutdown(wait=True,cancel_futures=True)
    return results


def business(snapshot):
    profile=snapshot.get('profile') or {}
    return {'site':snapshot['project']['site_url'],'seeds':snapshot['seeds'],
            'facts':profile.get('payload',{}).get('facts',[]),
            'goal':'外贸独立站获得相关采购询盘；行业、产品和具体受众从当次证据推导',
            'unknown':'没有客户访谈、转化/排名效果、资源预算或实时 SERP 数据，不能编造这些依据'}


def validate_merges(value, ids, require_complete=True):
    # Overlapping equivalence proposals are combined transitively; final members remain unique.
    actual={i for t in value.topics for i in t.groups}
    if require_complete or actual-ids:
        complete(sorted(actual),ids)
    if any(t.representative not in t.groups for t in value.topics):
        raise ValueError('representative 必须是该组 groups 中的一个输入编号')


def merge_topic_groups(groups,outputs):
    parents=list(range(len(groups)))
    def root(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]]
            i=parents[i]
        return i
    for output in outputs:
        for topic in output['topics']:
            representative=root(topic['representative'])
            for i in topic['groups']:
                parents[root(i)]=representative
    merged={}
    for i,g in enumerate(groups):
        representative=root(i)
        if representative not in merged:
            merged[representative]={**groups[representative],'members':[]}
        merged[representative]['members']+=g['members']
    return list(merged.values())


def consolidate_topics(pending, context, execution, settings, checkpoint):
    """Compare each new batch against every catalog shard; never grow an unbounded prompt."""
    limit=max(2,(settings.model_max_tokens-1024)//32)
    def definitions(groups):
        return [{'i':i,**{k:g[k] for k in Topic.model_fields}} for i,g in enumerate(groups)]
    def merge_request(payload, repair=False):
        ids={g['i'] for g in payload['items']+payload.get('incoming',[])}
        original_ids=sorted(ids)
        remapped=original_ids!=list(range(min(ids),max(ids)+1))
        if remapped:
            local_ids={i:index for index,i in enumerate(original_ids)}
            payload={**payload,**{key:[{**g,'i':local_ids[g['i']]} for g in payload[key]]
                for key in ('items','incoming') if key in payload}}
            ids=set(range(len(original_ids)))
        def validate(value):
            validate_merges(value,ids,require_complete=repair)
        output=execution.call('topic_consolidation',payload,Merges,
            '合并跨批次的同义/近义主题，使一项客户任务对应一个主题。items 和 incoming 都是待比较的主题；'
            '区分相同任务的不同措辞与需要不同答案的任务。不要因为产品相同就合并安全、采购、比较等不同问题，'
            '也不要按单复数、营销修饰拆页。所有输入 i 恰好出现在一个 groups 中；representative 选该组中最能表达共同任务的原主题编号。'
            '只输出编号，系统保留所选代表主题的原始定义和全部成员。这是语义主题合并，不是 SERP 同页验证。',validate)
        covered={i for t in output['topics'] for i in t['groups']}
        if ids-covered:
            entries=payload['items']+payload.get('incoming',[])
            # Each omitted topic is explicitly compared against every other topic in small requests.
            for missing in sorted(ids-covered):
                target=next(g for g in entries if g['i']==missing)
                base={'business':payload['business'],'incoming':[target]}
                batches=pack([g for g in entries if g['i']!=missing],settings.model_input_chars,base,min(32,limit//2))
                for batch in batches:
                    corrected=merge_request({**base,'items':batch},repair=True)
                    output={'topics':output['topics']+corrected['topics']}
            validate_merges(Merges.model_validate(output),ids)
        if remapped:
            output={'topics':[{'representative':original_ids[t['representative']],
                'groups':[original_ids[i] for i in t['groups']]} for t in output['topics']]}
        return output
    batches=pack(definitions(pending),settings.model_input_chars,{'business':context},limit//2)
    proposals=parallel(batches,lambda batch:merge_request({'business':context,'items':batch}),
        settings.strategy_concurrency,checkpoint,'3/5 skill 分片合并主题')
    catalog=[]
    for index,(batch,output) in enumerate(zip(batches,proposals),1):
        # Local responses use original pending IDs; translate them before combining.
        indices={g['i']:i for i,g in enumerate(batch)}
        local={'topics':[{'representative':indices[t['representative']],
                         'groups':[indices[i] for i in t['groups']]} for t in output['topics']]}
        incoming=merge_topic_groups([pending[g['i']] for g in batch],[local])
        if not catalog:
            catalog=incoming
            continue
        groups=catalog+incoming
        entries=definitions(groups)
        base={'business':context,'incoming':entries[len(catalog):]}
        shards=pack(entries[:len(catalog)],settings.model_input_chars,base,limit-len(incoming))
        outputs=parallel(shards,lambda shard:merge_request({**base,'items':shard}),
            settings.strategy_concurrency,checkpoint,f'3/5 skill 对齐主题目录 {index}/{len(batches)}')
        catalog=merge_topic_groups(groups,outputs)
    complete([i for g in catalog for i in g['members']],[i for g in pending for i in g['members']])
    return catalog


def execute_strategy(snapshot, execution, settings, checkpoint):
    """Every stage executes skills; only validation, transport and presentation are deterministic code."""
    context=business(snapshot)
    seeds={s['keyword'] for s in snapshot['seeds']}
    pages=[p for p in snapshot['pages'] if p['status']=='read' and page_role(p)=='content']
    pages += [p for p in snapshot['competitors'] if p['status']=='read']
    own_urls={p['url'] for p in snapshot['pages']}
    page_ids={i:p for i,p in enumerate(pages)}
    chunks=[]
    for i,p in page_ids.items():
        # Reserve enough ID digits for every possible one-character chunk.
        reserved_id=len(chunks)+len(p['body'])
        parts=split_body(p['body'],lambda body:{'items':[{'id':reserved_id,'page':i,'title':p['title'],'body':body}]},settings.model_input_chars)
        for body in parts:
            chunks.append({'id':len(chunks),'page':i,'title':p['title'],'body':body})
    batches=pack(chunks,settings.model_input_chars,limit=max(1,settings.model_max_tokens//600))
    def read(batch):
        by_id={p['id']:p for p in batch}
        def validate(value):
            complete([p.id for p in value.pages],by_id)
            for p in value.pages:
                if not p.quotes:
                    raise ValueError('网页摘要没有原文引用')
                quotes=[]
                for q in p.quotes:
                    try:
                        quotes.append(original_quote(by_id[p.id]['body'],q))
                    except ValueError:
                        raise ValueError(f'页面 {p.id} 引用没有对应原文，请从该页面逐字复制片段：{q}') from None
                p.quotes=quotes
        return execution.call('page_inventory',{'items':batch},PageNotes,
            '逐段阅读所有输入正文。每段返回其真正回答的核心问题、紧凑中文摘要（180 字符以内）及精确原文引用。'
            '区分正文与菜单/页脚；摘要应体现页面主要产品、受众和任务，不能因为页脚产品导航而认定正文覆盖该产品。'
            '同一页可能分段，本段未见的信息不得断言全页没有。覆盖每个 id 恰好一次，不添加输入外 ID。',validate,names=('seo-audit',))
    notes=parallel(batches,read,settings.strategy_concurrency,checkpoint,'2/5 skill 阅读完整正文')
    cards={i:{'id':i,'summary':'','questions':[],'quotes':[]} for i in page_ids}
    for output in notes:
        for n in output['pages']:
            card=cards[chunks[n['id']]['page']]
            card['summary']+=' '+n['summary']
            card['questions']+=n['questions']
            card['quotes']+=n['quotes']
    own_cards=[[i,c['summary']] for i,c in cards.items() if page_ids[i]['url'] in own_urls]
    rows=sorted(snapshot['keywords'],key=lambda r:(r['market'],r['language'],r['keyword'].casefold(),r['id']))
    observed=sorted(rows,key=rank)[:100]
    def validate_catalog(value):
        if any(t.seed not in seeds for t in [*value.topics,*value.expansions]):
            raise ValueError('主题或拓词未关联本轮基础词')
    catalog=execution.call('strategy_catalog',{'business':context,'pages':own_cards,
        'measured_keyword_examples':[[r['keyword'],r['volume'],r['kd']] for r in observed],
        'keyword_total':len(rows),'examples_scope':'按已知搜索量排序的100个样例，只用于提出初步主题；下一阶段逐词阅读全量输入并允许补充主题'},Catalog,
        '应用 content-strategy 的内容支柱、买家阶段与优先主题方法，结合本次业务和已读网页提出动态初步主题目录。'
        '主题是一项可由一个内容单元回答的客户任务，不是每个基础词机械乘上同一套任务。不要重复近义主题，也不要把不同任务合成万能页面。'
        '每个主题关联一个实际输入 seed；同时提出自然衍生检索词，不编造指标。不要把目录当固定边界，后续关键词可以产生新主题。'
        '区分采购支持、选型/知识支持与纯零售/品牌导航；业务证据不足的方向列 unknowns。',validate_catalog)
    taxonomy={i+1:t for i,t in enumerate(catalog['topics'])}
    base={'business':context,'catalog':[{'id':i,**t} for i,t in taxonomy.items()]}
    keyword_batches=pack([{'i':i,'keyword':r['keyword'],'market':r['market'],'language':r['language'],'volume':r['volume'],'kd':r['kd']} for i,r in enumerate(rows)],settings.model_input_chars,{**base,'retry_unresolved':3},keyword_batch_limit(settings))
    def assign(batch):
        remaining=batch
        accepted={'groups':[],'deferred':[]}
        instruction=(
            '逐词理解并归类全部 items，每个 i 必须且只能出现在一个 groups.members 或 deferred.members 中。'
            '按真实搜索意图、对象和客户问题合组，不能仅因共有单词就合并；兼容任意行业的新输入。'
            '匹配动态目录时 topic_id 填目录 ID、复用目录元数据；目录不足时 topic_id=0，生成新的具体 title/question/seed/buyer_stage/intent。'
            '保留能支持采购的知识内容；不同品牌导航、其它材料/产品、与当前业务不符或无法可靠判断的词进入 deferred，按同一原因合并编号。'
            '不要逐词建主题，也不要把复杂任务强行装入一个泛化主题。不要省略长尾、低搜索量或未知指标词。')
        for attempt in range(4):
            ids={r['i'] for r in remaining}
            def validate(value):
                validate_assignments(value,ids,taxonomy,seeds)
            try:
                output=execution.call('keyword_assignment',{**base,'items':remaining,**({'retry_unresolved':attempt} if attempt else {})},Assignments,instruction,validate)
            except SkillOutputError:
                if len(remaining)==1:
                    accepted['deferred'].append({'members':[remaining[0]['i']],
                        'reason':'模型对该词的单独输出仍未通过校验，保留待人工核对；没有使用规则替代语义判断'})
                else:
                    middle=len(remaining)//2
                    for subset in (remaining[:middle],remaining[middle:]):
                        resolved=assign(subset)
                        for kind in accepted:
                            accepted[kind]+=resolved[kind]
                remaining=[]
                break
            resolved,unresolved=unique_assignments(output,ids)
            for kind in accepted:
                accepted[kind]+=resolved[kind]
            remaining=[r for r in remaining if r['i'] in unresolved]
            if not remaining:
                break
        if remaining:
            accepted['deferred'].append({'members':[r['i'] for r in remaining],
                'reason':'模型四次分析后仍未返回唯一归属，保留待人工核对；没有丢弃或强行分组'})
        complete([i for g in accepted['groups']+accepted['deferred'] for i in g['members']],[r['i'] for r in batch])
        return accepted
    assignments=parallel(keyword_batches,assign,settings.strategy_concurrency,checkpoint,'3/5 skill 逐词归类')
    grouped={}
    deferred=[]
    for output in assignments:
        for g in output['groups']:
            definition={k:g[k] for k in Topic.model_fields}
            for i in g['members']:
                r=rows[i]
                key=(digest(definition),r['market'],r['language'])
                if key not in grouped:
                    grouped[key]={**definition,'market':r['market'],'language':r['language'],'members':[]}
                grouped[key]['members'].append(r['id'])
        for d in output['deferred']:
            deferred += [{'id':rows[i]['id'],'reason':d['reason']} for i in d['members']]
    # Reconcile proposals with the existing dynamic catalog per market/language.
    topics=[]
    contexts=sorted({(g['market'],g['language']) for g in grouped.values()})
    for market,language in contexts:
        pending=[g for g in grouped.values() if (g['market'],g['language'])==(market,language)]
        topics += consolidate_topics(pending,context,execution,settings,checkpoint)
    by_key={r['id']:r for r in rows}
    for t in topics:
        t['members']=sorted(t['members'],key=lambda key:rank(by_key[key]))
        t['id']=digest([t['question'],t['members']])[:20]
    def review(topic):
        topic_context={k:v for k,v in topic.items() if k!='members'}
        topic_context['keyword_count']=len(topic['members'])
        topic_context['representative_keywords']=[by_key[key]['keyword'] for key in topic['members'][:12]]
        topic_context['primary_metrics']={k:by_key[topic['members'][0]][k] for k in ('keyword','volume','kd')}
        own_ids={i for i,p in page_ids.items() if p['url'] in own_urls}
        def validate_match(value):
            if value.target_page is not None and value.target_page not in own_ids:
                raise ValueError('目标页不是本项目已读正文')
            if any(i not in own_ids for i in value.related_pages):
                raise ValueError('关联页不存在')
        match=execution.call('page_matching',{'business':context,'topic':topic_context,'all_own_page_summaries':own_cards},Match,
            '依据所有已读网页的语义目录，为该客户任务寻找真实承接页。只在正文主题/受众/任务相关时选择 target_page。'
            '不能仅凭 URL/标题关键词或菜单命中选择页面；现有页面可优化承接时优先复用。没有相关页面则 null，并解释排查依据。'
            'related_pages 只列真正相关页面。business_fit 判断当次业务适配，uncertain/outside 不能直接建议新增。',validate_match,names=('content-strategy','seo-audit'))
        target=page_ids.get(match['target_page'])
        full_target={'url':target['url'],'title':target['title'],'body':target['body']} if target else None
        competitors=[{'url':p['url'],**cards[i]} for i,p in page_ids.items() if p['url'] not in own_urls]
        def validate_assessment(value):
            for req in value.requirements:
                if req.covered and not req.quote:
                    raise ValueError('已覆盖项没有原文依据')
                if req.quote:
                    if not target:
                        raise ValueError('没有目标页，不能生成原文引用')
                    req.quote=original_quote(target['body'],req.quote)
                if not req.covered and not req.suggestion:
                    raise ValueError('缺口没有具体建议')
            if value.customer_impact.score is not None or value.resources.score is not None:
                raise ValueError('没有客户研究或资源预算，不得伪造量化评分')
        assessed=assess_target(execution,settings,{'business':context,'topic':topic_context,'match':match,
            'competitor_evidence':competitors},full_target,
            '执行 content-strategy 的价值/优先级评估和 seo-audit 的内容深度审核。围绕本主题的真实买家问题生成检查项，禁止所有产品套同一列表。'
            '完整阅读目标正文；covered=true 必须存在实际回答该问题的原文 quote，不是关键词提及、页脚导航或其它含义的同一个词。'
            '部分覆盖也用 covered=false，quote 可指出现有内容，explanation 说明缺口。每项 question 为具体读者问题，suggestion 为可执行改进或新增模块；已覆盖项说明保留依据。'
            '没有目标页时所有 covered=false，给出针对该主题的原创大纲模块，不编造企业能力、参数、认证或医疗建议。'
            '竞品引用只用于结构与角度参考，不是本站事实或排名证据。优先解释 B2B 价值、目标受众和是否值得制作。'
            '四维评分中 customer_impact/resources 没有实测资料，score 必须 null 并解释缺什么；fit/search 可基于本轮数据推断并说明。'
            '未知业务事实列 unknowns；cta 应符合本主题下一步，不重复通用模板。',validate_assessment)
        return {'topic':topic,'match':match,'assessment':assessed}
    decisions=parallel(topics,review,settings.strategy_concurrency,checkpoint,'4/5 skill 匹配全文与内容决策')
    snapshot['strategy']={'version':VERSION,'skills':execution.skills,'catalog':catalog,'page_cards':list(cards.values()),
        'page_urls':[p['url'] for p in pages],'decisions':decisions,'deferred':deferred,
        'calls':sorted(execution.records,key=lambda r:(r['stage'],r['request_hash'])),
        'keyword_batches':len(keyword_batches),'keyword_count':len(rows),'page_chunks':len(chunks),
        'max_threads':settings.strategy_concurrency,'keyword_batch_limit':keyword_batch_limit(settings)}
    return build_skill_result(snapshot)


def rank(row):
    return (row['volume'] is None,-(row['volume'] or 0),row['kd'] is None,row['kd'] if row['kd'] is not None else 101,row['keyword'],row['id'])


def build_skill_result(snapshot):
    """Replay validated semantic decisions without a model request or business dictionaries."""
    s=snapshot['strategy']
    if s['version']!=VERSION:
        raise ValueError('策略执行版本不兼容')
    rows={r['id']:r for r in snapshot['keywords']}
    complete([key for d in s['decisions'] for key in d['topic']['members']]+[d['id'] for d in s['deferred']],rows)
    pages={p['url']:p for p in snapshot['pages']+snapshot['competitors']}
    topics=[]
    for d in s['decisions']:
        t,m,a=d['topic'],d['match'],d['assessment']
        members=[rows[key] for key in t['members']]
        target=s['page_urls'][m['target_page']] if m['target_page'] is not None else None
        reqs=[{'title':r['title'],'covered':r['covered'],'explanation':r['explanation'],
               'evidence':{'url':target,'quote':r['quote']} if r['quote'] else None,'competitor_examples':[]} for r in a['requirements']]
        for r in reqs:
            if r['evidence'] and r['evidence']['quote'] not in pages[target]['body']:
                raise ValueError('快照引用与原文不一致')
        missing=[r for r in a['requirements'] if not r['covered']]
        action=('review' if m['business_fit'] in {'uncertain','outside'} else
                ('optimize' if missing else 'keep') if target else 'new')
        if action=='new' and any(p['status'] not in {'read','excluded'} and page_role(p)=='content' for p in snapshot['pages']):
            action='review'
        reason=m['reason']+(' '+f'正文覆盖 {len(reqs)-len(missing)}/{len(reqs)} 个语义检查项。' if target else ' 尚无匹配承接页；新增仍为待审核候选。')
        related=[{'url':s['page_urls'][i],'title':pages[s['page_urls'][i]]['title']} for i in m['related_pages']]
        topic={**{k:v for k,v in t.items() if k!='members'},'member_ids':t['members'],'primary':members[0]['keyword'],
            'keyword_count':len(members),'volume':members[0]['volume'],'kd':members[0]['kd'],
            'sample_keywords':[r['keyword'] for r in members[:8]],'volume_conflicts':sum(r['volume_conflict'] for r in members),
            'kd_missing':sum(r['kd'] is None for r in members),'action':action,'target_url':target,'reason':reason,
            'value':a['value'],'value_reason':a['value_reason'],'priority':a['priority'],'requirements':reqs,
            'decision_basis':[a['value_reason'],m['reason'],'买家阶段：'+t['buyer_stage'],'搜索意图：'+t['intent'],
                              '模型语义判断，尚未经过 SERP 和业务审核；客户影响与资源预算未知'],
            'candidates':([{'url':target,'title':pages[target]['title'],'status':'read'}] if target else []),
            'related_pages':related,'competitors':[],
            'strategy_scores':{k:a[k] for k in ('customer_impact','content_market_fit','search_potential','resources')},
            'unknowns':a['unknowns']}
        topic['brief']={'kind':action,'title':t['title'],'primary_keyword':members[0]['keyword'],'target_url':target,
            'buyer_task':t['question'],'content_type':a['content_type'],
            'optimization':[{'gap':r['title'],'suggestion':r['suggestion']} for r in missing] if action=='optimize' else [],
            'outline':[{'heading':r['title'],'question':r['question'],'instructions':r['suggestion'],'evidence':None,'needs_confirmation':True} for r in missing] if action in {'new','optimize'} else [],
            'supporting_keywords':topic['sample_keywords'],'internal_links':related,'cta':a['cta'],
            'publication_gate':'模型建议需人工核对业务范围、原文和 SERP；未知事实不得进入发布稿。'}
        topics.append(topic)
    topics.sort(key=lambda t:(t['volume'] is None,-(t['volume'] or 0),t['kd'] is None,t['kd'] if t['kd'] is not None else 101,t['id']))
    webpages=[p for p in snapshot['pages'] if page_role(p)!='asset']
    assets=[p for p in snapshot['pages'] if page_role(p)=='asset']
    keywords={r['keyword'].casefold() for r in rows.values()}
    return {'version':VERSION,'accounting':snapshot['accounting'],'topics':topics,'deferred':s['deferred'],
        'expansions':[{**e,'observed':e['keyword'].casefold() in keywords} for e in s['catalog']['expansions']],
        'summary':{'topics':len(topics),'assigned':sum(t['keyword_count'] for t in topics),'deferred':len(s['deferred']),
                   'actions':dict(Counter(t['action'] for t in topics))},
        'coverage':{'own_read':sum(p['status']=='read' for p in webpages),'own_discovered':len(webpages),
            'asset_read':sum(p['status']=='read' for p in assets),'asset_total':len(assets),
            'competitor_read':sum(p['status']=='read' for p in snapshot['competitors']),'competitor_total':len(snapshot['competitors'])},
        'method':{'kind':'skill_model','skills':[{k:v for k,v in skill.items() if k!='instructions'} for skill in s['skills'].values()],
            'pillars':s['catalog']['pillars'],'unknowns':s['catalog']['unknowns'],'calls':s['calls'],
            'keyword_count':s['keyword_count'],'keyword_batches':s['keyword_batches'],'page_chunks':s['page_chunks']},
        'limits':['主题由本轮 skill 和模型推导，不是行业词典；没有实时 SERP 同页验证。',
                  '完整正文分批生成语义目录，匹配后的目标正文再次审核；目录压缩仍可能漏掉细节，需人工复核。',
                  '快照重放复用已保存模型决策；重新请求模型不承诺结果完全一致。',
                  '客户访谈、排名/询盘表现及资源预算未知，优先级只是有依据的策略建议。']}
