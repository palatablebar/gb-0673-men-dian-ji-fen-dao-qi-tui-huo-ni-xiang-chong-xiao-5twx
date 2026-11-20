import hashlib, json, calendar
from app.coerce import instant
from app.config import RULE_VERSION
from app.errors import BusinessError

RETENTION_DEFAULT_MONTHS = 24

def decide(state):
    balanced=state['balance']['row_balanced'] and state['balance']['points_balanced'] and not state['refund_issues']
    balances={}
    for lot in state['point_ledger']['lots']:balances[lot['member']]=balances.get(lot['member'],0)+lot['remaining']
    rows=[{'member':m,'remaining':n} for m,n in sorted(balances.items())]
    return {'status':'可归档' if balanced else '待复核','members':len(rows),'seal':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),'balances':rows}

def add_months(dt, months):
    m=dt.month-1+months
    y=dt.year+m//12
    m=m%12+1
    return dt.replace(year=y,month=m,day=min(dt.day,calendar.monthrange(y,m)[1]))

def pseudonym(run_id, member):
    return 'MBR-'+hashlib.sha256(f'{run_id}:{member}'.encode()).hexdigest()[:12].upper()

def _canonical(voucher):
    return json.dumps(voucher,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def content_hash(voucher):
    return hashlib.sha256(_canonical({k:v for k,v in voucher.items() if k!='content_sha'}).encode()).hexdigest()

def build_voucher(state):
    """生成到期与退货冲销归档凭证。

    自包含：原消费分配、批次到期时刻与当时规则窗口全部内嵌，原始捕获被覆盖后复核链不断。
    脱敏：最近活动距今超过保留期的会员以确定性伪名替代，同批重跑伪名一致。
    确定性：内容不含墙钟时间，content_sha 只随输入与规则版本变化。
    """
    ctx=state['context'];as_of=instant(ctx['as_of'])
    retention=ctx.get('retention_months',RETENTION_DEFAULT_MONTHS)
    run_id=state.get('run_id') or state['manifest']['run_id']
    last={}
    for r in state['records']:
        at=instant(r['at'])
        if r['member'] not in last or at>last[r['member']]:last[r['member']]=at
    masked=set()
    def name(member):
        if not member:return member
        deadline=add_months(last[member],retention) if member in last else None
        if deadline is None or as_of>=deadline:
            masked.add(member);return pseudonym(run_id,member)
        return member
    spends={s['id']:s for s in state['point_ledger']['spends']}
    recs={r['id']:r for r in state['records']}
    rules={k:{'start':v['start'],'end':v['end']} for k,v in ctx['rules'].items()}
    broken=[]
    refund_lines=[]
    for rf in state['point_ledger']['refunds']:
        rec=recs.get(rf['id']);orig=spends.get(rf['ref']);original=None
        if orig is None:
            broken.append(rf['id'])
        else:
            orec=recs.get(rf['ref'])
            original={'id':orig['id'],'points':orig['points'],'at':orig['at'],
                      'rule':orec['rule'] if orec else None,'version':orec['version'] if orec else None,
                      'allocations':[{'lot':a['lot'],'points':a['points'],'expires':a['expires']} for a in orig['allocations']]}
            if {a['lot'] for a in rf['allocations']}-{a['lot'] for a in original['allocations']}:broken.append(rf['id'])
        if rec is None or rec['rule'] not in rules:broken.append(rf['id'])
        refund_lines.append({'id':rf['id'],'member':name(rf['member']),'ref':rf['ref'],'points':rf['points'],
            'restored':rf['restored'],'expired_refund':rf['expired_refund'],
            'at':rec['at'] if rec else None,'rule':rec['rule'] if rec else None,'version':rec['version'] if rec else None,
            'original':original,'allocations':[{'lot':a['lot'],'points':a['points'],'expired':a['expired']} for a in rf['allocations']],
            'line':rf['line']})
    expiry_lines=[{'lot':e['id'],'member':name(e['member']),'points':e['points'],'at':e['at']}
                  for e in state['point_ledger']['ledger'] if e['action']=='EXPIRE']
    pending_lines=[{'line':q['line'],'id':q['id'],'member':name(q['raw'].get('member','')),'code':q['code'],'detail':q['detail']}
                   for q in state['quarantine']]
    restored=sum(l['restored'] for l in refund_lines)
    expired_refund=sum(l['expired_refund'] for l in refund_lines)
    expired_natural=sum(l['points'] for l in expiry_lines)
    refunded=sum(l['points'] for l in refund_lines)
    b=state['balance']
    checks=[
        {'name':'恢复加过期等于退款','ok':restored+expired_refund==refunded,'detail':f'{restored}+{expired_refund}={refunded}'},
        {'name':'退款总额对得上','ok':refunded==b['refunded'],'detail':f"凭证{refunded} 对账{b['refunded']}"},
        {'name':'过期损失对得上','ok':expired_natural+expired_refund==b['expired'],'detail':f"自然{expired_natural}+退货{expired_refund}={expired_natural+expired_refund} 对账{b['expired']}"},
        {'name':'待核数量对得上','ok':len(pending_lines)==b['quarantined'],'detail':f"凭证{len(pending_lines)} 对账{b['quarantined']}"},
        {'name':'行守恒','ok':bool(b['row_balanced']),'detail':f"采纳{b['accepted']} 隔离{b['quarantined']}"},
        {'name':'积分守恒','ok':bool(b['points_balanced']),'detail':f"发放{b['issued']} 可用{b['remaining']}"},
        {'name':'回拨无异常','ok':not state['refund_issues'],'detail':f"异常{len(state['refund_issues'])}笔"},
        {'name':'复核链完整','ok':not broken,'detail':f"断链{len(broken)}处"},
    ]
    ready=all(c['ok'] for c in checks)
    status='可发布' if ready else '候选'
    checks.append({'name':'发布状态一致','ok':ready==(status=='可发布'),'detail':status})
    voucher={'voucher_id':'VCH-'+run_id,'run_id':run_id,'rule_version':RULE_VERSION,'as_of':ctx['as_of'],
        'retention_months':retention,'source':state.get('source',''),'status':status,
        'summary':{'restored':restored,'expired_refund':expired_refund,'expired_natural':expired_natural,
                   'expired_loss':expired_natural+expired_refund,'refunded':refunded,'pending':len(pending_lines),
                   'refund_lines':len(refund_lines),'expiry_lines':len(expiry_lines),'masked_members':len(masked)},
        'checks':checks,'refund_lines':refund_lines,'expiry_lines':expiry_lines,'pending_lines':pending_lines,
        'rules':rules,'chain':{'complete':not broken,'broken':sorted(set(broken))}}
    voucher['content_sha']=content_hash(voucher)
    return voucher

def ensure_voucher(state):
    """旧批或原始捕获缺失时按已存结果重建同一凭证，保证不留断链。"""
    voucher=(state.get('archive') or {}).get('voucher')
    return voucher if voucher else build_voucher(state)

def validate_publishable(voucher):
    if voucher['status']!='可发布':raise BusinessError('ARCHIVE_NOT_READY','凭证为候选状态，待核完成后才能发布')
    if not voucher['chain']['complete']:raise BusinessError('ARCHIVE_CHAIN_BROKEN','复核链存在断链，禁止发布')
    if not all(c['ok'] for c in voucher['checks']):raise BusinessError('ARCHIVE_UNBALANCED','凭证勾稽不一致，禁止发布')
    if voucher['content_sha']!=content_hash(voucher):raise BusinessError('ARCHIVE_TAMPERED','凭证内容指纹不一致')

def apply(state):
    state['archive']=decide(state);state['archive']['voucher']=build_voucher(state)
    summary=state['archive']['voucher']['summary']
    state['views']['archive']={'title':'期末归档','rows':state['archive']['balances'],
        'metrics':{'会员数':state['archive']['members'],'可用积分':state['balance']['remaining'],'状态':state['archive']['voucher']['status'],
                   '恢复积分':summary['restored'],'过期损失':summary['expired_loss'],'待核':summary['pending']}}
