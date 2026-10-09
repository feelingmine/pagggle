"""HTTP isolation, offline replay and cancellation on a private copy of real data."""
import json
import os
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from pagggle.app import create_app
from pagggle.config import ROOT, load_settings
from pagggle.content_strategy import VERSION, build_result, digest
from pagggle.store import Store, encode, now, uid

pytestmark=pytest.mark.skipif(os.environ.get('PAGGGLE_VERIFY_FIXED_DATA')!='1',reason='Requires private fixed website and real keyword input snapshot')


@pytest.fixture
def fixed(tmp_path):
    path=tmp_path/'copy.sqlite3'
    with sqlite3.connect(f"file:{ROOT/'data/pagggle.sqlite3'}?mode=ro",uri=True) as src, sqlite3.connect(path) as dst:src.backup(dst)
    store=Store(path)
    project=next(p for p in store.list_projects() if p['site_url']=='https://siliconeproductsmanufacturer.com/')
    store.recover_jobs()
    frozen=json.loads((ROOT/'data/verification/content-workflow/offline-input.json').read_text())
    outcome=build_result(frozen)
    job=store.create_job(project['id'],'content_plan');run=uid()
    with store.connect() as db:
        db.execute('INSERT INTO content_runs VALUES (?,?,?,?,?,?,?,?,?,?)',(run,project['id'],job,VERSION,encode(frozen),encode(outcome),digest(frozen),digest(outcome),None,now()))
    store.update_job(project['id'],job,status='succeeded')
    return store,project,run,outcome


def wait(client,base,run):
    for _ in range(200):
        result=client.get(base+'/'+run).json()
        if result['status'] not in {'queued','running'}:return result
        time.sleep(.05)
    pytest.fail('Planning did not finish')


def test_offline_replay_pagination_isolation(fixed,monkeypatch):
    store,project,run,outcome=fixed
    def no_network(*args,**kwargs):raise AssertionError('Replay must never fetch')
    monkeypatch.setattr('pagggle.content_workflow.fetch_firecrawl',no_network)
    before=store.snapshot(project['id'])
    with TestClient(create_app(load_settings(),store)) as client:
        base=f"/api/projects/{project['id']}/content-plans"
        assert client.post(base,json={'files':['../../config.json']}).status_code==409
        assert client.get('/api/projects/not-the-owner/content-plans/'+run).status_code==404
        assert client.get('/api/projects/not-the-owner/content-plans/'+run+'/export').status_code==404
        assert client.post('/api/projects/not-the-owner/content-plans/'+run+'/replay',json={}).status_code==404
        replay=client.post(base+'/'+run+'/replay',json={}).json()['id']
        actual=wait(client,base,replay)
        assert actual['status']=='succeeded',actual
        assert actual['result_hash']==digest(outcome)
        topic=outcome['topics'][0]
        first=client.get(base+'/'+replay+'/keywords',params={'topic':topic['id'],'page':1,'size':10}).json()
        second=client.get(base+'/'+replay+'/keywords',params={'topic':topic['id'],'page':2,'size':10}).json()
        assert first['total']==topic['keyword_count']
        assert not {r['id'] for r in first['rows']} & {r['id'] for r in second['rows']}
        exported=client.get(base+'/'+replay+'/export').json()
        assert len(exported['input']['keywords'])==56457
        assert exported['result']['accounting']['input']==90009
    after=store.snapshot(project['id'])
    for key in ['sources','profiles','seed_keyword_runs','keyword_onboarding']:assert before[key]==after[key]


def test_cancel_and_duplicate_active_run(fixed,monkeypatch):
    store,project,run,_=fixed
    entered,release=threading.Event(),threading.Event()
    real_build=build_result
    def hold(frozen):
        entered.set();release.wait(timeout=15)
        return real_build(frozen)
    monkeypatch.setattr('pagggle.content_workflow.build_result',hold)
    with TestClient(create_app(load_settings(),store)) as client:
        base=f"/api/projects/{project['id']}/content-plans"
        created=client.post(base+'/'+run+'/replay',json={}).json()
        try:
            assert entered.wait(10)
            assert client.post(base+'/'+run+'/replay',json={}).status_code==409
            assert client.post(f"/api/projects/{project['id']}/jobs/{created['job_id']}/cancel",json={}).status_code==200
        finally:release.set()
        actual=wait(client,base,created['id'])
        assert actual['status']=='cancelled' and actual['result_hash'] is None
        assert client.get(base+'/'+created['id']+'/export').status_code==409


def test_all_discovered_accounts_for_every_page_and_reuses_read_sources(fixed,monkeypatch):
    from pagggle.content_workflow import collect_evidence
    from pagggle.crawl import CrawlError
    store,project,_,_=fixed
    settings=load_settings()
    config=settings.content_workflows[project['id']].model_copy(update={'own_scope':'all_discovered','evidence_concurrency':4})
    snapshot=store.snapshot(project['id'])
    with store.connect() as db:
        cached={r['url']:json.loads(r['payload']) for r in db.execute('SELECT url,payload FROM content_assets WHERE project_id=?',(project['id'],))}
    calls=[]
    def unavailable(url,*args,**kwargs):
        calls.append(url)
        raise CrawlError('验证：模拟网络中断，不生成替代正文')
    monkeypatch.setattr('pagggle.content_workflow.fetch_firecrawl',unavailable)
    def failed_batch(wanted,*args):
        for url,owner in wanted:
            calls.append(url)
            yield {'url':url,'owner':owner,'status':'failed','body':'','title':url,'error':'验证：模拟网络中断'}
    monkeypatch.setattr('pagggle.content_workflow.batch_evidence',failed_batch)
    pages,competitors=collect_evidence(store,project['id'],config,settings,snapshot,lambda message:None)
    by_url={p['url']:p for p in pages}
    originals={p['url']:p for p in snapshot['sources'] if p['kind']=='page'}
    assert set(originals)<=set(by_url)
    for url,page in originals.items():
        if page['status']=='read':
            assert by_url[url]['body']==page['body'] and url not in calls
        elif page['status']=='excluded':
            assert by_url[url]['status']=='excluded' and url not in calls
        elif cached.get(url,{}).get('status')=='read':
            assert by_url[url]['body']==cached[url]['body'] and url not in calls
        else:
            assert url in calls and by_url[url]['status']=='failed'
    assert len(calls)==len(set(calls))
    assert len(competitors)==len(config.competitor_urls)
    assert store.snapshot(project['id'])['sources']==snapshot['sources']
