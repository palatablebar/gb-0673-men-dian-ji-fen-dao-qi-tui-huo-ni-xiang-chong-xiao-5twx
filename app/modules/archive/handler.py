from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse
from app.engine import evaluate
from app.errors import BusinessError
from app.web import render
from app import store
router=APIRouter()
@router.get('/archive')
def page(request:Request):
    return render(request,'archive')
@router.post('/api/archive')
def calculate(payload:dict=Body(default={})):
    return evaluate(payload,until=7)
@router.get('/api/runs/{run_id}/voucher')
def voucher_download(run_id:str):
    from app.main import DB_PATH
    voucher=store.effective_voucher(DB_PATH,run_id)
    return JSONResponse(voucher,headers={'Content-Disposition':f'attachment; filename="voucher-{run_id}.json"'})
@router.post('/api/runs/{run_id}/voucher/publish')
def voucher_publish(run_id:str,payload:dict=Body(...)):
    from app.main import DB_PATH
    revision=payload.get('revision')
    if not isinstance(revision,int) or revision<1: raise BusinessError('BAD_REVISION','版本号须为正整数')
    return store.publish_voucher(DB_PATH,run_id,revision)
