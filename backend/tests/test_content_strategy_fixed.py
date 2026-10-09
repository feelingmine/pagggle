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
