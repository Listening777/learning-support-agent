# -*- coding: utf-8 -*-
"""
API 路由层（提示词4 + 提示词5）
  · /api/auth/*    登录鉴权（JWT，学生/管理员两种角色）
  · /api/chat      学生问答入口（SSE 流式：session/status/delta/citations/done/error）
  · /api/sessions  会话列表与历史回显
  · /api/admin/*   知识库管理 / 提示词模板管理 / 对话审计（仅管理员）
统一约定：业务失败返回 {code, message} 中文错误信息；0 表示成功。
错误码：0成功 4001参数 4003无权 4011未登录 4012需管理员 4013学生不可访问
       4004文件不支持 4293限流 5001LLM失败 5002检索/存储异常 5003文档解析失败
"""
import json
import time
import uuid
import re
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse

from .. import config
from ..models import db
from ..core.security import verify_password, make_jwt, verify_jwt
from ..services import kb_service

router = APIRouter()


# ═════════════════ 通用工具：鉴权 / 错误响应 / 限流 ═════════════════
def err(code: int, message: str, status: int = 200):
    """统一错误响应：先返回错误码和中文错误信息（老师约束）。"""
    from fastapi.responses import JSONResponse
    return JSONResponse({"code": code, "message": message}, status_code=status)


def current_user(request: Request) -> dict | None:
    """从 Authorization 头解析 JWT，返回 payload 或 None。"""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    return verify_jwt(token) if token else None


def require_student(request: Request):
    """学生鉴权依赖：管理员访问学生接口返回 4013。"""
    user = current_user(request)
    if not user:
        return None, err(4011, "请先登录", 401)
    if user["role"] != "student":
        return None, err(4013, "该接口仅限学生访问")
    return user, None


def require_admin(request: Request):
    """管理员鉴权依赖：学生访问管理接口返回 4012（接口级权限校验）。"""
    user = current_user(request)
    if not user:
        return None, err(4011, "请先登录", 401)
    if user["role"] != "admin":
        return None, err(4012, "需要管理员权限")
    return user, None

# 简易内存限流器：{uid: [时间戳...]}
_rate_bucket: dict[int, list[float]] = {}


def rate_limited(uid: int) -> bool:
    """单用户限流：每分钟最多 RATE_LIMIT_PER_MIN 次（提示词9并发项）。"""
    now = time.time()
    bucket = [t for t in _rate_bucket.get(uid, []) if now - t < 60]
    if len(bucket) >= config.RATE_LIMIT_PER_MIN:
        _rate_bucket[uid] = bucket
        return True
    bucket.append(now)
    _rate_bucket[uid] = bucket
    return False


# ═════════════════ 1. 登录鉴权（提示词5 第1项） ═════════════════
@router.post("/api/auth/login")
async def login(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        return err(4001, "用户名和密码不能为空")
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not row or not verify_password(password, row["password_hash"]):
        return err(4011, "用户名或密码错误")
    token = make_jwt({"uid": row["id"], "role": row["role"],
                      "name": row["real_name"]})
    return {"code": 0, "token": token, "role": row["role"],
            "name": row["real_name"]}


@router.get("/api/auth/me")
async def me(request: Request):
    user = current_user(request)
    if not user:
        return err(4011, "请先登录", 401)
    return {"code": 0, "uid": user["uid"], "role": user["role"],
            "name": user["name"]}


# ═════════════════ 2. 学生问答入口：POST /api/chat（SSE 流式） ═════════════════
def _sse(event: str, data: dict) -> str:
    """格式化一条 SSE 帧。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _get_or_create_session(session_id: str | None, uid: int,
                           question: str) -> tuple[str, bool]:
    """会话处理：无 id 则新建；有 id 则校验归属。返回 (session_id, 新建?)。"""
    conn = db.get_conn()
    now = datetime.now().isoformat()
    if not session_id:
        sid = uuid.uuid4().hex[:16]
        title = question[:20]
        conn.execute("INSERT INTO sessions(id,user_id,title,created_at,updated_at)"
                     " VALUES(?,?,?,?,?)", (sid, uid, title, now, now))
        conn.commit(); conn.close()
        return sid, True
    row = conn.execute("SELECT user_id FROM sessions WHERE id=?",
                       (session_id,)).fetchone()
    if not row or row["user_id"] != uid:
        conn.close()
        return "", False
    conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
    conn.commit(); conn.close()
    return session_id, False


def _recent_history(session_id: str, rounds: int) -> list[dict]:
    """取最近 N 轮历史（rounds 轮 = 2N 条消息），旧→新排列。"""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? "
        "ORDER BY id DESC LIMIT ?", (session_id, rounds * 2)).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]}
            for r in reversed(rows) if r["role"] in ("user", "assistant")]


def _save_message(session_id: str, role: str, content: str,
                  citations: str = "") -> None:
    conn = db.get_conn()
    conn.execute("INSERT INTO messages(session_id,role,content,citations,created_at)"
                 " VALUES(?,?,?,?,?)",
                 (session_id, role, content, citations,
                  datetime.now().isoformat()))
    conn.commit(); conn.close()


@router.post("/api/chat")
async def chat(request: Request):
    """
    SSE 流式问答接口。事件流顺序：
      session → status(检索中) → delta(逐段) → citations → done
      出错时推送 error 事件（中文提示），不中断连接。
    多轮确认机制：草稿生成后进入"待确认"状态，学生回复"确认"即提交
      （对应提示词7确认中断，Web 版用下一轮对话实现确认）。
    """
    user, e = require_student(request)
    if e:
        return e
    body = await request.json()
    question = (body.get("question") or "").strip()
    session_id = body.get("session_id")
    if not question:
        return err(4001, "问题不能为空")
    if len(question) > 500:
        return err(4001, "问题不能超过 500 字")
    if rate_limited(user["uid"]):
        return err(4293, "请求过于频繁，请稍后再试")

    sid, created = _get_or_create_session(session_id, user["uid"], question)
    if not sid:
        return err(4003, "会话不存在或无权访问")

    async def stream():
        # —— 事件1：会话 ID ——
        yield _sse("session", {"session_id": sid, "created": created})

        # —— 多轮确认：若存在待确认草稿且学生回复"确认"，直接提交 ——
        conn = db.get_conn()
        pending = conn.execute(
            "SELECT id FROM support_requests WHERE session_id=? AND status='draft'"
            " ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
        conn.close()
        if pending and re.fullmatch(r"(确认|同意|好的，确认|y|yes)", question):
            _save_message(sid, "user", question)
            ctx = {"student_confirmed": True, "draft_request_id": pending["id"]}
            from ..core.agent.tools import ToolExecutor
            ex = ToolExecutor(config.DB_PATH, str(config.POLICY_DIR /
                                                  "学习支持与辅导协作管理制度.md"))
            result = ex.execute("submit_request", {"request_id": "@draft"}, ctx)
            text = "已确认，你的学习支持请求已生效。" if "confirmed" in result \
                else "提交失败：" + json.loads(result).get("message", "未知原因")
            _save_message(sid, "assistant", text)
            yield _sse("delta", {"content": text})
            yield _sse("done", {"message": "confirmed"})
            return

        # —— 保存学生消息 ——
        _save_message(sid, "user", question)

        # —— 事件2：检索状态 ——
        yield _sse("status", {"message": "正在检索学习支持制度…"})

        # —— 混合检索 top3（复用阶段1核心；失败推送 error 不编造） ——
        try:
            hits = kb_service.hybrid_search(question)
        except Exception as exc:   # 检索异常兜底（提示词9异常项）
            yield _sse("error", {"code": 5002,
                                 "message": "制度检索暂时不可用，未找到依据，请稍后重试"})
            return

        # —— 组装上下文：系统提示词（数据库动态读取）+ 最近6轮历史 + 条文 ——
        system_prompt = db.get_system_prompt() + config.SYSTEM_GUARD_PROMPT
        history = _recent_history(sid, config.CHAT_HISTORY_ROUNDS)
        context = "\n\n".join(f"【条文 {i+1}】{h['snippet']}" for i, h in enumerate(hits))
        user_content = (f"以下是检索到的学习支持制度条文：\n{context}\n\n"
                        f"学生问题：{question}\n"
                        f"请仅依据以上条文回答；若条文中没有答案，回答“制度中未找到”。")

        # —— 心理危机/注入风险扫描：命中 high 规则时强制追加转人工提示 ——
        risk_hits = db.scan_risk(question)
        if any(r["level"] == "high" for r in risk_hits):
            user_content += ("\n\n【系统强制提示】该消息疑似涉及学生情绪/心理危机，"
                             "你必须表达关心并提示联系学校心理健康教育中心"
                             "（010-12345678）或真人辅导老师，不得自行进行心理疏导。")

        messages = [{"role": "system", "content": system_prompt}] + history + \
                   [{"role": "user", "content": user_content}]

        # —— LLM 流式生成（未配置 key 时降级为直接流式输出检索条文） ——
        full = ""
        citations = [{"filename": h["filename"],
                      "snippet": h["snippet"][:100]} for h in hits]
        try:
            if config.LLM_API_KEY:
                from ..core.llm_client import stream_llm
                for piece in stream_llm(messages):
                    full += piece
                    yield _sse("delta", {"content": piece})
            else:
                # 降级模式：无 LLM 时把检索条文作为回答（保证流程可演示）
                text = (f"根据检索到的学习支持制度条文：\n{context}\n"
                        f"（当前未配置 LLM_API_KEY，以上为制度原文检索结果）")
                full = text
                yield _sse("delta", {"content": text})
        except Exception:
            yield _sse("error", {"code": 5001,
                                 "message": "助手暂时不可用，请稍后重试或联系辅导老师"})
            return

        # —— 落库 + 事件3/4：引用来源与结束 ——
        _save_message(sid, "assistant", full,
                      json.dumps(citations, ensure_ascii=False))
        yield _sse("citations", {"citations": citations})
        yield _sse("done", {"message": "ok"})

    return StreamingResponse(stream(), media_type="text/event-stream")


# ═════════════════ 3. 会话管理 ═════════════════
@router.get("/api/sessions")
async def list_sessions(request: Request):
    user, e = require_student(request)
    if e:
        return e
    conn = db.get_conn()
    rows = conn.execute("SELECT id, title, updated_at FROM sessions "
                        "WHERE user_id=? ORDER BY updated_at DESC",
                        (user["uid"],)).fetchall()
    conn.close()
    return {"code": 0, "sessions": [dict(r) for r in rows]}


@router.get("/api/sessions/{sid}/messages")
async def session_messages(sid: str, request: Request):
    user, e = require_student(request)
    if e:
        return e
    conn = db.get_conn()
    row = conn.execute("SELECT user_id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not row or row["user_id"] != user["uid"]:
        conn.close()
        return err(4003, "会话不存在或无权访问")
    rows = conn.execute("SELECT role, content, citations FROM messages "
                        "WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    conn.close()
    return {"code": 0, "messages": [
        {"role": r["role"], "content": r["content"],
         "citations": json.loads(r["citations"]) if r["citations"] else []}
        for r in rows]}


# ═════════════════ 4. 管理后台接口（提示词5，仅管理员） ═════════════════
@router.get("/api/admin/stats")
async def admin_stats(request: Request):
    user, e = require_admin(request)
    if e:
        return e
    conn = db.get_conn()
    stats = {
        "documents": conn.execute("SELECT COUNT(*) FROM policy_documents "
                                  "WHERE status='active'").fetchone()[0],
        "chunks": conn.execute("SELECT COUNT(*) FROM doc_chunks").fetchone()[0],
        "sessions": conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
        "messages": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
    }
    conn.close()
    return {"code": 0, "stats": stats}


@router.post("/api/admin/kb/upload")
async def kb_upload(request: Request, file: UploadFile = File(...),
                    doc_type: str = Form("policy")):
    """知识库上传：md/pdf → 解析 → 500字切片 → 向量化入库（提示词5 第2项）。"""
    user, e = require_admin(request)
    if e:
        return e
    if doc_type not in ("policy", "faq", "guide"):
        return err(4001, "文档类型必须是 policy/faq/guide")
    raw = await file.read()
    if len(raw) > config.MAX_UPLOAD_MB * 1024 * 1024:
        return err(4004, f"文件不能超过 {config.MAX_UPLOAD_MB}MB")
    if not file.filename.lower().endswith((".md", ".txt", ".markdown", ".pdf")):
        return err(4004, "仅支持 markdown/txt/pdf 文件")
    try:
        result = kb_service.upload_document(file.filename, raw, doc_type,
                                            user["uid"])
    except ValueError as exc:
        return err(4004, str(exc))
    except Exception:
        return err(5003, "文档解析失败，请检查文件是否损坏")
    return {"code": 0, **result}


@router.get("/api/admin/kb/list")
async def kb_list(request: Request):
    user, e = require_admin(request)
    if e:
        return e
    conn = db.get_conn()
    rows = conn.execute("SELECT id, filename, doc_type, chunk_count, status,"
                        " created_at FROM policy_documents WHERE status='active'"
                        " ORDER BY id DESC").fetchall()
    conn.close()
    return {"code": 0, "documents": [dict(r) for r in rows]}


@router.delete("/api/admin/kb/{doc_id}")
async def kb_delete(doc_id: int, request: Request):
    user, e = require_admin(request)
    if e:
        return e
    return {"code": 0, "deleted": kb_service.delete_document(doc_id)} \
        if kb_service.delete_document(doc_id) else err(4001, "文档不存在")


@router.get("/api/admin/prompt")
async def get_prompt(request: Request):
    user, e = require_admin(request)
    if e:
        return e
    return {"code": 0, "content": db.get_system_prompt()}


@router.put("/api/admin/prompt")
async def update_prompt(request: Request):
    """提示词管理：存数据库新版本，问答立即生效（不写死在代码里）。"""
    user, e = require_admin(request)
    if e:
        return e
    body = await request.json()
    content = (body.get("content") or "").strip()
    if not content:
        return err(4001, "提示词内容不能为空")
    if len(content) > 2000:
        return err(4001, "提示词不能超过 2000 字")
    conn = db.get_conn()
    conn.execute("INSERT INTO system_prompts(content,is_default,updated_by,"
                 "created_at) VALUES(?,0,?,?)",
                 (content, user["uid"], datetime.now().isoformat()))
    conn.commit(); conn.close()
    return {"code": 0, "message": "已保存，立即对问答生效"}


@router.post("/api/admin/prompt/reset")
async def reset_prompt(request: Request):
    user, e = require_admin(request)
    if e:
        return e
    conn = db.get_conn()
    conn.execute("INSERT INTO system_prompts(content,is_default,updated_by,"
                 "created_at) VALUES(?,1,?,?)",
                 (config.DEFAULT_SYSTEM_PROMPT, user["uid"],
                  datetime.now().isoformat()))
    conn.commit(); conn.close()
    return {"code": 0, "message": "已恢复默认模板"}


@router.get("/api/admin/audit")
async def audit(request: Request, student_id: int | None = None,
                date_from: str | None = None, date_to: str | None = None):
    """对话审计：按学生/时间筛选；命中风险规则的消息带 risk 标记（红/黄）。"""
    user, e = require_admin(request)
    if e:
        return e
    conn = db.get_conn()
    sql = ("SELECT m.id, m.session_id, s.user_id, u.real_name, u.username,"
           " m.role, m.content, m.created_at FROM messages m "
           "JOIN sessions s ON m.session_id=s.id "
           "JOIN users u ON s.user_id=u.id WHERE m.role='user'")
    params: list = []
    if student_id:
        sql += " AND s.user_id=?"; params.append(student_id)
    if date_from:
        sql += " AND m.created_at>=?"; params.append(date_from)
    if date_to:
        sql += " AND m.created_at<=?"; params.append(date_to + "T23:59:59")
    sql += " ORDER BY m.id DESC LIMIT 100"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    # 管理员操作留痕（呼应制度第九条审计合规）
    conn.execute("INSERT INTO agent_logs(session_id,turn,tool_name,arguments,"
                 "duration_ms,result_summary,created_at) VALUES('audit',0,"
                 "'admin_view',?,0,'查看审计页',?)",
                 (json.dumps({"by": user["uid"]}), datetime.now().isoformat()))
    conn.commit(); conn.close()
    for r in rows:
        r["risk"] = [{"level": h["level"], "note": h["note"]}
                     for h in db.scan_risk(r["content"])]
    return {"code": 0, "records": rows}


@router.get("/api/health")
async def health():
    return {"code": 0, "message": "ok"}
