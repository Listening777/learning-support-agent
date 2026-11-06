# -*- coding: utf-8 -*-
"""
==============================================================================
 提示词 7 · Agent 编排演示：3 个典型对话轨迹（CLI 版）
==============================================================================
 运行：python examples/agent_demo.py
 未配置 LLM_API_KEY 时自动进入演示模式（mock 决策脚本回放轨迹），
 用于验证：5 轮循环 / 日志表 / 顺序约束防线 / 确认中断机制。
==============================================================================
"""
import sys
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

# 动态生成未来第 3 天的时段（避免演示日期过期被 check_schedule 拦截）
FUTURE_SLOT = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d 15:00")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from learning_support.core.agent.tools import ToolExecutor
from learning_support.core.agent.loop import run_agent

DB = str(ROOT / "data" / "agent_demo.db")
POLICY = str(ROOT / "data" / "policy" / "学习支持与辅导协作管理制度.md")


def init_db():
    """建 Agent 阶段需要的两张表：support_requests + agent_logs。"""
    conn = sqlite3.connect(DB)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS support_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, user_id INTEGER,
        request_type TEXT,                 -- plan=学习计划 / booking=辅导预约
        draft_content TEXT,                -- 草稿内容(JSON)
        status TEXT,                       -- draft → confirmed / cancelled
        schedule_check TEXT,               -- check_schedule 校验结果(JSON)
        created_at TEXT, confirmed_at TEXT);
    CREATE TABLE IF NOT EXISTS agent_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, turn INTEGER,     -- 第几轮循环
        tool_name TEXT, arguments TEXT,
        duration_ms INTEGER, result_summary TEXT, created_at TEXT);
    """)
    conn.commit(); conn.close()


def make_confirm_hook(auto: bool):
    """
    确认中断回调。CLI 演示：auto=True 自动确认（模拟学生说"确认"），
    阶段 4 中此回调替换为：SSE 推送草稿 → 前端渲染确认按钮 → 等待学生点击。
    """
    def hook(draft_text: str) -> bool:
        print(draft_text)
        if auto:
            print("（演示模式：自动确认）")
            return True
        return input("学生确认？(y/n) > ").strip().lower() == "y"
    return hook


# ──────────────────── 轨迹 a：纯制度/方法问答，不调写入工具 ────────────────────
MOCK_A = [
    {"name": "query_resources", "args": {"query": "期末复习 每天学习时长", "course": "高等数学"}},
    "根据制度第七条，高等数学建议每周复习 6 小时以上、其中习题练习不少于 3 小时，"
    "折算到每天约 1 小时复习加 30 分钟习题。建议配合每周 1 次辅导预约巩固薄弱章节。"
    "（引用来源：学习支持与辅导协作管理制度.md 第四章第七条）",
]

# ── 轨迹 b：完整链路（第1轮并行查制度+资源 → 查时段 → 建草稿 → 确认 → 提交） ──
MOCK_B = [
    # 第 1 轮：模型一次决策并行调用两个只读工具（Function Calling 支持多工具调用）
    [{"name": "search_policy", "args": {"query": "辅导预约流程 限额"}},
     {"name": "query_resources", "args": {"query": "期末复习计划", "course": "高等数学"}}],
    # 第 2 轮：校验时段（预约前置条件）
    {"name": "check_schedule", "args": {"slot": FUTURE_SLOT, "teacher_id": 1}},
    # 第 3 轮：校验通过后建草稿 → 触发确认中断
    {"name": "create_plan_draft", "args": {"draft_type": "booking", "content": {
        "teacher_id": 1, "slot": FUTURE_SLOT, "topic": "高等数学期末复习",
        "duration_minutes": 45,
        "plan": ["第1周：极限与导数错题复盘", "第2周：积分专项练习",
                 "第3周：真题模拟 + 答疑"]}}},
    # 学生已在确认中断中点击"确认"（student_confirmed=True），此时提交才放行
    {"name": "submit_request", "args": {"request_id": "@draft"}},
    "已为你生成学习计划与预约草稿并经你确认，预约已生效，辅导时段为该日 15:00，时长 45 分钟。"
    "计划共 3 周推进，请按周完成对应任务。（引用来源：制度第二章第三、四条）",
]

# ── 轨迹 c：学生问解题对错 → 检索 FAQ/答疑规范 → 给结论与依据 ──
MOCK_C = [
    {"name": "search_policy", "args": {"query": "答疑规范 作业 代做"}},
    {"name": "query_resources", "args": {"query": "解题思路检查", "course": "程序设计"}},
    "制度第五条规定答疑仅讲解思路与方法、不得代替完成作业。请把你的解法思路发给我，"
    "我可以对照课程 FAQ 指出思路中的问题并给出检查清单，但不会直接替你改作业结论。"
    "（引用来源：制度第三章第五条）",
]


def run_case(title: str, tag: str, question: str, mock: list, auto_confirm: bool):
    print("\n" + "=" * 66)
    print(f"【{title}】学生问题：{question}")
    print("=" * 66)
    executor = ToolExecutor(DB, POLICY)
    session_ctx = {"session_id": f"demo-{tag}"}
    result = run_agent(
        question=question, history=[], executor=executor,
        session_ctx=session_ctx, db_path=DB,
        session_id=session_ctx["session_id"],
        confirm_hook=make_confirm_hook(auto_confirm),
        mock_script=list(mock))
    print(f"\n[会话状态] {result['status']}  |  工具调用 {len(result['logs'])} 次")
    for r in result["logs"]:
        print(f"  · 第{r['turn']}轮 {r['tool']} {r['duration_ms']}ms")


def main():
    init_db()
    # 轨迹 a：只读问答
    run_case("轨迹a", "a", "期末复习一般每天学几个小时比较合理？", MOCK_A, False)
    # 轨迹 b：完整预约链路（含确认中断，自动确认）
    run_case("轨迹b", "b", "帮我制定下个月的高数期末复习计划并预约辅导", MOCK_B, True)
    # 轨迹 c：解题检查类
    run_case("轨迹c", "c", "我这道题的解法对吗", MOCK_C, False)

    # 展示落库结果
    print("\n" + "=" * 66)
    conn = sqlite3.connect(DB)
    print("[support_requests 表]")
    for row in conn.execute("SELECT id, request_type, status, created_at FROM support_requests"):
        print(f"  #{row[0]} type={row[1]} status={row[2]} at={row[3]}")
    print("[agent_logs 表（最近 6 条）]")
    for row in conn.execute("SELECT turn, tool_name, duration_ms FROM agent_logs ORDER BY id DESC LIMIT 6"):
        print(f"  第{row[0]}轮 {row[1]} {row[2]}ms")
    conn.close()


if __name__ == "__main__":
    main()
