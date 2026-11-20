import json, sqlite3
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from app import main, store
from app.engine import evaluate
from app.modules.archive.rules import add_months, build_voucher, ensure_voucher, pseudonym, validate_publishable
from app.errors import BusinessError
from app.coerce import instant

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'test.sqlite'))
    return TestClient(main.app)

def tamper(tmp_path, run_id, mutate):
    path=str(tmp_path/'test.sqlite')
    with sqlite3.connect(path) as con:
        result=json.loads(con.execute('SELECT result FROM runs WHERE id=?',(run_id,)).fetchone()[0])
        mutate(result)
        con.execute('UPDATE runs SET result=? WHERE id=?',(json.dumps(result,ensure_ascii=False),run_id))

def voucher_of(client, run_id):
    response=client.get('/api/runs/'+run_id+'/voucher')
    assert response.status_code==200
    return response.json()

# 勾稽：恢复积分、过期损失、待核数量、发布状态互相对得上
def test_summary_reconciles_with_balance(sample):
    v=sample['archive']['voucher'];s=v['summary'];b=sample['balance']
    assert s['restored']+s['expired_refund']==s['refunded']==b['refunded']
    assert s['expired_loss']==s['expired_natural']+s['expired_refund']==b['expired']
    assert s['pending']==b['quarantined']==len(sample['quarantine'])
    assert s['restored']==sum(x['restored'] for x in sample['refund_audit'])
def test_all_checks_pass_and_ready(sample):
    v=sample['archive']['voucher']
    assert v['status']=='可发布'
    assert all(c['ok'] for c in v['checks'])
    assert {c['name'] for c in v['checks']}>={'恢复加过期等于退款','过期损失对得上','待核数量对得上','发布状态一致'}
def test_mixed_refund_line_split(sample):
    r1=next(x for x in sample['archive']['voucher']['refund_lines'] if x['id']=='R1')
    assert (r1['restored'],r1['expired_refund'],r1['points'])==(20,10,30)

# 复核链：追回原消费与当时规则，凭证自包含
def test_refund_traces_original_spend(sample):
    r1=next(x for x in sample['archive']['voucher']['refund_lines'] if x['id']=='R1')
    assert r1['ref']=='S1' and r1['original']['points']==120
    assert [(a['lot'],a['points']) for a in r1['original']['allocations']]==[('E1',100),('E2',20)]
    assert r1['original']['rule']=='常规' and r1['original']['version']==1
def test_rules_snapshot_embedded(sample):
    v=sample['archive']['voucher']
    assert v['rules']['常规']=={'start':'2026-01-01T00:00:00+08:00','end':'2027-01-01T00:00:00+08:00'}
    assert all(x['rule'] in v['rules'] for x in v['refund_lines'])
def test_chain_complete(sample):
    assert sample['archive']['voucher']['chain']=={'complete':True,'broken':[]}
def test_broken_chain_detected(sample):
    s=deepcopy(sample);s['point_ledger']['refunds'][0]['ref']='S-GONE'
    v=build_voucher(s)
    assert not v['chain']['complete'] and v['status']=='候选'
    with pytest.raises(BusinessError): validate_publishable(v)

# 原始捕获覆盖后不留断链
def test_rebuild_after_snapshot_overwritten(sample):
    original=sample['archive']['voucher']['content_sha']
    s=deepcopy(sample);s.pop('input_snapshot');s['archive']={}
    assert ensure_voucher(s)['content_sha']==original

# 保留期脱敏：超期会员用不可直接识别的依据，复核链不断
def test_retention_masks_beyond_period():
    v=evaluate({'dataset':'dirty','overrides':{'retention_months':0}})['archive']['voucher']
    members=[x['member'] for x in v['refund_lines']+v['expiry_lines']+v['pending_lines'] if x['member']]
    assert members and all(m.startswith('MBR-') for m in members)
    assert '00001' not in json.dumps(v,ensure_ascii=False)
    r1=next(x for x in v['refund_lines'] if x['id']=='R1')
    assert r1['member']==pseudonym(v['run_id'],'00001')
def test_retention_keeps_fresh_member(sample):
    v=sample['archive']['voucher']
    assert next(x for x in v['refund_lines'] if x['id']=='R1')['member']=='00001'
def test_unknown_member_masked_conservatively(sample):
    bad=next(x for x in sample['archive']['voucher']['pending_lines'] if x['id']=='BADCOL')
    assert bad['member'].startswith('MBR-')
def test_pseudonym_stable_per_run():
    a=evaluate({'dataset':'dirty','overrides':{'retention_months':0}})['archive']['voucher']
    b=evaluate({'dataset':'dirty','overrides':{'retention_months':0}})['archive']['voucher']
    assert a['refund_lines'][0]['member']==b['refund_lines'][0]['member']
    assert a['content_sha']==b['content_sha']
def test_add_months_clamps_day():
    assert add_months(instant('2026-01-31T00:00:00+00:00'),1).isoformat()=='2026-02-28T00:00:00+00:00'
    assert add_months(instant('2026-10-20T01:00:00+00:00'),24).isoformat()=='2028-10-20T01:00:00+00:00'

# 候选标记：勾稽破裂时只能标候选，不得可发布
def test_unbalanced_marks_candidate(sample):
    s=deepcopy(sample);s['balance']['points_balanced']=False
    v=build_voucher(s)
    assert v['status']=='候选'
    assert not next(c for c in v['checks'] if c['name']=='积分守恒')['ok']
    with pytest.raises(BusinessError) as e: validate_publishable(v)
    assert e.value.code=='ARCHIVE_NOT_READY'

# 发布：幂等、版本、失败保留旧版
def test_publish_flow_and_idempotency(client, tmp_path):
    run=client.post('/api/runs',json={}).json();rid=run['manifest']['run_id']
    assert voucher_of(client,rid)['publication']['status']=='未发布'
    assert client.post(f'/api/runs/{rid}/voucher/publish',json={'revision':99}).status_code==409
    published=client.post(f'/api/runs/{rid}/voucher/publish',json={'revision':1}).json()
    assert published['publication']['status']=='已发布' and published['publication']['version']==1
    again=client.post(f'/api/runs/{rid}/voucher/publish',json={'revision':1}).json()
    assert again['publication']['version']==1 and again['content_sha']==published['content_sha']
def test_publish_failure_keeps_old_version(client, tmp_path):
    run=client.post('/api/runs',json={}).json();rid=run['manifest']['run_id']
    sha=client.post(f'/api/runs/{rid}/voucher/publish',json={'revision':1}).json()['content_sha']
    tamper(tmp_path,rid,lambda r:r['archive']['voucher'].update(status='候选'))
    assert client.post(f'/api/runs/{rid}/voucher/publish',json={'revision':1}).status_code==422
    kept=voucher_of(client,rid)
    assert kept['publication']['status']=='已发布' and kept['content_sha']==sha
def test_tampered_voucher_rejected(client, tmp_path):
    run=client.post('/api/runs',json={}).json();rid=run['manifest']['run_id']
    tamper(tmp_path,rid,lambda r:r['archive']['voucher']['summary'].update(restored=1))
    assert client.post(f'/api/runs/{rid}/voucher/publish',json={'revision':1}).json()=={'code':'ARCHIVE_TAMPERED','detail':'凭证内容指纹不一致'}
def test_publish_bad_revision_type(client):
    run=client.post('/api/runs',json={}).json();rid=run['manifest']['run_id']
    assert client.post(f'/api/runs/{rid}/voucher/publish',json={'revision':0}).status_code==422

# 页面与下载入口一致；同批重跑内容一致
def test_page_and_download_consistent(client):
    rid=client.post('/api/runs',json={}).json()['manifest']['run_id']
    client.post(f'/api/runs/{rid}/voucher/publish',json={'revision':1})
    page=client.get('/archive?run_id='+rid)
    assert page.status_code==200
    v=voucher_of(client,rid)
    assert v['content_sha'][:16] in page.text and '已发布' in page.text
    assert v['voucher_id'] in page.text
def test_rerun_keeps_archive_identical(client):
    rid=client.post('/api/runs',json={}).json()['manifest']['run_id']
    client.post(f'/api/runs/{rid}/voucher/publish',json={'revision':1})
    before=voucher_of(client,rid)
    assert client.post(f'/api/runs/{rid}/rerun').json()['manifest']['run_id']==rid
    after=voucher_of(client,rid)
    assert after['content_sha']==before['content_sha'] and after['publication']['status']=='已发布'
    assert len(client.get('/api/runs').json())==1
def test_download_is_attachment(client):
    rid=client.post('/api/runs',json={}).json()['manifest']['run_id']
    response=client.get('/api/runs/'+rid+'/voucher')
    assert 'attachment' in response.headers['content-disposition']
    assert response.json()['run_id']==rid
