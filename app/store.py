import sqlite3, json
from datetime import datetime, timezone
from pathlib import Path
from app.errors import BusinessError

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    con.execute("CREATE TABLE IF NOT EXISTS vouchers (run_id TEXT PRIMARY KEY, content_sha TEXT NOT NULL, content TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, published_at TEXT NOT NULL)")
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    return result

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)

def get_published(path, run_id):
    with connect(path) as con:
        row=con.execute('SELECT run_id,content_sha,content,version,published_at FROM vouchers WHERE run_id=?',(run_id,)).fetchone()
    if not row: return None
    record=dict(row);record['content']=json.loads(record['content'])
    return record

def effective_voucher(path, run_id):
    """页面与下载共用的同一份凭证：已发布优先，否则给出当前候选。"""
    from app.modules.archive.rules import ensure_voucher
    result=get(path,run_id)
    published=get_published(path,run_id)
    if published:
        return {**published['content'],'content_sha':published['content_sha'],
                'publication':{'status':'已发布','version':published['version'],'published_at':published['published_at']}}
    voucher=ensure_voucher(result)
    return {**voucher,'publication':{'status':'未发布','version':0,'published_at':None}}

def publish_voucher(path, run_id, revision):
    """事务化发布：校验失败抛错即回滚，旧版凭证原样保留；同内容重复发布幂等。"""
    from app.modules.archive.rules import ensure_voucher, validate_publishable
    result=get(path,run_id)
    if result['revision']!=revision:
        raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    voucher=ensure_voucher(result)
    validate_publishable(voucher)
    sha=voucher['content_sha'];now=datetime.now(timezone.utc).isoformat()
    with connect(path) as con:
        row=con.execute('SELECT content_sha FROM vouchers WHERE run_id=?',(run_id,)).fetchone()
        if row and row['content_sha']==sha:
            pass
        elif row:
            con.execute('UPDATE vouchers SET content_sha=?,content=?,version=version+1,published_at=? WHERE run_id=?',
                        (sha,json.dumps(voucher,ensure_ascii=False),now,run_id))
        else:
            con.execute('INSERT INTO vouchers(run_id,content_sha,content,version,published_at) VALUES(?,?,?,1,?)',
                        (run_id,sha,json.dumps(voucher,ensure_ascii=False),now))
    return effective_voucher(path,run_id)
