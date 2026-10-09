"""Use the user's real snapshot; no invented keywords, customer records or model responses."""
import hashlib
import json
import os
import sqlite3

import httpx
import pytest

from pagggle.config import ROOT, load_settings
from pagggle.content_strategy import digest, page_role
from pagggle.content_workflow import freeze_keywords
from pagggle.skill_execution import SkillExecution, load_skills
from pagggle.skill_strategy import PageNotes, assessment_inputs, complete, keyword_batch_limit, pack, parallel, split_body
from pagggle.store import Store, now, uid

pytestmark=pytest.mark.skipif(os.environ.get('PAGGGLE_VERIFY_FIXED_DATA')!='1',reason='Requires user-authorized private files and real site snapshot')
PID='3569ed46f2554178b327c2e7c58c8b3b'


@pytest.fixture(scope='module')
def snapshot():
    with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
        return json.loads(db.execute('SELECT input_json FROM content_runs WHERE project_id=? AND id=?',(PID,'ebeeb183544d4cb0a3a87a01823caa27')).fetchone()[0])


def test_all_keywords_are_batched_without_loss_and_missing_ids_are_rejected(snapshot):
    rows=[{'i':i,'keyword':r['keyword']} for i,r in enumerate(snapshot['keywords'])]
    batches=pack(rows,60000,{'source':'real saved input'},600)
    assert len(batches)>1
    assert [r for b in batches for r in b]==rows
    assert all(len(json.dumps({'source':'real saved input','items':b},ensure_ascii=False))<=60000 for b in batches)
    complete([r['i'] for b in batches for r in b],range(len(rows)))
    with pytest.raises(ValueError):complete([r['i'] for b in batches for r in b][:-1],range(len(rows)))


def test_output_budget_splits_all_real_keywords_before_submission(snapshot):
    settings=load_settings()
    limit=keyword_batch_limit(settings)
    assert 1<=limit<settings.strategy_keyword_batch_size
    rows=[{'i':i,'keyword':r['keyword']} for i,r in enumerate(snapshot['keywords'])]
    batches=pack(rows,settings.model_input_chars,limit=limit)
    assert [r for batch in batches for r in batch]==rows
    assert max(map(len,batches))<=limit
    assert keyword_batch_limit(settings.model_copy(update={'model_max_tokens':6000}))<limit


def test_real_page_splitting_counts_escapes_and_retains_every_character(snapshot):
    pages=[p for p in snapshot['pages']+snapshot['competitors'] if p['status']=='read' and p['body']]
    split_count=0
    for p in pages:
        context=lambda body:{'items':[{'title':p['title'],'url':p['url'],'body':body}]}
        parts=split_body(p['body'],context,1000)
        assert ''.join(parts)==p['body']
        assert all(len(json.dumps(context(part),ensure_ascii=False))<=1000 for part in parts)
        split_count+=len(parts)>1
    assert split_count>0
    with pytest.raises(ValueError,match='正文之外'):
        split_body(pages[0]['body'],lambda body:{'title':pages[0]['body'],'body':body},10)


def test_long_target_is_split_before_assessment_and_short_target_is_unchanged(snapshot):
    page=max((p for p in snapshot['pages'] if p['status']=='read'),key=lambda p:len(p['body']))
    target={k:page[k] for k in ('url','title','body')}
    context={'site':snapshot['project']['site_url'],'seeds':snapshot['seeds']}
    parts=assessment_inputs(context,target,5000)
    assert len(parts)>1
    assert ''.join(p['target_full_text']['body'] for p in parts)==target['body']
    assert all(len(json.dumps(p,ensure_ascii=False))<=5000 and p['partial_target'] for p in parts)
    assert assessment_inputs(context,target,200000)==[{**context,'target_full_text':target}]


@pytest.mark.parametrize('workers',[1,3,10])
def test_thread_pool_starts_configured_workers_together_and_never_exceeds_limit(snapshot,workers):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    rows=snapshot['keywords'][:30]
    lock=threading.Lock();full=threading.Event();release=threading.Event()
    active=peak=0
    def work(row):
        nonlocal active,peak
        with lock:
            active+=1;peak=max(peak,active)
            if active==workers:full.set()
        try:
            assert release.wait(5)
            return row['id']
        finally:
            with lock:active-=1
    with ThreadPoolExecutor(max_workers=1) as runner:
        pending=runner.submit(parallel,rows,work,workers,lambda _:None,'验证')
        try:assert full.wait(5), 'Requests did not start concurrently'
        finally:release.set()
        assert pending.result(timeout=10)==[r['id'] for r in rows]
    assert peak==workers


def test_progress_follows_completion_without_reordering_results(snapshot):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    rows=snapshot['keywords'][:10]
    first=rows[0]['id'];release=threading.Event();progress=threading.Event()
    def work(row):
        if row['id']==first:assert release.wait(5)
        return row['id']
    def checkpoint(message):
        if message:progress.set()
    with ThreadPoolExecutor(max_workers=1) as runner:
        pending=runner.submit(parallel,rows,work,10,checkpoint,'验证')
        try:
            assert progress.wait(5), 'Slow first request blocked completion reporting'
            assert not pending.done()
        finally:release.set()
        assert pending.result(timeout=10)==[r['id'] for r in rows]


def test_failed_or_cancelled_pool_does_not_start_queued_requests(snapshot):
    started=[]
    def failure(row):
        started.append(row['id'])
        raise ValueError('HTTP 402')
    with pytest.raises(ValueError,match='HTTP 402'):
        parallel(snapshot['keywords'][:30],failure,1,lambda _:None,'验证')
    assert started==[snapshot['keywords'][0]['id']]
    started.clear()
    def cancelled(_):raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError):
        parallel(snapshot['keywords'][:30],lambda row:started.append(row['id']),10,cancelled,'验证')
    assert not started


def test_changed_real_file_selection_changes_strategy_input(snapshot):
    settings=load_settings();config=settings.content_workflows[PID]
    one=freeze_keywords(config,['silicone-teether_all-keywords_us_2026-10-09.xlsx'])
    assert len(one['keywords'])<len(snapshot['keywords'])
    assert digest(one['keywords'])!=digest(snapshot['keywords'])
    assert {r['id'] for r in one['keywords']} <= {r['id'] for r in snapshot['keywords']}
    assert one['seeds']==snapshot['seeds']


@pytest.mark.parametrize('http_status',[None,402])
def test_real_skill_text_is_in_actual_request_and_network_failure_is_not_rule_fallback(snapshot,tmp_path,monkeypatch,http_status):
    path=tmp_path/'copy.sqlite3'
    with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as src,sqlite3.connect(path) as dst:src.backup(dst)
    store=Store(path);store.recover_jobs();settings=load_settings();skills=load_skills(settings)
    job=store.create_job(PID,'content_plan');run=uid()
    with store.connect() as db:db.execute('INSERT INTO content_runs (id,project_id,job_id,version,created_at) VALUES (?,?,?,?,?)',(run,PID,job,'skill-content-strategy-1',now()))
    captured=[]
    class Offline:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            captured.append(kwargs['json'])
            if http_status:return httpx.Response(http_status)
            raise httpx.ConnectError('offline verification')
    monkeypatch.setattr('pagggle.skill_execution.httpx.Client',Offline)
    page=next(p for p in snapshot['pages'] if p['status']=='read' and page_role(p)=='content')
    engine=SkillExecution(store,PID,run,settings,skills,lambda _:None)
    with pytest.raises(ValueError,match='HTTP 402' if http_status else '未回退规则'):
        engine.call('page_inventory',{'items':[{'id':0,'body':page['body']}]},PageNotes,'读取真实原文',names=('content-strategy','seo-audit'))
    assert len(captured)==1
    system=captured[0]['messages'][0]['content']
    for name,item in skills.items():
        assert item['instructions'] in system
        assert item['sha256']==hashlib.sha256(item['instructions'].encode()).hexdigest()
    assert settings.api_key.get_secret_value() not in system
    assert settings.api_key.get_secret_value() not in captured[0]['messages'][1]['content']
    with store.connect() as db:
        record=json.loads(db.execute('SELECT payload FROM content_skill_calls WHERE project_id=? AND run_id=?',(PID,run)).fetchone()[0])
    assert record['status']=='failed' and record['output'] is None
    if http_status:assert record['http_status']==http_status
    assert record['request']==captured[0]
    assert not any(c['status']=='succeeded' for c in engine.records)


def test_cancelled_skill_execution_never_sends_a_request(tmp_path,monkeypatch):
    settings=load_settings();skills=load_skills(settings)
    def cancelled(_):raise InterruptedError()
    def forbidden(*args,**kwargs):pytest.fail('Cancelled run sent a model request')
    monkeypatch.setattr('pagggle.skill_execution.httpx.Client',forbidden)
    engine=SkillExecution(None,PID,'cancelled',settings,skills,cancelled)
    with pytest.raises(InterruptedError):engine.call('page_inventory',{},PageNotes,'取消校验')


def test_actual_model_quotes_resolve_layout_whitespace_without_accepting_rewrites():
    from pagggle.skill_strategy import original_quote
    with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
        row=db.execute("SELECT payload FROM content_skill_calls WHERE project_id=? AND run_id=? AND json_extract(payload,'$.status')='failed'",(PID,'72ccdd6af5bf42cc81563b6975133c83')).fetchone()
    if not row:pytest.skip('Requires the real first model response with HTML line breaks')
    record=json.loads(row[0]);pages={p['id']:p for p in json.loads(record['request']['messages'][1]['content'])['items']}
    notes=PageNotes.model_validate_json(record['attempts'][-1]['content'])
    adjusted=0
    for page in notes.pages:
        for quote in page.quotes:
            source=pages[page.id]['body']
            exact=original_quote(source,quote)
            assert exact in source
            adjusted+=exact!=quote
            with pytest.raises(ValueError):original_quote(source,quote+' '+quote)
    assert adjusted>0


def test_actual_incomplete_assignment_keeps_unique_results_and_retries_ambiguous_ids():
    from pagggle.skill_strategy import Assignments, unique_assignments
    with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
        row=db.execute("SELECT payload FROM content_skill_calls WHERE project_id=? AND run_id=? AND stage='keyword_assignment' AND json_extract(payload,'$.status')='failed'",(PID,'cb719212f7104576a2acfcbecb65ddd7')).fetchone()
    if not row:pytest.skip('Requires actual keyword-assignment response with duplicate/missing IDs')
    record=json.loads(row[0]);expected={r['i'] for r in json.loads(record['request']['messages'][1]['content'])['items']}
    for attempt in record['attempts']:
        output=Assignments.model_validate_json(attempt['content']).model_dump()
        resolved,unresolved=unique_assignments(output,expected)
        ids=[i for g in resolved['groups']+resolved['deferred'] for i in g['members']]
        assert len(ids)==len(set(ids))
        assert set(ids).isdisjoint(unresolved)
        assert set(ids)|unresolved==expected
        assert unresolved


def test_actual_failed_responses_receive_specific_field_and_allowed_seed_feedback():
    from pydantic import ValidationError
    from pagggle.skill_execution import validation_feedback
    from pagggle.skill_strategy import Assignments, validate_assignments
    with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
        records=[json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM content_skill_calls WHERE project_id=? AND run_id=? AND stage='keyword_assignment' AND json_extract(payload,'$.status')='failed'",
            (PID,'65e64cfefcc94d24a6cc81ee5564baa1'))]
    if not records:pytest.skip('Requires actual failed recovery responses')
    fields=seed_errors=0
    for record in records:
        context=json.loads(record['request']['messages'][1]['content'])
        ids={r['i'] for r in context['items']}
        seeds={s['keyword'] for s in context['business']['seeds']}
        taxonomy={t['id'] for t in context['catalog']}
        for attempt in record['attempts']:
            try:parsed=Assignments.model_validate_json(attempt['content'])
            except ValidationError as error:
                feedback=validation_feedback(error)
                for item in error.errors():
                    assert '.'.join(map(str,item['loc'])) in feedback
                    assert item['msg'] in feedback
                fields+=1
                continue
            if any(g.seed not in seeds for g in parsed.groups):
                with pytest.raises(ValueError) as error:validate_assignments(parsed,ids,taxonomy,seeds)
                if '主题基础词' in str(error.value):
                    assert all(seed in validation_feedback(error.value) for seed in seeds)
                    seed_errors+=1
    assert fields and seed_errors


@pytest.mark.parametrize('omit_once',[False,True])
def test_consolidation_shards_compare_all_topics_and_preserve_original_definitions(omit_once):
    """Identity-only transport stub tests accounting, not fabricated semantic decisions."""
    from pagggle.skill_strategy import Topic, consolidate_topics
    with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
        row=db.execute("SELECT payload FROM content_skill_calls WHERE project_id=? AND run_id=? AND stage='topic_consolidation' LIMIT 1",
            (PID,'80e85da161e74f6e8996c8e6d47dcd73')).fetchone()
    if not row:pytest.skip('Requires actual consolidation request that exceeded output budget')
    context=json.loads(json.loads(row[0])['request']['messages'][1]['content'])
    pending=[{**{k:g[k] for k in Topic.model_fields},'members':[g['i']]} for g in context['items'][:5]]
    settings=load_settings().model_copy(update={'model_max_tokens':1088})
    comparisons=[]
    class IdentityTransport:
        omitted=False
        def call(self,stage,payload,schema,instruction,validate):
            assert len(json.dumps(payload,ensure_ascii=False))<=settings.model_input_chars
            items=payload['items']+payload.get('incoming',[])
            assert len(items)<=2
            comparisons.append([g['question'] for g in items])
            returned=items
            if omit_once and len(items)>1 and not self.omitted:
                self.omitted=True
                returned=items[:-1]
            value=schema.model_validate({'topics':[{'representative':g['i'],'groups':[g['i']]} for g in returned]})
            validate(value)
            return value.model_dump()
    merged=consolidate_topics(pending,context['business'],IdentityTransport(),settings,lambda _:None)
    assert merged==pending
    assert len([c for c in comparisons if len(c)==2])==len(pending)*(len(pending)-1)//2+int(omit_once)


def test_actual_looping_response_retries_original_input_without_appending_truncated_output(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from pagggle.skill_execution import SkillOutputError
    from pagggle.skill_strategy import Merges
    @contextmanager
    def connect():
        with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
            db.row_factory=sqlite3.Row
            yield db
    with connect() as db:
        row=db.execute("SELECT payload FROM content_skill_calls WHERE project_id=? AND run_id=? AND stage='topic_consolidation' AND json_extract(payload,'$.status')='failed' LIMIT 1",
            (PID,'68eccd18f7944299bbdd8c22679c1629')).fetchone()
    if not row:pytest.skip('Requires actual repeated-ID response')
    record=json.loads(row[0]);attempt=record['attempts'][-1]
    assert attempt['finish_reason']=='length'
    context=json.loads(record['request']['messages'][1]['content'])
    requests=[];saved=[]
    class RecordedTransport:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            requests.append(kwargs['json'])
            return httpx.Response(200,json={'choices':[{'message':{'content':attempt['content']},'finish_reason':'length'}],'usage':attempt['usage']})
    monkeypatch.setattr('pagggle.skill_execution.httpx.Client',RecordedTransport)
    settings=load_settings()
    engine=SkillExecution(SimpleNamespace(connect=connect),PID,'offline',settings,load_skills(settings),lambda _:None)
    monkeypatch.setattr(engine,'save',lambda key,stage,payload:saved.append(payload))
    with pytest.raises(SkillOutputError):
        engine.call('topic_consolidation',context,Merges,'离线验证实际截断响应的有限重试')
    assert len(requests)==3 and saved[0]['status']=='failed'
    assert all(json.loads(r['messages'][1]['content'])==context for r in requests)
    assert all(len(r['messages'])==3 for r in requests[1:])
    assert all(attempt['content'] not in m['content'] for r in requests for m in r['messages'])


def test_actual_overlapping_merge_proposals_produce_unique_complete_members():
    from pagggle.skill_strategy import Merges, Topic, merge_topic_groups, validate_merges
    with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
        rows=db.execute("SELECT payload FROM content_skill_calls WHERE project_id=? AND run_id=? AND stage='topic_consolidation' AND json_extract(payload,'$.status')='failed'",
            (PID,'bc57bcc8fb804cf8a9899325a94e2b28')).fetchall()
    if not rows:pytest.skip('Requires actual overlapping merge responses')
    accepted=0
    for row in rows:
        record=json.loads(row[0]);context=json.loads(record['request']['messages'][1]['content'])
        items=context['items']+context.get('incoming',[]);ids={g['i'] for g in items}
        value=Merges.model_validate_json(record['attempts'][-1]['content'])
        output_ids={i for t in value.topics for i in t.groups}
        if output_ids!=ids:
            with pytest.raises(ValueError):validate_merges(value,ids)
            continue
        validate_merges(value,ids)
        accepted+=1
        assert sum(len(t.groups) for t in value.topics)>len(ids)
        indices={g['i']:i for i,g in enumerate(items)}
        outputs={'topics':[{'representative':indices[t.representative],'groups':[indices[i] for i in t.groups]} for t in value.topics]}
        groups=[{**{k:g[k] for k in Topic.model_fields},'members':[g['i']]} for g in items]
        merged=merge_topic_groups(groups,[outputs])
        complete([i for g in merged for i in g['members']],ids)
        assert all({k:g[k] for k in Topic.model_fields} in [{k:t[k] for k in Topic.model_fields} for t in groups] for g in merged)
        with pytest.raises(ValueError):validate_merges(value,ids|{max(ids)+1})
        with pytest.raises(ValueError):validate_merges(value,ids-{min(ids)})
    assert accepted>0


def test_saved_failed_merge_can_be_revalidated_without_network_or_rewriting_history(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from pagggle.skill_strategy import Merges, validate_merges
    @contextmanager
    def connect():
        with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
            db.row_factory=sqlite3.Row
            yield db
    with connect() as db:
        rows=db.execute("SELECT payload FROM content_skill_calls WHERE project_id=? AND run_id=? AND stage='topic_consolidation' AND json_extract(payload,'$.status')='failed'",
            (PID,'bc57bcc8fb804cf8a9899325a94e2b28')).fetchall()
    row=None
    for candidate in rows:
        record=json.loads(candidate['payload']);context=json.loads(record['request']['messages'][1]['content'])
        ids={g['i'] for g in context['items']+context.get('incoming',[])}
        try:validate_merges(Merges.model_validate_json(record['attempts'][-1]['content']),ids)
        except ValueError:continue
        row=candidate
        break
    if not row:pytest.skip('Requires actual overlapping merge response with no missing/unknown IDs')
    record=json.loads(row['payload']);saved=[]
    context=json.loads(record['request']['messages'][1]['content'])
    instruction=record['request']['messages'][0]['content'].split('\n\n当前阶段：',1)[1].split('\nJSON schema：',1)[0]
    ids={g['i'] for g in context['items']+context.get('incoming',[])}
    def forbidden(*args,**kwargs):pytest.fail('Saved valid response must not trigger another model request')
    monkeypatch.setattr('pagggle.skill_execution.httpx.Client',forbidden)
    settings=load_settings()
    engine=SkillExecution(SimpleNamespace(connect=connect),PID,'offline',settings,load_skills(settings),lambda _:None)
    monkeypatch.setattr(engine,'save',lambda key,stage,payload:saved.append(payload))
    output=engine.call('topic_consolidation',context,Merges,instruction,lambda value:validate_merges(value,ids))
    validate_merges(Merges.model_validate(output),ids)
    assert saved[0]['status']=='succeeded' and saved[0]['revalidated_from']
    with connect() as db:
        original=db.execute('SELECT payload FROM content_skill_calls WHERE project_id=? AND run_id=? AND request_hash=?',
            (PID,'bc57bcc8fb804cf8a9899325a94e2b28',record['request_hash'])).fetchone()
    assert original['payload']==row['payload']


def test_stricter_repair_rejects_cached_output_and_requests_correction(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from pagggle.skill_strategy import Merges
    @contextmanager
    def connect():
        with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
            db.row_factory=sqlite3.Row
            yield db
    with connect() as db:
        row=db.execute("SELECT payload FROM content_skill_calls WHERE project_id=? AND stage='topic_consolidation' AND json_extract(payload,'$.status')='succeeded' LIMIT 1",(PID,)).fetchone()
    if not row:pytest.skip('Requires a real saved consolidation response')
    record=json.loads(row['payload']);requests=[];saved=[]
    context=json.loads(record['request']['messages'][1]['content'])
    instruction=record['request']['messages'][0]['content'].split('\n\n当前阶段：',1)[1].split('\nJSON schema：',1)[0]
    class Offline:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            requests.append(kwargs['json'])
            raise httpx.ConnectError('offline verification')
    def stricter(value):raise ValueError('This saved result does not meet the stricter repair requirement')
    monkeypatch.setattr('pagggle.skill_execution.httpx.Client',Offline)
    settings=load_settings()
    engine=SkillExecution(SimpleNamespace(connect=connect),PID,'offline',settings,load_skills(settings),lambda _:None)
    monkeypatch.setattr(engine,'save',lambda key,stage,payload:saved.append(payload))
    with pytest.raises(ValueError,match='未回退规则'):
        engine.call('topic_consolidation',context,Merges,instruction,stricter)
    assert len(requests)==1 and saved[0]['status']=='failed'
    assert json.loads(requests[0]['messages'][1]['content'])==context


def test_actual_assessment_failures_identify_fields_and_reject_other_page_quotes():
    from pagggle.skill_strategy import Assessment, validate_assessment
    with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
        rows=db.execute("SELECT payload FROM content_skill_calls WHERE project_id=? AND run_id=? AND stage='content_assessment' AND json_extract(payload,'$.status')='failed'",
            (PID,'63a5cba56b1d4a6aace8a9c4a2b0e52d')).fetchall()
    if not rows:pytest.skip('Requires actual assessment responses with wrong-page quotes')
    has_target=[]
    for row in rows:
        record=json.loads(row[0]);context=json.loads(record['request']['messages'][1]['content'])
        value=Assessment.model_validate_json(record['attempts'][-1]['content'])
        target=context['target_full_text'];has_target.append(bool(target))
        with pytest.raises(ValueError) as error:validate_assessment(value,target)
        assert 'requirements[' in str(error.value) and '.quote' in str(error.value)
        assert 'null' in str(error.value) and 'covered=false' in str(error.value)
        if target:assert 'competitor_evidence' in str(error.value)
    assert set(has_target)=={True,False}
