import logging,uuid
from fastapi import FastAPI,Form,HTTPException,Request,status
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from redis import Redis
from sqlalchemy import func,select,text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware
from app.config import settings
from app.db import SessionLocal,engine
from app.models import User,Customer,Site,Device,AuditEvent
from app.security import verify_password,csrf_token,validate_csrf
logging.basicConfig(level=logging.INFO); log=logging.getLogger('api')
app=FastAPI(title=settings.app_name,version='0.1.0',docs_url='/api/docs',redoc_url=None)
app.add_middleware(SessionMiddleware,secret_key=settings.app_secret_key,session_cookie=settings.session_cookie_name,max_age=settings.session_max_age_seconds,same_site='lax',https_only=settings.session_cookie_secure)
app.mount('/static',StaticFiles(directory='app/static'),name='static')
templates=Jinja2Templates(directory='app/templates'); templates.env.globals['app_name']=settings.app_name; templates.env.globals['csrf_token']=csrf_token

def current_user(request,db):
    raw=request.session.get('user_id')
    if not raw: return None
    try: u=db.get(User,uuid.UUID(raw))
    except Exception: return None
    return u if u and u.is_active else None

def require_admin(request,db):
    u=current_user(request,db)
    if not u or u.role!='admin': raise HTTPException(status_code=403)
    return u

def add_event(db,event_type,actor=None,customer_id=None,device_id=None,details=None,severity='info',result='success'):
    db.add(AuditEvent(event_type=event_type,actor_user_id=actor.id if actor else None,customer_id=customer_id,device_id=device_id,details=details or {},severity=severity,result=result))

def norm_mac(v):
    import re
    if not v.strip(): return None
    x=re.sub(r'[^0-9A-Fa-f]','',v)
    if len(x)!=12: raise ValueError('Il MAC deve contenere 12 cifre esadecimali.')
    x=x.upper(); return ':'.join(x[i:i+2] for i in range(0,12,2))

def opt(v):
    v=v.strip(); return v or None

def login_redirect(): return RedirectResponse('/login',status_code=303)

@app.get('/health')
def health():
    dbok=rok=False
    try:
        with engine.connect() as c: c.execute(text('SELECT 1'))
        dbok=True
    except Exception: log.exception('DB health failed')
    try: rok=bool(Redis.from_url(settings.redis_url,socket_timeout=2).ping())
    except Exception: log.exception('Redis health failed')
    code=200 if dbok and rok else 503
    return JSONResponse({'status':'ok' if code==200 else 'degraded','database':dbok,'redis':rok},status_code=code)

@app.get('/api/v1/health')
def api_health(): return health()

@app.get('/login',response_class=HTMLResponse)
def login_page(request:Request):
    with SessionLocal() as db:
        if current_user(request,db): return RedirectResponse('/',status_code=303)
    return templates.TemplateResponse(request=request,name='login.html',context={'error':None,'user':None})

@app.post('/login')
def do_login(request:Request,username:str=Form(...),password:str=Form(...),csrf:str=Form(...)):
    validate_csrf(request,csrf)
    with SessionLocal() as db:
        u=db.scalar(select(User).where(User.username==username.strip()))
        if not u or not u.is_active or not verify_password(password,u.password_hash):
            return templates.TemplateResponse(request=request,name='login.html',context={'error':'Credenziali non valide.','user':None},status_code=401)
        request.session.clear(); request.session['user_id']=str(u.id); request.session['csrf_token']=csrf_token(request)
        add_event(db,'USER_LOGIN',actor=u,details={'username':u.username}); db.commit()
    return RedirectResponse('/',status_code=303)

@app.post('/logout')
def logout(request:Request,csrf:str=Form(...)):
    validate_csrf(request,csrf)
    request.session.clear(); return RedirectResponse('/login',status_code=303)

@app.get('/',response_class=HTMLResponse)
def dashboard(request:Request):
    with SessionLocal() as db:
        u=current_user(request,db)
        if not u: return login_redirect()
        ctx={'user':u,'customer_count':db.scalar(select(func.count(Customer.id))) or 0,'site_count':db.scalar(select(func.count(Site.id))) or 0,'device_count':db.scalar(select(func.count(Device.id))) or 0,'recent_events':list(db.scalars(select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(15)))}
        return templates.TemplateResponse(request=request,name='dashboard.html',context=ctx)

@app.get('/customers',response_class=HTMLResponse)
def customers(request:Request):
    with SessionLocal() as db:
        u=current_user(request,db)
        if not u: return login_redirect()
        rows=list(db.scalars(select(Customer).options(selectinload(Customer.devices),selectinload(Customer.sites)).order_by(Customer.name)))
        return templates.TemplateResponse(request=request,name='customers.html',context={'user':u,'customers':rows})

@app.post('/customers')
def add_customer(request:Request,name:str=Form(...),code:str=Form(''),notes:str=Form(''),csrf:str=Form(...)):
    validate_csrf(request,csrf)
    with SessionLocal() as db:
        u=require_admin(request,db); c=Customer(name=name.strip(),code=opt(code),notes=opt(notes)); db.add(c)
        try: db.flush(); add_event(db,'CUSTOMER_ADDED',actor=u,customer_id=c.id,details={'name':c.name,'code':c.code}); db.commit()
        except IntegrityError: db.rollback(); raise HTTPException(409,'Codice cliente già utilizzato.')
    return RedirectResponse('/customers',status_code=303)

@app.get('/customers/{customer_id}',response_class=HTMLResponse)
def customer_detail(request:Request,customer_id:uuid.UUID):
    with SessionLocal() as db:
        u=current_user(request,db)
        if not u: return login_redirect()
        c=db.scalar(select(Customer).where(Customer.id==customer_id).options(selectinload(Customer.sites),selectinload(Customer.devices).selectinload(Device.site)))
        if not c: raise HTTPException(404)
        ev=list(db.scalars(select(AuditEvent).where(AuditEvent.customer_id==customer_id).order_by(AuditEvent.timestamp.desc()).limit(30)))
        return templates.TemplateResponse(request=request,name='customer_detail.html',context={'user':u,'customer':c,'events':ev})

@app.post('/customers/{customer_id}/sites')
def add_site(request:Request,customer_id:uuid.UUID,name:str=Form(...),address:str=Form(''),notes:str=Form(''),csrf:str=Form(...)):
    validate_csrf(request,csrf)
    with SessionLocal() as db:
        u=require_admin(request,db)
        if not db.get(Customer,customer_id): raise HTTPException(404)
        s=Site(customer_id=customer_id,name=name.strip(),address=opt(address),notes=opt(notes)); db.add(s); db.flush(); add_event(db,'SITE_ADDED',actor=u,customer_id=customer_id,details={'site_id':str(s.id),'name':s.name}); db.commit()
    return RedirectResponse(f'/customers/{customer_id}',status_code=303)

@app.post('/customers/{customer_id}/devices')
def add_device(request:Request,customer_id:uuid.UUID,name:str=Form(...),vendor:str=Form(...),device_type:str=Form(...),family:str=Form(''),model:str=Form(''),serial_number:str=Form(''),primary_mac:str=Form(''),management_ip:str=Form(''),firmware_version:str=Form(''),management_source:str=Form('manual'),site_id:str=Form(''),csrf:str=Form(...)):
    validate_csrf(request,csrf)
    with SessionLocal() as db:
        u=require_admin(request,db)
        if not db.get(Customer,customer_id): raise HTTPException(404)
        sid=None
        if site_id.strip():
            sid=uuid.UUID(site_id); s=db.get(Site,sid)
            if not s or s.customer_id!=customer_id: raise HTTPException(400,'Sede non valida.')
        try: mac=norm_mac(primary_mac)
        except ValueError as e: raise HTTPException(400,str(e))
        d=Device(customer_id=customer_id,site_id=sid,name=name.strip(),vendor=vendor.strip().lower(),device_type=device_type.strip().lower(),family=opt(family),model=opt(model),serial_number=opt(serial_number),primary_mac=mac,management_ip=opt(management_ip),firmware_version=opt(firmware_version),management_source=management_source.strip().lower(),status='unknown'); db.add(d)
        try: db.flush(); add_event(db,'DEVICE_ADDED',actor=u,customer_id=customer_id,device_id=d.id,details={'name':d.name,'vendor':d.vendor,'model':d.model,'primary_mac':d.primary_mac,'management_source':d.management_source}); db.commit()
        except IntegrityError: db.rollback(); raise HTTPException(409,'Esiste già un apparato dello stesso vendor con questo MAC.')
    return RedirectResponse(f'/customers/{customer_id}',status_code=303)
