"""Persisted content planning over explicit project inputs and frozen evidence."""
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .config import ROOT
from .content_strategy import VERSION, build_result, consolidate, digest, parse_seeds
from .crawl import CrawlError, fetch_firecrawl, normalize_url, parse_html
from .intake import parse_records
from .store import encode, now, uid


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[str] = Field(min_length=1)


def local_file(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT / 'backend/tests/data') or not path.is_file():
        raise ValueError('规划输入须为已配置的 tests/data 文件')
    return path


def freeze_keywords(config, names):
    allowed = {Path(p).name: local_file(p) for p in config.keyword_files}
    if len(names) != len(set(names)) or not set(names) <= allowed.keys():
        raise ValueError('请选择本项目已配置的关键词文件，不接受任意文件路径')
    records, files = {}, []
    for name in sorted(names):
        raw = allowed[name].read_bytes()
        records[name] = parse_records({'kind':'keyword','format':'xlsx','text':base64.b64encode(raw).decode(),'market':config.market,'language':config.language})
        files.append({'name':name,'sha256':hashlib.sha256(raw).hexdigest(),'rows':len(records[name])})
    keywords, accounting, rejected = consolidate(records)
    seed_raw = local_file(config.seed_file).read_bytes()
    return {'version':VERSION,'files':files,'seed_file':{'name':Path(config.seed_file).name,'sha256':hashlib.sha256(seed_raw).hexdigest()},
            'seeds':parse_seeds(seed_raw.decode('utf-8-sig')),'keywords':keywords,'accounting':accounting,'rejected':rejected}


def collect_evidence(store, project_id, config, settings, snapshot, checkpoint):
    pages = {p['url']: {k:p.get(k) for k in ('url','title','body','status','updated_at')} for p in snapshot['sources'] if p['kind']=='page' and p['url']}
    with store.connect() as db:
        cached = {r['url']:json.loads(r['payload']) for r in db.execute('SELECT url,payload FROM content_assets WHERE project_id=?',(project_id,))}
    own_host = urlsplit(snapshot['project']['site_url']).hostname
    wanted = [(url,'own') for url in config.own_evidence_urls] + [(url,'competitor') for url in config.competitor_urls]
    competitors = []
    for index, (url, owner) in enumerate(wanted,1):
        url = normalize_url(url)
        checkpoint(f'2/5 核对页面证据 {index}/{len(wanted)}：{urlsplit(url).hostname}')
        if owner=='own' and urlsplit(url).hostname != own_host:
            raise ValueError('本站补充页面必须属于项目网站')
        existing = pages.get(url) if owner=='own' else None
        if existing and existing['status']=='excluded':
            continue
        asset = existing if existing and existing['status']=='read' else cached.get(url)
        if not asset or asset.get('status')!='read':
            try:
                final, _, raw = fetch_firecrawl(url,settings,allowed_host=urlsplit(url).hostname)
                title, body, _ = parse_html(raw,final)
                if not body.strip():
                    raise ValueError('页面正文为空')
                asset = {'url':url,'title':title,'body':body,'status':'read','owner':owner,'fetched_at':now(),'sha256':hashlib.sha256(raw).hexdigest()}
            except Exception as error:
                # Provider exceptions can contain credential-bearing request details.
                message = str(error) if isinstance(error,CrawlError) else '本页采集失败，保留待核对；可在新运行中重试'
                asset = {'url':url,'title':url,'body':'','status':'failed','owner':owner,'fetched_at':now(),'error':message}
            checkpoint(f'2/5 已处理页面 {index}/{len(wanted)}')
            with store.connect() as db:
                db.execute('INSERT OR REPLACE INTO content_assets VALUES (?,?,?)',(project_id,url,encode(asset)))
        if owner=='own':
            pages[url] = asset
        else:
            competitors.append(asset)
    return sorted(pages.values(),key=lambda p:p['url']), sorted(competitors,key=lambda p:p['url'])


def register_content_workflow(app, store, settings):
    prefix = '/api/projects/{project_id}/content-plans'

    def config_for(project_id):
        store.project(project_id)
        config = settings.content_workflows.get(project_id)
        if not config:
            raise ValueError('此项目尚未配置内容规划输入，请在 config.json 的 content_workflows 中配置')
        return config

    def run_for(project_id, run_id, full=False):
        store.project(project_id)
        fields = 'r.*' if full else 'r.id,r.project_id,r.job_id,r.version,r.input_hash,r.result_hash,r.replay_of,r.created_at'
        with store.connect() as db:
            row = db.execute(f'SELECT {fields},j.status,j.progress,j.error FROM content_runs r JOIN jobs j ON j.id=r.job_id AND j.project_id=r.project_id WHERE r.project_id=? AND r.id=?',(project_id,run_id)).fetchone()
        if not row:
            raise LookupError('规划版本不存在')
        return dict(row)

    def checkpoint(project_id, job_id, progress):
        if store.cancelled(project_id,job_id):
            raise InterruptedError()
        store.update_job(project_id,job_id,status='running',progress=progress)

    def work(project_id, job_id, run_id, names, source_run):
        check = lambda message: checkpoint(project_id,job_id,message)
        try:
            check('1/5 固定基础词与关键词输入')
            if source_run:
                saved = run_for(project_id,source_run,True)
                frozen = json.loads(saved['input_json'])
            else:
                config = config_for(project_id)
                frozen = freeze_keywords(config,names)
                snapshot = store.snapshot(project_id)
                frozen['project'] = snapshot['project']
                frozen['profile'] = snapshot['profiles'][0] if snapshot['profiles'] else None
                frozen['pages'], frozen['competitors'] = collect_evidence(store,project_id,config,settings,snapshot,check)
            check('3/5 按产品对象与客户任务形成主题')
            input_hash = digest(frozen)
            with store.connect() as db:
                db.execute('UPDATE content_runs SET input_json=?,input_hash=? WHERE project_id=? AND id=?',(encode(frozen),input_hash,project_id,run_id))
            check('4/5 匹配已有页面，检查内容覆盖')
            result = build_result(frozen)
            check('5/5 生成优化建议与新增大纲')
            result_hash = digest(result)
            if source_run and result_hash != saved['result_hash']:
                raise ValueError('快照重放结果与原版本不一致，未保存结果')
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                job = db.execute('SELECT status FROM jobs WHERE project_id=? AND id=?',(project_id,job_id)).fetchone()
                if not job or job['status'] not in {'queued','running'}:
                    raise InterruptedError()
                db.execute('UPDATE content_runs SET result_json=?,result_hash=? WHERE project_id=? AND id=?',(encode(result),result_hash,project_id,run_id))
                db.execute("UPDATE jobs SET status='succeeded',progress='5/5 内容规划完成',updated_at=?,version=version+1 WHERE project_id=? AND id=?",(now(),project_id,job_id))
                store.event(db,project_id,'content_plan_completed',{'run_id':run_id,'input_hash':input_hash,'result_hash':result_hash})
        except InterruptedError:
            return
        except Exception as error:
            message = str(error) if isinstance(error,ValueError) else '内容规划执行失败；原始数据保留，可重新运行'
            store.update_job(project_id,job_id,status='failed',error=message,progress='执行中断')

    def start(project_id, names=None, source_run=None):
        job_id = store.create_job(project_id,'content_plan')
        run_id = uid()
        try:
            with store.connect() as db:
                db.execute('INSERT INTO content_runs (id,project_id,job_id,version,replay_of,created_at) VALUES (?,?,?,?,?,?)',(run_id,project_id,job_id,VERSION,source_run,now()))
            app.state.executor.submit(work,project_id,job_id,run_id,names,source_run)
        except Exception:
            store.update_job(project_id,job_id,status='failed',error='无法启动内容规划')
            raise
        return {'id':run_id,'job_id':job_id}

    @app.get(prefix)
    def setup(project_id: str):
        store.project(project_id)
        config = settings.content_workflows.get(project_id)
        with store.connect() as db:
            ids = [r['id'] for r in db.execute('SELECT id FROM content_runs WHERE project_id=? ORDER BY created_at DESC',(project_id,))]
        return {'current_version':VERSION,'configured':bool(config),'files':[{'name':Path(p).name,'bytes':local_file(p).stat().st_size} for p in config.keyword_files] if config else [],
                'seed_file':Path(config.seed_file).name if config else None,'market':config.market if config else None,'language':config.language if config else None,
                'competitor_urls':config.competitor_urls if config else [],'own_evidence_urls':config.own_evidence_urls if config else [],'runs':[run_for(project_id,i) for i in ids]}

    @app.post(prefix)
    def create(project_id: str, data: PlanInput):
        config = config_for(project_id)
        allowed = {Path(p).name for p in config.keyword_files}
        if not set(data.files)<=allowed or len(data.files)!=len(set(data.files)):
            raise ValueError('关键词文件不属于本项目配置或重复')
        return start(project_id,data.files)

    @app.post(prefix+'/{run_id}/replay')
    def replay(project_id: str, run_id: str):
        saved = run_for(project_id,run_id,True)
        if saved['status']!='succeeded' or saved['version']!=VERSION:
            raise ValueError('仅可重放当前规则版本的已完成规划')
        return start(project_id,source_run=run_id)

    @app.get(prefix+'/{run_id}')
    def result(project_id: str, run_id: str):
        saved = run_for(project_id,run_id,True)
        frozen = json.loads(saved.pop('input_json') or '{}')
        outcome = json.loads(saved.pop('result_json') or '{}')
        # Complete members and raw observations remain accessible in detail/export.
        topics = [{k:v for k,v in t.items() if k not in {'member_ids','brief','requirements','competitors','candidates','related_pages'}} for t in outcome.get('topics',[])]
        return {**saved,'result':{k:v for k,v in outcome.items() if k not in {'topics','deferred'}},'topics':topics,
                'inputs':{k:frozen.get(k) for k in ('files','seed_file','seeds','accounting')},
                'evidence':[{'url':p['url'],'title':p.get('title'),'status':p['status'],'error':p.get('error')} for p in frozen.get('competitors',[])]}

    @app.get(prefix+'/{run_id}/keywords')
    def members(project_id: str, run_id: str, topic: str | None=None, deferred: bool=False, page: int=Query(1,ge=1), size: int=Query(10,ge=1,le=100), q: str=''):
        saved = run_for(project_id,run_id,True)
        frozen, outcome = json.loads(saved['input_json'] or '{}'), json.loads(saved['result_json'] or '{}')
        rows, detail = frozen.get('keywords',[]), None
        if topic:
            detail = next((t for t in outcome.get('topics',[]) if t['id']==topic),None)
            if not detail:
                raise LookupError('主题不存在')
            keys = {key:i for i,key in enumerate(detail['member_ids'])}
            rows = sorted((r for r in rows if r['id'] in keys),key=lambda r:keys[r['id']])
        if deferred:
            reasons = {r['id']:r['reason'] for r in outcome.get('deferred',[])}
            rows = [{**r,'reason':reasons[r['id']]} for r in rows if r['id'] in reasons]
        rows = [r for r in rows if q.casefold() in r['keyword'].casefold()]
        return {'topic':{k:v for k,v in detail.items() if k!='member_ids'} if detail else None,'total':len(rows),'page':page,'size':size,'rows':rows[(page-1)*size:page*size]}

    @app.get(prefix+'/{run_id}/export')
    def export(project_id: str, run_id: str):
        saved = run_for(project_id,run_id,True)
        if saved['status']!='succeeded':
            raise ValueError('规划尚未完成')
        payload = {'run':{k:v for k,v in saved.items() if k not in {'input_json','result_json'}},'input':json.loads(saved['input_json']),'result':json.loads(saved['result_json'])}
        return Response(encode(payload),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="pagggle-plan-{run_id}.json"'})
