"""Versioned, deterministic content decisions; never equate a topic with a URL."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from urllib.parse import unquote, urlsplit

VERSION = "silicone-content-strategy-4"
# Ordered objects: accessories before parent products, specific products before hubs.
OBJECTS = [
    ("pet", "宠物硅胶用品", r"\b(?:pet|dog|cat)s?\b", "pet silicone products"),
    ("pacifier_clip", "奶嘴夹与配件", r"pacifier.*(?:clip|holder|case|strap)|(?:clip|holder|case).*pacifier", "baby silicone products"),
    ("teether", "硅胶牙胶", r"teether|teething.{0,20}(?:toy|ring|mitt|bead|tube|stick)|(?:toy|ring).{0,15}teething", "silicone teether"),
    ("feeding_set", "婴儿喂养套装", r"(?:feeding|weaning|dinnerware|tableware).{0,20}(?:set|kit)|set.{0,20}(?:feeding|weaning)", "silicone baby feeding set"),
    ("bib", "硅胶围兜", r"\bbibs?\b", "baby silicone products"),
    ("spoon", "婴儿勺与餐具", r"\bspoons?\b|feeding utensils|baby utensils", "silicone baby feeding set"),
    ("plate", "婴儿餐盘", r"\bplates?\b|baby dishes", "silicone baby feeding set"),
    ("bowl", "喂养碗", r"\bbowls?\b", "silicone baby feeding set"),
    ("food_storage", "辅食储存与分装", r"food.{0,20}(?:storage|container|freezer|tray)|freez.{0,20}(?:food|tray)", "silicone baby feeding set"),
    ("feeder", "辅食喂食器", r"\bfeeders?\b|fruit pacifier|food pacifier", "baby silicone products"),
    ("pacifier", "安抚奶嘴", r"pacifier|soother|binkie", "baby silicone products"),
    ("bottle", "硅胶瓶", r"\bbottles?\b", "silicone bottles"),
    ("cup", "硅胶杯", r"\bcups?\b|drinkware", "silicone cups"),
    ("ice_tray", "硅胶冰格", r"ice.{0,15}(?:cube|tray|mold)|ice cube", "silicone ice cube trays"),
    ("mat", "硅胶垫", r"\bmats?\b|placemats?", "silicone mats"),
    ("mold", "模具与开发", r"\bmou?lds?\b|tooling", "silicone mold"),
    ("baby_toy", "婴儿硅胶玩具", r"(?:baby|infant|silicone|silicon).{0,20}(?:toy|block)|(?:toy|block).{0,20}(?:baby|infant|silicone)", "baby silicone products"),
    ("baby", "婴幼儿硅胶用品", r"(?:baby|infant|child).{0,20}(?:product|suppl|care)|baby silicone|silicone baby", "baby silicone products"),
    ("kitchen", "厨房硅胶用品", r"kitchen|cookware|cooking utensils|spatula|bakeware", "kitchen silicone products"),
    ("household", "家居硅胶用品", r"household|home silicone", "household silicone products"),
    ("odm", "ODM 产品开发", r"\bodm\b|product development", "ODM silicone product development"),
    ("manufacturing", "硅胶定制制造", r"silicon[e]?.{0,35}(?:manufactur|suppli|factory|factories|custom|molding|production)|(?:custom|oem).{0,25}silicone", "custom silicone product manufacturing"),
]
OBJECT_MAP = {o[0]: o for o in OBJECTS}
TASKS = {
    "care": ("清洁与使用", r"clean|washing|\bwash\b|dishwasher|steriliz|sterilis|sanitize|smell|odor|odour|how to use"),
    "safety": ("材料与安全", r"\bsafe\b|safety|toxic|bpa|\bfda\b|lfgb|certif|food.grade"),
    "compare": ("选型与比较", r"\bvs\b|versus|compar|\bbest\b|\bbetter\b|how to choose|which|review"),
    "cost": ("价格与采购条件", r"\bcost\b|\bprice\b|pricing|\bmoq\b|lead.time|minimum order"),
    "sourcing": ("制造与批量供货", r"\bmanufactur\w*|\bsuppliers?\b|\bwholesale\w*|\bfactor(?:y|ies)\b|\boem\b|\bodm\b|private label|\bbulk\b|\bcustom\w*"),
    "process": ("设计与制造工艺", r"how to make|making|process|injection|compression|molding|tooling|prototype|design"),
    "product": ("产品与规格", ""),
}
# Each item is an answer requirement, not an assertion of company capability.
REQUIREMENTS = {
    "product": [("产品与适用场景", r"application|feeding|teething|baby|infant|use"), ("规格与可选配置", r"size|dimension|capacity|hardness|weight|piece"), ("材料及依据", r"material|food.grade|food.contact|platinum.cured|test report"), ("询盘与选型下一步", r"quote|contact|inquir|drawing|sample")],
    "sourcing": [("产品与定制范围", r"custom|oem|odm|logo|packaging"), ("MOQ 与样品条件", r"moq|minimum order|sample"), ("交期与流程", r"lead.time|delivery|production|process"), ("质量与证明材料", r"test|quality|inspection|certificate|report"), ("询价所需信息", r"drawing|specification|quote|quantity|contact")],
    "care": [("清洁步骤", r"wash|clean|rinse"), ("温度与设备条件", r"temperature|dishwasher|boil|heat"), ("不适用情形与限制", r"avoid|do not|caution|warning|damage"), ("干燥储存与更换", r"dry|storage|store|replace|wear")],
    "safety": [("材料与产品适用范围", r"material|food.contact|food.grade"), ("证明文件与测试范围", r"test|report|certificate|standard"), ("使用限制与风险", r"risk|hazard|avoid|warning|supervision"), ("采购核验方法", r"verify|check|request|documentation|supplier")],
    "compare": [("比较对象与使用条件", r"compare|versus|\bvs\b|alternative|plastic|silicone"), ("选择维度与取舍", r"advantage|disadvantage|trade.off|durab|cost|design"), ("规格或测试依据", r"test|specification|dimension|material"), ("按场景选择与下一步", r"choose|select|application|need|contact")],
    "cost": [("成本构成", r"cost|price|tooling|material"), ("数量与规格对价格的影响", r"quantity|volume|size|complex|moq"), ("样品和模具费用边界", r"sample|mold|tooling"), ("报价所需资料", r"quote|drawing|specification|contact")],
    "process": [("设计输入与步骤", r"design|drawing|step|process"), ("材料与工艺选择", r"material|injection|compression|molding"), ("样品及验证", r"prototype|sample|test"), ("量产与质量控制", r"production|inspection|quality")],
}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def normalized(value):
    return re.sub(r"\s+", " ", str(value).casefold().replace("-", " ")).strip()


def classify(text, filter_scope=True):
    text = normalized(text)
    if filter_scope and re.search(r"\b(?:adults?|abdl|sex|reborn|doll|cannabis|porn)\b|teething (?:pain|remed)|meds for teething", text):
        return None, "product", "不同受众或缺少已确认业务依据"
    if filter_scope and re.search(r"\b(?:amazon|walmart|target|ebay|etsy)\b|near me", text):
        return None, "product", "特定渠道/本地导航需求，单独核对"
    obj = next((o[0] for o in OBJECTS if re.search(o[2], text)), None)
    task = next((k for k, (_, pattern) in TASKS.items() if pattern and re.search(pattern, text)), "product")
    if not obj:
        return None, task, "未匹配基础词的产品对象，待人工归类"
    if filter_scope and task != "compare" and re.search(r"\b(?:glass|steel|wooden|ceramic|latex|plastic)\b", text) and "silicone" not in text and "silicon" not in text:
        return None, task, "其它材料产品，需确认业务范围"
    return obj, task, None


def parse_seeds(text):
    seeds = []
    kind = "product"
    for line in text.splitlines():
        line = line.strip()
        if line == "服务分类":
            kind = "service"
        elif re.fullmatch(r"[A-Za-z][A-Za-z0-9 &/\-]+", line):
            if normalized(line) not in {normalized(s['keyword']) for s in seeds}:
                seeds.append({"keyword": line, "kind": kind})
    if not seeds:
        raise ValueError("基础词文件没有可识别的英文基础词")
    return seeds


def consolidate(file_records):
    grouped, rejected = {}, []
    total = 0
    for name, records in sorted(file_records.items()):
        for r in records:
            total += 1
            observation = {"file": name, "row": r["row"], "raw": r["raw"], "volume": r["volume"], "kd": r["kd"]}
            if r["status"] == "failed":
                rejected.append({"file": name, "row": r["row"], "keyword": r["original"], "error": r["error"]})
                continue
            key = (normalized(r["original"]), r["market"], r["language"])
            if key not in grouped:
                grouped[key] = {"id": digest(key)[:20], "keyword": r["original"], "market": r["market"], "language": r["language"], "observations": []}
            grouped[key]["observations"].append(observation)
    rows = []
    for _, r in sorted(grouped.items()):
        for metric in ("volume", "kd"):
            values = {o[metric] for o in r["observations"] if o[metric] is not None}
            r[metric] = next(iter(values)) if len(values) == 1 else None
            r[metric + "_conflict"] = len(values) > 1
        rows.append(r)
    return rows, {"input": total, "unique": len(rows), "duplicate_observations": total-len(rejected)-len(rows), "failed": len(rejected)}, rejected


def expand(seeds, rows):
    observed = {normalized(r["keyword"]) for r in rows}
    result = []
    for seed in seeds:
        term = seed['keyword']
        suffixes = ["cost", "process", "supplier"] if seed['kind'] == 'service' else ["manufacturer", "wholesale", "custom", "how to choose", "how to clean"]
        for modifier in suffixes:
            keyword = f"{modifier} {term}" if modifier in {'custom','how to choose','how to clean'} else f"{term} {modifier}"
            result.append({"seed":term, "keyword":keyword, "observed":normalized(keyword) in observed, "volume":None, "kd":None, "basis":"规则衍生；未命中导出数据时尚无需求指标"})
    return result


def make_topics(seeds, rows):
    seed_lookup = {normalized(s['keyword']): s['keyword'] for s in seeds}
    buckets, deferred = defaultdict(list), []
    for r in rows:
        obj, task, reason = classify(r['keyword'])
        seed = seed_lookup.get(normalized(OBJECT_MAP[obj][3])) if obj else None
        if not seed:
            deferred.append({"id":r['id'], "keyword":r['keyword'], "reason":reason or "对应类别未包含于本次基础词"})
        else:
            buckets[(obj,task,r['market'],r['language'],seed)].append(r)
    topics = []
    for (obj,task,market,language,seed), members in sorted(buckets.items(), key=lambda item: str(item[0])):
        members.sort(key=lambda r:(r['volume'] is None,-(r['volume'] or 0),r['kd'] is None,r['kd'] if r['kd'] is not None else 101,normalized(r['keyword'])))
        primary = members[0]
        topics.append({"id":digest([obj,task,market,language])[:20], "object":obj, "task":task, "seed":seed, "title":f"{OBJECT_MAP[obj][1]} · {TASKS[task][0]}", "market":market, "language":language,
            "primary":primary['keyword'], "volume":primary['volume'], "kd":primary['kd'], "member_ids":[r['id'] for r in members], "keyword_count":len(members),
            "sample_keywords":[r['keyword'] for r in members[:8]], "volume_conflicts":sum(r['volume_conflict'] for r in members), "kd_missing":sum(r['kd'] is None for r in members)})
    return topics, deferred


def page_role(page):
    path = urlsplit(page['url']).path.lower()
    if path.startswith('/wp-content/') or re.search(r'\.(?:jpg|jpeg|png|gif|webp|svg|pdf|zip|mp4|ico|css|js|xml|json|txt)$',path):
        return 'asset'
    if path.startswith(('/tag/','/product-tag/','/category/','/author/')) or re.search(r'/page/\d+/?$',path):
        return 'index'
    return 'content'


def page_object(page):
    path = unquote(urlsplit(page['url']).path).replace('-', ' ')
    # The most specific final URL segment avoids classifying every baby URL as the hub.
    leaf = path.rstrip('/').split('/')[-1]
    obj = classify(leaf or page.get('title',''), filter_scope=False)[0]
    if not obj:
        obj = classify(page.get('title',''), filter_scope=False)[0]
    task = classify(leaf, filter_scope=False)[1]
    if '/custom silicone products/' in path and task=='product':
        task='sourcing'
    if not leaf:
        task=classify(page.get('title',''), filter_scope=False)[1]
    return obj, task


def evidence_lines(pages):
    sets = [set(p.get('body','').splitlines()) for p in pages if p.get('body')]
    counts = Counter(line.strip() for lines in sets for line in lines)
    result = {}
    for p in pages:
        result[p['url']] = [line.strip() for line in p.get('body','').splitlines() if ((len(line.strip()) >= 80 and re.search(r'[.!?;:]',line)) or (len(line.strip()) >= 25 and ':' in line and re.search(r'\d',line))) and line.strip()!=p.get('title') and not (len(sets)>2 and counts[line.strip()] > len(sets)*0.5)]
    return result


def decide(topics, pages, competitors):
    pages = [p for p in pages if page_role(p)=='content']
    lines = evidence_lines(pages)
    competitor_lines = evidence_lines(competitors)
    for topic in topics:
        obj, task = topic['object'], topic['task']
        same_object = [p for p in pages if p.get('status')!='excluded' and (page_object(p)[0]==obj or (obj in {'cup','bottle'} and re.search(r'cups?.{0,8}bottles?|bottles?.{0,8}cups?', normalized(unquote(urlsplit(p['url']).path).rstrip('/').split('/')[-1]))))]
        exact = [p for p in same_object if page_object(p)[1] == task]
        candidates = exact or (same_object if task in {'product','sourcing'} else [])
        candidates.sort(key=lambda p:(p.get('status')!='read',urlsplit(p['url']).path=='/',len(urlsplit(p['url']).path),p['url']))
        page = next((p for p in candidates if p.get('status')=='read'),None)
        requirements = []
        for title, pattern in REQUIREMENTS[task]:
            evidence = next((line for line in lines.get(page['url'],[]) if re.search(pattern,line,re.I)),None) if page else None
            requirements.append({"title":title,"covered":bool(evidence),"evidence": {"url":page['url'],"quote":evidence} if evidence else None})
        matched_competitors = []
        for p in competitors:
            po, pt = page_object(p)
            if p.get('status')=='read':
                quote = next((line.strip() for line in p.get('body','').splitlines() if len(line.strip())>=60 and re.search(OBJECT_MAP[obj][2],line,re.I)),None)
                if quote:
                    matched_competitors.append({"url":p['url'],"title":p['title'],"quote":quote,"basis":"竞品内容参考；不代表本站能力或搜索排名"})
        missing = [r['title'] for r in requirements if not r['covered']]
        for requirement, (_, pattern) in zip(requirements, REQUIREMENTS[task]):
            requirement['competitor_examples'] = [
                {'url': p['url'], 'quote': line}
                for p in matched_competitors
                for line in competitor_lines.get(p['url'], [])
                if re.search(pattern, line, re.I) and (page_object(p)[0]==obj or re.search(OBJECT_MAP[obj][2],line,re.I))
            ][:3]
        if page:
            action = 'optimize' if missing else 'keep'
            reason = f"已读页面覆盖 {len(requirements)-len(missing)}/{len(requirements)} 个检查项。" + ("缺少可定位的正文依据："+'、'.join(missing) if missing else "当前检查未发现明确内容缺口，保留并人工复核。")
        elif candidates:
            action, reason = 'review', '发现相关现有 URL 尚未读取，不能据此建议重写或新增；先补读正文。'
        else:
            action, reason = 'new', '已保存页面清单中未匹配同对象/任务页面，提出新增候选；发布前仍须核对全站与搜索结果。'
        # Unsupported adjacent products must not be expanded into asserted business lines.
        if not same_object and obj in {'pacifier','pacifier_clip','feeder','baby_toy'}:
            action, reason = 'review', '基础词只提供上位业务范围；尚无明确现有产品页支持这个细分品类，先确认业务范围。'
        priority = '优先核对' if task in {'sourcing','cost','product'} and action not in {'review','keep'} else ('补充证据' if action=='review' else '常规核对')
        value = ('直接采购支持' if task in {'sourcing','cost','process'} else '选型与信任支持' if task in {'compare','safety'} else '产品发现与使用支持')
        value_reason = ('回答采购条件与供应商选择问题，可承接询盘；仍需验证目标市场需求。' if task in {'sourcing','cost','process'} else '覆盖选型或使用阶段，优先复用产品页/FAQ，并检查是否主要面向零售用户。')
        topic.update(value=value,value_reason=value_reason,action=action,reason=reason,priority=priority,target_url=page['url'] if page else None,
            candidates=[{"url":p['url'],"title":p.get('title',p['url']),"status":p.get('status')} for p in candidates],
            requirements=requirements,competitors=matched_competitors,
            related_pages=[{"url":p['url'],"title":p.get('title','')} for p in same_object if p.get('status')=='read'],
            decision_basis=[f"价值判断：{value}。{value_reason}",f"基础词：{topic['seed']}",f"客户任务：{TASKS[task][0]}",f"覆盖 {topic['keyword_count']} 个实测词；未知或冲突指标不按零处理", "排名/询盘价值未实测；竞品覆盖不是排名证据"])
    return sorted(topics,key=lambda t:(t['volume'] is None,-(t['volume'] or 0),t['kd'] is None,t['kd'] if t['kd'] is not None else 101,t['id']))


def brief(topic):
    obj = OBJECT_MAP[topic['object']][1]
    action = topic['action']
    dimensions = {
        'teether':'形状、纹理、握持方式、硬度、清洁条件与测试适用范围',
        'feeding_set':'套装组成、碗盘吸附结构、勺杯搭配、年龄范围、包装与定制边界',
        'bib':'颈围调节、口袋结构、重量、印刷、折叠和清洁条件',
        'spoon':'勺头尺寸、手柄结构、软硬度、材料组合与批量包装',
        'plate':'分格、容量、底部吸附、餐椅托盘适配与温度条件',
        'bowl':'容量、口径、吸附底座、盖子和清洁条件',
        'cup':'容量、开口/吸管/杯盖方案、防漏条件与零件清洁',
        'bottle':'容量、材料组合、密封部件和适用温度',
        'mat':'尺寸、厚度、防滑结构、表面纹理与收纳方式',
        'food_storage':'单格容量、密封方式、冷冻/加热条件与分装方式',
        'mold':'产品图样、分模、模腔、材料选择、试样与模具归属',
        'manufacturing':'材料、结构、模具、打样、起订条件与检验交付',
        'odm':'想法/样品输入、设计迭代、打样验证与量产交接',
    }.get(topic['object'], '产品适用场景、规格、材料、定制范围与验证资料')
    instructions = {
        '规格与可选配置':f'增加规格对照表，列出{dimensions}中的可选项和已验证范围；未知参数留作内部核对项。',
        '询盘与选型下一步':'在产品介绍后提供清楚的咨询入口，说明想法、参考图片、样品或图纸均可开始；已知数量和规格可选填，不强制用户先准备完整资料。',
        '材料及依据':'区分材料类别、具体产品与测试报告；列出报告对应对象、用途和日期，不把材料符合要求写成整件产品获批。',
        '产品与适用场景':f'按目标客户使用场景解释产品差异，结合{dimensions}说明适配边界，并链接对应产品。',
        'MOQ 与样品条件':'分别解释现有模具与新开模、单款与混色的 MOQ/样品条件；未知数值需业务确认，允许先咨询。',
        '交期与流程':'按需求核对、设计/模具、打样确认、生产与交付列步骤；说明哪些输入影响周期，不编造承诺天数。',
        '质量与证明材料':'按产品/市场列需要核对的测试和检验材料，并关联实际可公开证明；不要复制竞品认证声明。',
        '清洁步骤':'按拆卸、清洗、冲洗和干燥组织操作步骤；各步骤说明对应产品范围，引用企业验证资料。',
        '温度与设备条件':'核对具体产品对洗碗机、煮沸及其它设备的适用条件；未验证温度和时间不得写成通用建议。',
        '成本构成':'拆分产品材料、结构、模具、表面/印刷、包装和检验对报价的影响，区分一次性费用与单件费用。',
        '报价所需资料':'给出可选资料清单及报价假设，接受想法/样品起步；对尚未明确的数量和规格给出下一步沟通方式。',
    }
    modules = []
    for req in topic['requirements']:
        modules.append({"heading":f"{obj}：{req['title']}", "question":f"回答“{topic['primary']}”背后的{req['title']}问题；结合{dimensions}说明选择依据和限制。",
            "instructions": ("保留已有内容并核对范围。" if req['covered'] else instructions.get(req['title'],f"补充{req['title']}：围绕{dimensions}解释选择依据、流程与限制；未获得企业证据时保留待确认。")),
            "evidence":req['evidence'],"needs_confirmation":not req['covered']})
    return {"kind":action, "title":topic['title'], "target_url":topic['target_url'], "primary_keyword":topic['primary'],
        "buyer_task":TASKS[topic['task']][0],"content_type":"产品/供货页面" if topic['task'] in {'product','sourcing'} else "选型或使用指南 / 可先评估现有页模块",
        "optimization":[{"gap":r['title'],"suggestion":instructions.get(r['title'],f"在现有页面补充“{r['title']}”模块：围绕{dimensions}给出可核对的选项/流程/限制，链接对应资料。先核对原文是否已有，保留已确认内容。")} for r in topic['requirements'] if not r['covered']] if action=='optimize' else [],
        "outline":modules if action in {'new','optimize'} else [],
        "supporting_keywords":topic['sample_keywords'],"internal_links":topic['related_pages'],
        "cta":"从想法、参考图片、样品或图纸开始咨询；已知的数量和规格可帮助核对方案，资料不完整也可联系。" if topic['task'] in {'sourcing','product','cost','process'} else "查看相关产品与材料说明；使用条件不明时联系企业核实。",
        "publication_gate":"人工核对业务范围、原文覆盖、相似 URL 与 SERP；未知认证/交期/参数不得进入发布稿。"}


def build_result(snapshot):
    seeds = snapshot['seeds']
    rows = snapshot['keywords']
    topics, deferred = make_topics(seeds,rows)
    topics = decide(topics,snapshot['pages'],snapshot['competitors'])
    for topic in topics:
        topic['brief'] = brief(topic)
    assigned = [key for topic in topics for key in topic['member_ids']]
    assert len(assigned)==len(set(assigned)) and set(assigned)|{r['id'] for r in deferred}=={r['id'] for r in rows}
    webpages = [p for p in snapshot['pages'] if page_role(p)!='asset']
    assets = [p for p in snapshot['pages'] if page_role(p)=='asset']
    return {"version":VERSION,"accounting":snapshot['accounting'],"expansions":expand(seeds,rows),"topics":topics,"deferred":deferred,
        "summary":{"topics":len(topics),"assigned":len(assigned),"deferred":len(deferred),"actions":dict(Counter(t['action'] for t in topics))},
        "coverage":{"own_read":sum(p.get('status')=='read' for p in webpages),"own_discovered":len(webpages),"asset_read":sum(p.get('status')=='read' for p in assets),"asset_total":len(assets),"index_pages":sum(page_role(p)=='index' for p in webpages),"competitor_read":sum(p.get('status')=='read' for p in snapshot['competitors']),"competitor_total":len(snapshot['competitors'])},
        "limits":["图片/文档附件、标签、作者及分页索引不作为主题主目标页；其读取记录仍保留。","规则生成的内容主题尚未经过 SERP 同页验证。","URL 存在但未读正文时保留待核对；新增仅为候选。","按正文词项定位覆盖属于可复核检查，不是内容质量或转化保证。"]}
