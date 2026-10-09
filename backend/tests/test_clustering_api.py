import time
import threading

from fastapi.testclient import TestClient
import pytest

from pagggle.app import create_app
from pagggle.clustering import cluster_keywords
from pagggle.config import Settings
from pagggle.intake import import_records
from pagggle.store import Store


def test_cluster_http_versions_isolation_and_failure_preserves_previous(tmp_path, monkeypatch):
    store = Store(tmp_path / 'test.sqlite3')
    settings = Settings(BASE_URL='https://provider.example/v1', model='test', api_key='secret-test')
    a = store.create_project('A', 'https://a.example')['id']
    b = store.create_project('B', 'https://b.example')['id']
    payload = dict(kind='keyword', format='lines', text='gasket\nseal\ngasket', request_id='cluster-input')
    imported = import_records(store, a, payload, commit=True)
    foreign = import_records(store, b, payload, commit=True)['records'][0]['id']
    ids = [r['id'] for r in imported['records'] if r['status'] == 'pending']
    monkeypatch.setattr('pagggle.app.cluster_keywords', lambda s, rows, **kw: cluster_keywords(s, rows, encoder=lambda texts: [[1,0]]*len(texts), **kw))
    def wait(client, project):
        for _ in range(200):
            job = client.get(f'/api/projects/{project}').json()['jobs'][0]
            if job['status'] not in ('queued','running'): return job
            time.sleep(.01)
        pytest.fail('job did not finish')
    with TestClient(create_app(settings, store)) as client:
        route = f'/api/projects/{a}'
        assert client.post(route+'/jobs', json={'kind':'clusters','demand_ids':[]}).status_code == 409
        assert client.post(route+'/jobs', json={'kind':'clusters','demand_ids':[foreign]}).status_code == 404
        assert client.post(route+'/jobs', json={'kind':'clusters','demand_ids':[imported['records'][2]['id']]}).status_code == 404
        assert client.post(route+'/jobs', json={'kind':'clusters'}).status_code == 202
        assert wait(client,a)['status'] == 'succeeded'
        first = client.get(route+'/clusters').json()['runs'][0]
        assert first['version'] == 1
        assert sorted(m['demand_id'] for g in first['payload']['groups'] for m in g['members']) == sorted(ids)
        assert store.profiles(a) == []
        assert client.get(f'/api/projects/{b}/clusters').json()['runs'] == []
        assert client.get('/api/projects/missing/clusters').status_code == 404
        assert client.post(route+'/jobs', json={'kind':'clusters','demand_ids':[ids[0]]}).status_code == 202
        assert wait(client,a)['status'] == 'succeeded'
        assert [r['version'] for r in client.get(route+'/clusters').json()['runs']] == [2,1]
        def broken(*args,**kwargs): raise ValueError('synthetic encoder failure')
        monkeypatch.setattr('pagggle.app.cluster_keywords', broken)
        client.post(route+'/jobs', json={'kind':'clusters'})
        assert wait(client,a)['status'] == 'failed'
        runs = client.get(route+'/clusters').json()['runs']
        assert len(runs) == 2 and runs[1] == first
        assert store.snapshot(a)['model_runs'] == []


def test_cluster_cancel_and_exclusive_job_prevent_result_write(tmp_path, monkeypatch):
    store = Store(tmp_path / 'test.sqlite3')
    settings = Settings(BASE_URL='https://provider.example/v1', model='test', api_key='test')
    a = store.create_project('A', 'https://a.example')['id']
    import_records(store,a,dict(kind='keyword',format='lines',text='gasket',request_id='cluster-input'),commit=True)
    entered, release = threading.Event(), threading.Event()
    def deferred(s, rows, **kw):
        entered.set(); assert release.wait(5)
        return cluster_keywords(s, rows, encoder=lambda _: [[1,0]])
    monkeypatch.setattr('pagggle.app.cluster_keywords',deferred)
    with TestClient(create_app(settings,store)) as client:
        route=f'/api/projects/{a}'
        job=client.post(route+'/jobs',json={'kind':'clusters'}).json()['id']
        try:
            assert entered.wait(5)
            assert client.post(route+'/jobs',json={'kind':'clusters'}).status_code==409
            assert client.post(route+f'/jobs/{job}/cancel',json={}).status_code==200
        finally: release.set()
    assert store.cluster_runs(a)==[]
    assert store.jobs(a)[0]['status']=='cancelled'


def test_store_rejects_omitted_or_foreign_cluster_members(tmp_path):
    store=Store(tmp_path/'test.sqlite3')
    a=store.create_project('A','https://a.example')['id']
    b=store.create_project('B','https://b.example')['id']
    data=dict(kind='keyword',format='lines',text='gasket',request_id='cluster-input')
    own=import_records(store,a,data,commit=True)['records'][0]['id']
    other=import_records(store,b,data,commit=True)['records'][0]['id']
    job=store.create_job(a,'clusters',{'demand_ids':[own]});store.update_job(a,job,status='running')
    for members in ([],[{'demand_id':other}],[{'demand_id':own},{'demand_id':own}]):
        with pytest.raises(ValueError):store.save_clusters(a,job,{'groups':[{'members':members}]})
    assert store.cluster_runs(a)==[]
