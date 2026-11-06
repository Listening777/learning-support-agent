# -*- coding: utf-8 -*-
"""FastAPI 应用入口：挂载路由 + 静态页面 + 启动初始化。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from learning_support.models import db
from learning_support.api.routes import router
from learning_support import config

app = FastAPI(title="学习支持与辅导协作智能体系统")
app.include_router(router)

WEB = Path(__file__).resolve().parents[2] / "web"


@app.on_event("startup")
def startup():
    """启动初始化：建库建表 + 自动灌入默认制度语料 + 加载知识库索引。

    云部署（数据库为空）时自动把 data/policy 下的制度文档灌入知识库，
    无需人工上传即可开箱问答；本地已有数据时跳过，不重复灌入。
    """
    db.init_db()
    from learning_support.services import kb_service
    conn = db.get_conn()
    has_docs = conn.execute(
        "SELECT 1 FROM policy_documents WHERE status='active' LIMIT 1").fetchone()
    conn.close()
    if not has_docs:
        policy_dir = config.POLICY_DIR
        if policy_dir.exists():
            for md in sorted(policy_dir.glob("*.md")):
                kb_service.upload_document(md.name, md.read_bytes(),
                                           doc_type="policy", uploader_id=1)
            print("[启动] 首次部署：已自动灌入默认制度语料")
    n = kb_service.rebuild_index()
    print(f"[启动] 数据库就绪，知识库索引加载 {n} 个切片")


# 学生聊天页（单页应用）
@app.get("/chat")
def chat_page():
    return FileResponse(WEB / "chat.html")


# 管理后台（服务端渲染骨架 + fetch 数据）
@app.get("/admin/{page}")
def admin_page(page: str):
    allowed = {"dashboard", "kb", "prompt", "audit"}
    if page not in allowed:
        return FileResponse(WEB / "404.html", status_code=404)
    return FileResponse(WEB / "admin" / f"{page}.html")


@app.get("/admin")
def admin_index():
    return RedirectResponse("/admin/dashboard")


@app.get("/")
def index():
    return RedirectResponse("/chat")

app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
