"""
太平洋产险 · 外勤助手 - 云端后端服务器

部署方式：
  本地运行: python main.py
  部署到 Render: 见 render.yaml

功能：
  - 员工注册/登录
  - 拜访记录、意向客户、客户档案云端同步
  - 管理者看板数据汇总
  - PWA 支持（可添加到手机桌面）
"""

import os
import time
import hashlib
from contextlib import asynccontextmanager

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ============================================================
# SETUP
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_FILE = os.path.join(DATA_DIR, "field_work.db").replace("\\", "/")
DB_URL = os.environ.get("DATABASE_URL", f"sqlite+aiosqlite:///{DB_FILE}")
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql+asyncpg://", 1)

PORT = int(os.environ.get("PORT", 8000))

# ============================================================
# DATABASE
# ============================================================

engine = create_async_engine(DB_URL, echo=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = sa.Column(sa.Integer, primary_key=True)
    name = sa.Column(sa.String(100), unique=True, nullable=False, index=True)
    pwd_hash = sa.Column(sa.String(64), nullable=False)
    created_at = sa.Column(sa.Float, default=time.time)


class VisitRecord(Base):
    __tablename__ = "visits"
    id = sa.Column(sa.Integer, primary_key=True)
    client_id = sa.Column(sa.String(50), nullable=False, index=True)
    user_name = sa.Column(sa.String(100), nullable=False, index=True)
    customer = sa.Column(sa.String(200), nullable=False)
    category = sa.Column(sa.String(50))
    bank = sa.Column(sa.String(50), default="")
    bank_sub = sa.Column(sa.String(100), default="")
    visit_date = sa.Column(sa.String(20))
    interest = sa.Column(sa.String(10))
    content = sa.Column(sa.Text, default="")
    support = sa.Column(sa.Text, default="")
    next_visit = sa.Column(sa.String(20))
    status = sa.Column(sa.String(20), default="已拜访")
    created_at = sa.Column(sa.Float, default=time.time)


class LeadRecord(Base):
    __tablename__ = "leads"
    id = sa.Column(sa.Integer, primary_key=True)
    client_id = sa.Column(sa.String(50), nullable=False, index=True)
    user_name = sa.Column(sa.String(100), nullable=False, index=True)
    name = sa.Column(sa.String(200), nullable=False)
    contact = sa.Column(sa.String(100), default="")
    phone = sa.Column(sa.String(50), default="")
    channel = sa.Column(sa.String(50), default="")
    referrer = sa.Column(sa.String(100), default="")
    industry = sa.Column(sa.String(50), default="")
    product = sa.Column(sa.String(200), default="")
    amount = sa.Column(sa.Float, default=0)
    close_date = sa.Column(sa.String(20))
    stage = sa.Column(sa.String(50), default="初步接触")
    created_at = sa.Column(sa.Float, default=time.time)


class CustomerRecord(Base):
    __tablename__ = "customers"
    id = sa.Column(sa.Integer, primary_key=True)
    client_id = sa.Column(sa.String(50), nullable=False, index=True)
    user_name = sa.Column(sa.String(100), nullable=False, index=True)
    name = sa.Column(sa.String(200), nullable=False)
    contact = sa.Column(sa.String(100), default="")
    phone = sa.Column(sa.String(50), default="")
    level = sa.Column(sa.String(20), default="C级(普通)")
    industry = sa.Column(sa.String(50), default="")
    size = sa.Column(sa.String(50), default="")
    premium = sa.Column(sa.Float, default=0)
    decision = sa.Column(sa.String(200), default="")
    address = sa.Column(sa.String(300), default="")
    created_at = sa.Column(sa.Float, default=time.time)


AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ============================================================
# PWA STATIC CONTENT
# ============================================================

MANIFEST_JSON = {
    "name": "太平洋产险·外勤助手",
    "short_name": "外勤助手",
    "start_url": "/mobile",
    "display": "standalone",
    "background_color": "#2563eb",
    "theme_color": "#2563eb",
    "orientation": "portrait",
    "icons": [
        {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ]
}

SW_JS = """self.addEventListener('install', e => { self.skipWaiting(); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.map(k => caches.delete(k))).then(() => clients.claim())));
});
self.addEventListener('fetch', e => {
  if (e.request.url.includes('/api/') || e.request.mode === 'navigate') return;
  e.respondWith(
    caches.open('v1').then(c =>
      c.match(e.request).then(r => r || fetch(e.request).then(resp => {
        if (resp.ok) c.put(e.request, resp.clone());
        return resp;
      }))
    )
  );
});"""


# ============================================================
# SCHEMAS
# ============================================================


class LoginRequest(BaseModel):
    name: str
    password: str


class SyncRequest(BaseModel):
    visits: list[dict] = []
    leads: list[dict] = []
    customers: list[dict] = []


# ============================================================
# HELPERS
# ============================================================


def pwd_hash(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()[:16]


def to_visit_dict(v: VisitRecord) -> dict:
    return {
        "id": v.client_id, "userName": v.user_name,
        "customer": v.customer, "category": v.category or "",
        "bank": v.bank or "", "bankSub": v.bank_sub or "",
        "date": v.visit_date or "", "interest": v.interest or "",
        "content": v.content or "", "support": v.support or "",
        "nextVisit": v.next_visit or "", "status": v.status or "已拜访",
        "createdAt": v.created_at or 0,
    }


def to_lead_dict(l: LeadRecord) -> dict:
    return {
        "id": l.client_id, "userName": l.user_name,
        "name": l.name, "contact": l.contact or "",
        "phone": l.phone or "", "channel": l.channel or "",
        "referrer": l.referrer or "", "industry": l.industry or "",
        "product": l.product or "", "amount": l.amount or 0,
        "closeDate": l.close_date or "", "stage": l.stage or "初步接触",
        "createdAt": l.created_at or 0,
    }


def to_customer_dict(c: CustomerRecord) -> dict:
    return {
        "id": c.client_id, "userName": c.user_name,
        "name": c.name, "contact": c.contact or "",
        "phone": c.phone or "", "level": c.level or "",
        "industry": c.industry or "", "size": c.size or "",
        "premium": c.premium or 0, "decision": c.decision or "",
        "address": c.address or "", "createdAt": c.created_at or 0,
    }


# ============================================================
# APP
# ============================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="外勤助手 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# API ROUTES
# ============================================================


@app.get("/api/health")
async def health():
    return {"status": "ok", "time": time.time()}


@app.post("/api/register")
async def register(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(sa.select(User).where(User.name == req.name))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "用户已存在")
    user = User(name=req.name, pwd_hash=pwd_hash(req.password))
    db.add(user)
    await db.commit()
    return {"ok": True, "msg": "注册成功"}


@app.post("/api/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(sa.select(User).where(User.name == req.name))
    user = result.scalar_one_or_none()
    if not user or user.pwd_hash != pwd_hash(req.password):
        raise HTTPException(401, "用户名或密码错误")
    return {"ok": True, "name": user.name}


@app.post("/api/sync")
async def sync_data(req: SyncRequest, request: Request, db: AsyncSession = Depends(get_db)):
    user_name = request.headers.get("X-User-Name", "")
    if not user_name:
        raise HTTPException(400, "缺少 X-User-Name 请求头")

    existing_visits = await db.execute(
        sa.select(VisitRecord.client_id).where(VisitRecord.user_name == user_name)
    )
    existing_visit_ids = set(row[0] for row in existing_visits)

    existing_leads = await db.execute(
        sa.select(LeadRecord.client_id).where(LeadRecord.user_name == user_name)
    )
    existing_lead_ids = set(row[0] for row in existing_leads)

    existing_customers = await db.execute(
        sa.select(CustomerRecord.client_id).where(CustomerRecord.user_name == user_name)
    )
    existing_customer_ids = set(row[0] for row in existing_customers)

    new_visits = 0
    for v in req.visits:
        cid = v.get("id", "")
        if cid and cid not in existing_visit_ids:
            db.add(VisitRecord(
                client_id=cid, user_name=user_name,
                customer=v.get("customer", ""), category=v.get("category", ""),
                bank=v.get("bank", ""), bank_sub=v.get("bankSub", ""),
                visit_date=v.get("date", ""), interest=v.get("interest", ""),
                content=v.get("content", ""), support=v.get("support", ""),
                next_visit=v.get("nextVisit", ""), status=v.get("status", "已拜访"),
                created_at=v.get("createdAt", time.time()),
            ))
            new_visits += 1

    new_leads = 0
    for l in req.leads:
        cid = l.get("id", "")
        if cid and cid not in existing_lead_ids:
            db.add(LeadRecord(
                client_id=cid, user_name=user_name,
                name=l.get("name", ""), contact=l.get("contact", ""),
                phone=l.get("phone", ""), channel=l.get("channel", ""),
                referrer=l.get("referrer", ""), industry=l.get("industry", ""),
                product=l.get("product", ""), amount=l.get("amount", 0),
                close_date=l.get("closeDate", ""), stage=l.get("stage", "初步接触"),
                created_at=l.get("createdAt", time.time()),
            ))
            new_leads += 1

    new_customers = 0
    for c in req.customers:
        cid = c.get("id", "")
        if cid and cid not in existing_customer_ids:
            db.add(CustomerRecord(
                client_id=cid, user_name=user_name,
                name=c.get("name", ""), contact=c.get("contact", ""),
                phone=c.get("phone", ""), level=c.get("level", ""),
                industry=c.get("industry", ""), size=c.get("size", ""),
                premium=c.get("premium", 0), decision=c.get("decision", ""),
                address=c.get("address", ""),
                created_at=c.get("createdAt", time.time()),
            ))
            new_customers += 1

    await db.commit()
    return {
        "ok": True,
        "synced": {"visits": new_visits, "leads": new_leads, "customers": new_customers},
    }


@app.get("/api/sync")
async def get_user_data(user: str = "", db: AsyncSession = Depends(get_db)):
    if not user:
        raise HTTPException(400, "缺少 user 参数")

    visits_r = await db.execute(
        sa.select(VisitRecord).where(VisitRecord.user_name == user)
    )
    visits = [to_visit_dict(v) for v in visits_r.scalars()]

    leads_r = await db.execute(
        sa.select(LeadRecord).where(LeadRecord.user_name == user)
    )
    leads = [to_lead_dict(l) for l in leads_r.scalars()]

    customers_r = await db.execute(
        sa.select(CustomerRecord).where(CustomerRecord.user_name == user)
    )
    customers = [to_customer_dict(c) for c in customers_r.scalars()]

    return {"ok": True, "visits": visits, "leads": leads, "customers": customers}


@app.get("/api/summary")
async def get_summary(db: AsyncSession = Depends(get_db)):
    users_r = await db.execute(sa.select(User))
    users = [{"name": u.name, "createdAt": u.created_at} for u in users_r.scalars()]

    visits_r = await db.execute(
        sa.select(VisitRecord).order_by(VisitRecord.created_at.desc())
    )
    visits = [to_visit_dict(v) for v in visits_r.scalars()]

    leads_r = await db.execute(
        sa.select(LeadRecord).order_by(LeadRecord.created_at.desc())
    )
    leads = [to_lead_dict(l) for l in leads_r.scalars()]

    customers_r = await db.execute(
        sa.select(CustomerRecord).order_by(CustomerRecord.created_at.desc())
    )
    customers = [to_customer_dict(c) for c in customers_r.scalars()]

    user_stats = {}
    for v in visits:
        un = v["userName"]
        s = user_stats.setdefault(un, {"visits": 0, "leads": 0, "customers": 0})
        s["visits"] += 1
    for l in leads:
        un = l["userName"]
        s = user_stats.setdefault(un, {"visits": 0, "leads": 0, "customers": 0})
        s["leads"] += 1
    for c in customers:
        un = c["userName"]
        s = user_stats.setdefault(un, {"visits": 0, "leads": 0, "customers": 0})
        s["customers"] += 1

    return {
        "ok": True, "users": users,
        "visits": visits, "leads": leads, "customers": customers,
        "userStats": user_stats,
        "totalUsers": len(users), "totalVisits": len(visits),
        "totalLeads": len(leads), "totalCustomers": len(customers),
    }


@app.post("/api/delete_visit")
async def delete_visit(data: dict, request: Request, db: AsyncSession = Depends(get_db)):
    client_id = data.get("id", "")
    user_name = request.headers.get("X-User-Name", "")
    if not client_id:
        raise HTTPException(400, "缺少 id")
    await db.execute(
        sa.delete(VisitRecord).where(
            VisitRecord.client_id == client_id,
            VisitRecord.user_name == user_name,
        )
    )
    await db.commit()
    return {"ok": True}


@app.post("/api/delete_lead")
async def delete_lead(data: dict, request: Request, db: AsyncSession = Depends(get_db)):
    client_id = data.get("id", "")
    user_name = request.headers.get("X-User-Name", "")
    if not client_id:
        raise HTTPException(400, "缺少 id")
    await db.execute(
        sa.delete(LeadRecord).where(
            LeadRecord.client_id == client_id,
            LeadRecord.user_name == user_name,
        )
    )
    await db.commit()
    return {"ok": True}


@app.post("/api/delete_customer")
async def delete_customer(data: dict, request: Request, db: AsyncSession = Depends(get_db)):
    client_id = data.get("id", "")
    user_name = request.headers.get("X-User-Name", "")
    if not client_id:
        raise HTTPException(400, "缺少 id")
    await db.execute(
        sa.delete(CustomerRecord).where(
            CustomerRecord.client_id == client_id,
            CustomerRecord.user_name == user_name,
        )
    )
    await db.commit()
    return {"ok": True}


# ============================================================
# PWA STATIC FILES
# ============================================================


@app.get("/static/manifest.json")
async def manifest():
    return JSONResponse(content=MANIFEST_JSON)


@app.get("/static/sw.js")
async def service_worker():
    return Response(content=SW_JS, media_type="application/javascript")


@app.get("/static/icon-192.png")
async def icon_192():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="192" height="192"><rect width="192" height="192" rx="38" fill="#2563eb"/><text x="96" y="130" text-anchor="middle" fill="white" font-size="90" font-weight="bold">太</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/static/icon-512.png")
async def icon_512():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="512" height="512" rx="100" fill="#2563eb"/><text x="256" y="340" text-anchor="middle" fill="white" font-size="240" font-weight="bold">太</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


# ============================================================
# STATIC HTML PAGES (with JS injection for API sync)
# ============================================================


def load_mobile_html():
    path = os.path.join(BASE_DIR, "templates", "手机端.html")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    script_inject = """<script>
window.API_BASE = "";
window._serverOnline = false;

async function checkServerStatus() {
  if (!window.me) return;
  try {
    var r = await fetch(window.API_BASE + "/api/health", { signal: AbortSignal.timeout(4000) });
    if (r.ok) { window._serverOnline = true; return; }
  } catch (e) {}
  window._serverOnline = false;
}
setInterval(checkServerStatus, 30000);


async function syncToServer(){
  if(!window.me) return;
  try {
    var data = {visits:window.st.visits, leads:window.st.leads, customers:window.st.customers};
    var r = await fetch(window.API_BASE + "/api/sync", {
      method:"POST",
      headers:{"Content-Type":"application/json","X-User-Name":window.me},
      body:JSON.stringify(data)
    });
    var j = await r.json();
    if(j.ok) console.log("已同步到服务器", j.synced);
  } catch(e) { console.log("同步失败（离线模式）", e); }
}

function patchLogin() {
  var _origHandleLogin = window.handleLogin;
  window.handleLogin = async function(){
    var name=document.getElementById("loginName").value.trim();
    var pwd=document.getElementById("loginPwd").value;
    var err=document.getElementById("loginError");err.style.display="none";
    if(!name){err.textContent="请输入姓名";err.style.display="block";return}
    if(!pwd||pwd.length<4){err.textContent="密码不少于4位";err.style.display="block";return}
    var sh=localStorage.getItem("p_"+name);
    if(window.isLogin){
      if(!sh){err.textContent="用户"+name+"不存在";err.style.display="block";return}
      if(shash(pwd)!==sh){err.textContent="密码错误";err.style.display="block";return}
    }else{
      if(sh){if(shash(pwd)!==sh){err.textContent="密码错误";err.style.display="block";return}}
      else localStorage.setItem("p_"+name, shash(pwd));
    }
    window.me=name;
    try {
      var rr = await fetch(window.API_BASE + "/api/register", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({name:name, password:pwd})
      });
      var rj = await rr.json();
      if(!rj.ok && rr.status !== 400) console.warn("注册失败", rj);
      if(rr.status === 400) {
        await fetch(window.API_BASE + "/api/login", {
          method:"POST", headers:{"Content-Type":"application/json"},
          body:JSON.stringify({name:name, password:pwd})
        });
      }
    } catch(e) { console.log("服务器不可用，离线模式", e); }
    try {
      var r = await fetch(window.API_BASE + "/api/sync?user=" + encodeURIComponent(name));
      var j = await r.json();
      if(j.ok) {
        if(j.visits && j.visits.length) {
          var existing = new Set((window.st.visits||[]).map(function(v){return v.id}));
          j.visits.forEach(function(v){if(!existing.has(v.id)){window.st.visits.push(v);existing.add(v.id)}});
        }
        if(j.leads && j.leads.length) {
          var existing = new Set((window.st.leads||[]).map(function(l){return l.id}));
          j.leads.forEach(function(l){if(!existing.has(l.id)){window.st.leads.push(l);existing.add(l.id)}});
        }
        if(j.customers && j.customers.length) {
          var existing = new Set((window.st.customers||[]).map(function(c){return c.id}));
          j.customers.forEach(function(c){if(!existing.has(c.id)){window.st.customers.push(c);existing.add(c.id)}});
        }
        window.save();
      }
    } catch(e) { console.log("服务器不可用，使用本地数据"); }
    document.getElementById("loginScreen").classList.add("hidden");
    document.getElementById("appBody").classList.add("show");
    document.getElementById("headerName").textContent=name;
    window.renderAll();
  };
}
patchLogin();

var _origSave = window.save;
window.save = function(){
  try {
    var ls = window._localStorage || localStorage;
    ls.setItem("d_" + window.me, JSON.stringify(window.st));
  } catch(e){}
  if(window._syncTimer) clearTimeout(window._syncTimer);
  window._syncTimer = setTimeout(syncToServer, 500);
};
</script>"""

    html = html.replace("<script>", script_inject + "<script>", 1)

    pwa_meta = """<link rel="manifest" href="/static/manifest.json">
<script>
if('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/sw.js').catch(function(){});
}
</script>"""
    html = html.replace("</head>", pwa_meta + "</head>")

    return html


def load_dashboard_html():
    path = os.path.join(BASE_DIR, "templates", "渠道数据分析与销售管理系统.html")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    script_inject = """<script>
window.API_BASE = "";
window.LIVE_MODE = true;

async function loadLiveData(){
  try {
    var r = await fetch(window.API_BASE + "/api/summary");
    var j = await r.json();
    if(!j.ok) return;
    window.ANALYSIS_DATA = buildAnalysisData(j);
    if(window.renderAll || window.initDashboard) {
      (window.renderAll || window.initDashboard)();
    } else {
      console.log("已加载实时数据", j.totalUsers + "人", j.totalVisits + "条拜访");
    }
    var badge = document.getElementById("liveBadge") || (function(){
      var b = document.createElement("span");
      b.id = "liveBadge";
      b.style.cssText = "display:inline-block;font-size:11px;font-weight:600;padding:2px 10px;border-radius:20px;background:#d1fae5;color:#059669;margin-left:8px";
      b.textContent = "\\u5b9e\\u65f6\\u6570\\u636e";
      var h = document.querySelector(".header-info") || document.querySelector(".header h1");
      if(h) h.appendChild(b);
      return b;
    })();
    badge.textContent = "\\u5b9e\\u65f6 \\u00b7 " + j.totalUsers + "\\u4eba";
  } catch(e) {
    console.log("无法加载实时数据，使用本地数据");
    var b = document.createElement("span");
    b.style.cssText = "display:inline-block;font-size:11px;font-weight:600;padding:2px 10px;border-radius:20px;background:#ffe4e6;color:#e11d48;margin-left:8px";
    b.textContent = "\\u79bb\\u7ebf\\u6570\\u636e";
    var h = document.querySelector(".header-info") || document.querySelector(".header h1");
    if(h) h.appendChild(b);
  }
}

function buildAnalysisData(j) {
  var visits = j.visits || [];
  var leads = j.leads || [];
  var customers = j.customers || [];
  var catStats = {};
  visits.forEach(function(v) {
    var cat = v.category || "\\u5176\\u4ed6";
    if(!catStats[cat]) catStats[cat] = {count:0, users:{}};
    catStats[cat].count++;
    catStats[cat].users[v.userName] = (catStats[cat].users[v.userName]||0) + 1;
  });
  var stageStats = {};
  leads.forEach(function(l) {
    var s = l.stage || "\\u521d\\u6b65\\u63a5\\u89e6";
    stageStats[s] = (stageStats[s]||0) + 1;
  });
  var userStats = j.userStats || {};
  var userList = Object.keys(userStats).map(function(un) {
    var s = userStats[un];
    return [un, s.visits||0, s.leads||0, s.customers||0];
  }).sort(function(a,b){return (b[1]+b[2]+b[3])-(a[1]+a[2]+a[3])});
  var monthData = {};
  visits.forEach(function(v) {
    var d = v.date || "";
    var m = d.slice(0,7);
    if(m) { monthData[m] = (monthData[m]||0) + 1; }
  });
  return {
    totalUsers: j.totalUsers, totalVisits: j.totalVisits,
    totalLeads: j.totalLeads, totalCustomers: j.totalCustomers,
    visits: visits, leads: leads, customers: customers,
    catStats: catStats, stageStats: stageStats,
    userList: userList, monthData: monthData,
  };
}

if(document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", loadLiveData);
} else {
  loadLiveData();
}
</script>"""

    html = html.replace("<script>", script_inject + "<script>", 1)
    return html


# ============================================================
# ROUTES
# ============================================================


@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>太平洋产险 · 外勤助手</title><style>
body{margin:0;background:linear-gradient(135deg,#2563eb,#1d4ed8);min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:-apple-system,BlinkMacSystemFont,sans-serif;text-align:center;padding:20px}
.card{background:#fff;border-radius:20px;padding:40px 32px;max-width:400px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,.2)}
.logo{width:56px;height:56px;border-radius:14px;background:#2563eb;display:flex;align-items:center;justify-content:center;color:#fff;font-size:28px;font-weight:800;margin:0 auto 12px}
h1{font-size:20px;color:#1e293b;margin:0 0 4px}
p{font-size:13px;color:#64748b;margin:0 0 24px;line-height:1.5}
.btn{display:block;padding:14px;border-radius:10px;border:none;font-size:15px;font-weight:700;cursor:pointer;text-decoration:none;margin-bottom:10px;transition:opacity .15s}
.btn-pri{background:#2563eb;color:#fff}
.btn-out{background:transparent;color:#2563eb;border:1.5px solid #2563eb}
.btn:active{opacity:.85}
.hint{font-size:11px;color:#94a3b8;margin-top:16px;line-height:1.6}
</style></head><body>
<div class="card">
  <div class="logo">太</div>
  <h1>太平洋产险 · 外勤助手</h1>
  <p>外勤工作记录与销售管理系统</p>
  <a class="btn btn-pri" href="/mobile">外勤记录（员工）</a>
  <a class="btn btn-out" href="/dashboard">销售看板（管理者）</a>
  <div class="hint">在手机浏览器打开后，点"添加到主屏幕"<br>像 App 一样每天使用</div>
</div>
</body></html>"""


@app.get("/mobile", response_class=HTMLResponse)
async def mobile_app():
    html = load_mobile_html()
    if html is None:
        return "<h1>手机端页面未找到</h1><p>请确保 templates/手机端.html 文件存在</p>"
    return html


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_app():
    html = load_dashboard_html()
    if html is None:
        return "<h1>看板页面未找到</h1><p>请确保 templates/渠道数据分析与销售管理系统.html 文件存在</p>"
    return html


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import uvicorn
    print()
    print("  WEB 外勤助手服务器: http://localhost:%d" % PORT)
    print("  PHN 员工入口: /mobile")
    print("  DASH 管理看板: /dashboard")
    print("  TIP Ctrl+C 停止")
    print()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
