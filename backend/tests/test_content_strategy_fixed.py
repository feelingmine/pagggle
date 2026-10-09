"""Verify complete accounting against the user's unchanged Semrush exports."""
import base64
import json
import os
import sqlite3
from pathlib import Path

import pytest

from pagggle.config import ROOT
from pagggle.content_strategy import build_result, consolidate, digest, parse_seeds
from pagggle.intake import parse_records

pytestmark = pytest.mark.skipif(os.environ.get('PAGGGLE_VERIFY_FIXED_DATA')!='1', reason='Requires user-provided private keyword files and website snapshots')


@pytest.fixture(scope='module')
def frozen():
    directory = ROOT / 'backend/tests/data'
    files = sorted(directory.glob('*.xlsx'))
    assert len(files)==3, 'Expected the three user-provided files; no generated substitutes'
    records = {p.name:parse_records({'kind':'keyword','format':'xlsx','text':base64.b64encode(p.read_bytes()).decode(),'market':'us','language':'en'}) for p in files}
    rows, accounting, rejected = consolidate(records)
    with sqlite3.connect(f"file:{ROOT / 'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
        db.row_factory = sqlite3.Row
        project = db.execute('SELECT id FROM projects WHERE site_url=?',('https://siliconeproductsmanufacturer.com/',)).fetchone()
        pages = [dict(r) for r in db.execute("SELECT url,title,body,status FROM sources WHERE project_id=? AND kind='page'",(project['id'],))]
    return {'seeds':parse_seeds((directory/'base_keywords.txt').read_text()),'keywords':rows,'accounting':accounting,'rejected':rejected,'pages':pages,'competitors':[]}


def test_full_input_accounting(frozen):
    a=frozen['accounting']
    assert a=={'input':90009,'unique':56457,'duplicate_observations':33549,'failed':3}
    assert len(frozen['seeds'])==14
    assert sum(len(r['observations']) for r in frozen['keywords'])+len(frozen['rejected'])==a['input']
    assert all(r['volume'] is None for r in frozen['keywords'] if r['volume_conflict'])
    assert all(r['kd'] is None for r in frozen['keywords'] if r['kd_conflict'])


def test_replay_and_decision_evidence(frozen):
    result=build_result(frozen)
    assert digest(result)==digest(build_result(json.loads(json.dumps(frozen))))
    assigned=[i for t in result['topics'] for i in t['member_ids']]
    deferred=[r['id'] for r in result['deferred']]
    assert len(set(assigned+deferred))==len(assigned+deferred)==len(frozen['keywords'])
    assert len(result['topics']) < len(assigned)/10
    page_map={p['url']:p for p in frozen['pages']}
    for topic in result['topics']:
        if topic['action']=='new':
            assert not topic['candidates']
            assert topic['brief']['outline']
        if topic['action'] in {'optimize','keep'}:
            assert page_map[topic['target_url']]['status']=='read'
        if topic['action']=='optimize':
            assert topic['brief']['optimization']
        for req in topic['requirements']:
            if req['evidence']:
                assert req['evidence']['quote'] in page_map[req['evidence']['url']]['body']
    assert any(t['action']=='review' and any(p['status']=='uncovered' for p in t['candidates']) for t in result['topics'])
    assert all(e['volume'] is None and e['kd'] is None for e in result['expansions'] if not e['observed'])


def test_real_competitor_widget_is_not_document_challenge():
    from bs4 import BeautifulSoup
    from pagggle.crawl import parse_html
    path=ROOT/'data/verification/content-workflow/newtop-real.html'
    assert path.exists(), 'Capture the authorized real competitor HTML first'
    raw=path.read_bytes()
    soup=BeautifulSoup(raw,'html.parser')
    assert any('recaptcha' in t.get_text().lower() for t in soup.find_all('title'))
    title,body,_=parse_html(raw,'https://www.newtopsilicone.com/product-cases/mom-baby-supplies/')
    assert 'NEWTOP' in title and 'Silicone Teether' in body


def test_url_task_precedes_marketing_title(frozen):
    from pagggle.content_strategy import page_object
    page=next(p for p in frozen['pages'] if p['url'].endswith('/custom-baby-silicone-feeding-set-manufacturer/'))
    assert page_object(page)==('feeding_set','sourcing')
    for topic in build_result(frozen)['topics']:
        if topic['object']=='manufacturing' and topic['target_url']:
            assert '/kitchen-silicone-products/' not in topic['target_url']


def test_real_assets_and_tag_indexes_are_not_topic_targets():
    from pagggle.content_strategy import page_role
    with sqlite3.connect(f"file:{ROOT / 'data/pagggle.sqlite3'}?mode=ro",uri=True) as db:
        row=db.execute("SELECT input_json FROM content_runs WHERE project_id=? AND id=?",('3569ed46f2554178b327c2e7c58c8b3b','4eccef4cd190444d81bdd632d987a337')).fetchone()
    assert row, 'Requires the actual first full-site run'
    snapshot=json.loads(row[0]);pages={p['url']:p for p in snapshot['pages']}
    assert any(page_role(p)=='asset' and p['status']=='read' for p in pages.values())
    assert any(page_role(p)=='index' and p['status']=='read' for p in pages.values())
    result=build_result(snapshot)
    for t in result['topics']:
        if t['target_url']:assert page_role(pages[t['target_url']])=='content'
        assert all(page_role(pages[p['url']])=='content' for p in t['candidates'])
    assert result['coverage']['own_discovered']+result['coverage']['asset_total']==len(pages)
