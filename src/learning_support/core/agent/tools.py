# -*- coding: utf-8 -*-
"""
==============================================================================
 提示词 6/7 · Agent 工具定义与执行（Function Calling 规范）
==============================================================================
 5 个工具：search_policy / query_resources / check_schedule /
          create_plan_draft / submit_request
 前两个只读；后三个有副作用，受 guard.py 的三层防线约束。

 ★ 本模块后续阶段复用：阶段 4（Web 聊天接口）把 run_agent 挂到 /chat 后端
==============================================================================
"""
import json
import re
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

# 让 src 包可导入阶段 1 的检索核心（课程演示从单脚本起步，真实项目应拆成包）
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "examples"))
from bare_qa_prototype import build_knowledge_base  # 复用★检索核心

# ─────────────────────── 全局共享检索器（进程内单例） ───────────────────────
_retriever_cache = {}

def get_retriever(policy_path: str):
    """按文件路径缓存检索器，避免每次提问都重建索引。"""
    if policy_path not in _retriever_cache:
        r, name = build_knowledge_base(policy_path)
        _retriever_cache[policy_path] = (r, name)
    return _retriever_cache[policy_path]


# ════════════════════ Function Calling 工具注册表 ════════════════════
# 这份 schema 会原样传给 LLM 的 tools 参数，模型据此决定调用哪个工具
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_policy",
        "description": "检索学习支持制度条文。凡涉及预约规则、答疑规范、隐私制度、服务范围的问题必须先调用，不得凭记忆回答。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索关键词或自然语言问题"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "query_resources",
        "description": "检索学习方法指南、课程FAQ、复习建议等学习资源。学习方法类问题优先用此工具。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "course": {"type": "string", "description": "可选：课程名"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "check_schedule",
        "description": "查询辅导教师空闲时段并校验预约可行性（时段冲突、每周2次限额）。生成预约草稿前必须调用且须 valid=true。",
        "parameters": {"type": "object", "properties": {
            "slot": {"type": "string", "description": "期望时段 YYYY-MM-DD HH:MM"},
            "teacher_id": {"type": "integer"},
            "student_id": {"type": "integer"}},
            "required": ["slot"]}}},
    {"type": "function", "function": {
        "name": "create_plan_draft",
        "description": "生成学习计划或预约草稿（不会生效）。预约类必须先通过 check_schedule；草稿须学生确认后才能 submit。",
        "parameters": {"type": "object", "properties": {
            "draft_type": {"type": "string", "enum": ["plan", "booking"]},
            "content": {"type": "object"}},
            "required": ["draft_type", "content"]}}},
    {"type": "function", "function": {
        "name": "submit_request",
        "description": "提交学习支持请求使草稿生效。只能由学生确认动作触发，学生未确认时严禁调用。",
        "parameters": {"type": "object", "properties": {
            "request_id": {"type": "integer"}}, "required": ["request_id"]}}},
]


# ════════════════════ 工具实现（每个工具一个函数） ════════════════════
class ToolExecutor:
    """工具执行器：负责真正执行工具并把结果序列化为回填给模型的文本。"""

    def __init__(self, db_path: str, policy_path: str):
        self.db_path = db_path
        self.policy_path = policy_path
        # 内存版预约数据（课程演示用；真实实现应查数据库）
        # 空闲时段动态生成：未来第 3、4 天，避免演示日期过期
        d1 = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d 15:00")
        d2 = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d 16:00")
        d3 = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d 14:00")
        d4 = (datetime.now() + timedelta(days=6)).strftime("%Y-%m-%d 14:00")
        self.teacher_slots = {
            1: [(d1, "高等数学答疑"), (d2, "学习方法"),
                (d3, "程序设计答疑"), (d4, "高等数学答疑")],
        }
        self.student_weekly_bookings = 1   # 该生本周已预约 1 次（限额 2 次）
        self.bookings: list[dict] = []     # 已生效预约

    # ---- 工具 1/2：检索类（复用阶段 1 混合检索） ----
    def _search(self, query: str, top_k: int = 3) -> str:
        r, name = get_retriever(self.policy_path)
        hits = r.search(query, top_k)
        return json.dumps({
            "source": name,
            "hits": [{"snippet": h["chunk"][:200], "score": round(h["score"], 3)}
                     for h in hits],
        }, ensure_ascii=False)

    def search_policy(self, query: str) -> str:
        return self._search(query)

    def query_resources(self, query: str, course: str = "") -> str:
        q = f"{course} {query}".strip()
        return self._search(q)

    # ---- 工具 3：时段校验（只读，但结果是防线 2 的依据） ----
    def check_schedule(self, slot: str, teacher_id: int = 1,
                       student_id: int = 0) -> str:
        # 校验 1：时段格式
        try:
            t = datetime.strptime(slot, "%Y-%m-%d %H:%M")
        except ValueError:
            return json.dumps({"valid": False, "reason": "时段格式应为 YYYY-MM-DD HH:MM"},
                              ensure_ascii=False)
        # 校验 2：不能约过去的时间
        if t < datetime.now():
            return json.dumps({"valid": False, "reason": "不能预约过去的时段"},
                              ensure_ascii=False)
        # 校验 3：教师该时段是否存在
        all_slots = [s for s, _ in self.teacher_slots.get(teacher_id, [])]
        if slot not in all_slots:
            return json.dumps({"valid": False,
                               "reason": f"教师无此时段；可预约时段：{all_slots}"},
                              ensure_ascii=False)
        # 校验 4：每周 2 次限额（制度第四条）
        if self.student_weekly_bookings >= 2:
            return json.dumps({"valid": False, "reason": "已达到每周 2 次一对一辅导限额（制度第四条）"},
                              ensure_ascii=False)
        return json.dumps({"valid": True, "teacher_id": teacher_id, "slot": slot,
                           "duration_minutes": 45}, ensure_ascii=False)

    # ---- 工具 4：建草稿（写入，防线 2 拦截点之一） ----
    def create_plan_draft(self, draft_type: str, content: dict,
                          session_ctx: dict) -> str:
        # 防线 2：预约类草稿必须有 check_schedule 通过的记录（状态机持久在 session_ctx）
        if draft_type == "booking" and not session_ctx.get("schedule_valid"):
            return json.dumps({"error": "GUARD_BLOCKED",
                               "message": "必须先调用 check_schedule 且 valid=true 才能创建预约草稿"},
                              ensure_ascii=False)
        # 落库：support_requests 表，状态 draft
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            "INSERT INTO support_requests(session_id, user_id, request_type, "
            "draft_content, status, schedule_check, created_at) VALUES(?,?,?,?,?,?,?)",
            (session_ctx.get("session_id", "cli"), 1, draft_type,
             json.dumps(content, ensure_ascii=False), "draft",
             json.dumps(session_ctx.get("schedule_result", {}), ensure_ascii=False),
             datetime.now().isoformat()))
        rid = cur.lastrowid
        conn.commit(); conn.close()
        return json.dumps({"request_id": rid, "status": "draft",
                           "message": "草稿已生成，等待学生确认"}, ensure_ascii=False)

    # ---- 工具 5：提交（写入，防线 3 数据层兜底） ----
    def submit_request(self, request_id: int, session_ctx: dict) -> str:
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT status FROM support_requests WHERE id=?",
                           (request_id,)).fetchone()
        if not row:
            conn.close()
            return json.dumps({"error": "NOT_FOUND"}, ensure_ascii=False)
        # 防线 3：再次确认草稿状态且学生已确认（数据层最终兜底）
        if row[0] != "draft" or not session_ctx.get("student_confirmed"):
            conn.close()
            return json.dumps({"error": "GUARD_BLOCKED",
                               "message": "草稿尚未经学生确认，无法提交"},
                              ensure_ascii=False)
        conn.execute("UPDATE support_requests SET status='confirmed', confirmed_at=? WHERE id=?",
                     (datetime.now().isoformat(), request_id))
        conn.commit(); conn.close()
        if session_ctx.get("draft_type") == "booking":
            self.student_weekly_bookings += 1
            self.bookings.append({"request_id": request_id,
                                  "slot": session_ctx.get("schedule_result", {}).get("slot")})
        return json.dumps({"request_id": request_id, "status": "confirmed",
                           "message": "请求已生效"}, ensure_ascii=False)

    # ---- 统一入口：工具名 → 执行 ----
    def execute(self, name: str, args: dict, session_ctx: dict) -> str:
        if name == "search_policy":
            return self.search_policy(args["query"])
        if name == "query_resources":
            return self.query_resources(args.get("query", ""), args.get("course", ""))
        if name == "check_schedule":
            result = self.check_schedule(args["slot"], args.get("teacher_id", 1),
                                         args.get("student_id", 1))
            data = json.loads(result)
            if data.get("valid"):   # 校验通过 → 记入会话上下文，供防线 2 判断
                session_ctx["schedule_valid"] = True
                session_ctx["schedule_result"] = data
            return result
        if name == "create_plan_draft":
            result = self.create_plan_draft(args["draft_type"], args["content"],
                                            session_ctx)
            data = json.loads(result)
            if data.get("request_id"):
                session_ctx["draft_request_id"] = data["request_id"]
                session_ctx["draft_type"] = args["draft_type"]
            return result
        if name == "submit_request":
            # 演示占位符：@draft 自动解析为本次会话刚生成的草稿ID
            rid = args.get("request_id")
            if rid == "@draft":
                rid = session_ctx.get("draft_request_id")
            return self.submit_request(rid, session_ctx)
        return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)
