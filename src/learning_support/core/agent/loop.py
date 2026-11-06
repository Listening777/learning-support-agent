# -*- coding: utf-8 -*-
"""
==============================================================================
 提示词 7 · Agent 执行循环（轻量 Harness）
==============================================================================
 框架选型：自研轻量 Harness（约 150 行），不引入 LangChain/LlamaIndex 等
 重型框架。理由：
   1. 循环逻辑简单（决策→执行→回填→再决策），自研代码透明、便于教学展示；
   2. "确认中断"需要与 SSE 流交互，自研更容易精确控制；
   3. LLM 调用层复用阶段 1 的 openai 兼容封装，框架只管编排。
 模式说明：
   - real 模式：配置了 LLM_API_KEY，真实 Function Calling 决策；
   - mock 模式：未配 key 时用预设决策脚本回放 3 个典型轨迹，
     用于验证循环/防线/中断/日志机制本身。

 ★ run_agent() 为核心入口，阶段 4 的 /chat 接口直接复用
==============================================================================
"""
import json
import time
import sqlite3
import re
from datetime import datetime

# 系统提示词（防线 1）：工具调用规则段，约束模型不得跳过校验
SYSTEM_PROMPT = """你是学校学习支持制度助手，只根据提供的学习支持制度条文和学习资源回答，
不确定时明确说"制度中未找到"，回答末尾附引用来源，不得编造辅导流程或学习标准。

【工具调用规则（系统强制，不可违反）】
1. 涉及制度、预约规则的问题，必须先调用 search_policy 查证；
2. 学习方法问题先调用 query_resources；
3. 预约类请求必须先调用 check_schedule：校验失败（valid=false）时只能告知
   学生原因，严禁调用 create_plan_draft；
4. create_plan_draft 生成草稿后必须等待学生明确确认（如"确认"），
   学生未确认时严禁调用 submit_request；
5. 不得虚构教师时段、限额数字，一律以工具返回结果为准。"""

MAX_ROUNDS = 5   # 老师要求：最多 5 轮决策循环


# ════════════════════ 每轮日志（阶段 5 评测分析的数据源） ════════════════════
class AgentLogger:
    """把每轮工具调用写入 agent_logs 表：工具名/参数/耗时/结果摘要。"""

    def __init__(self, db_path: str, session_id: str):
        self.db_path = db_path
        self.session_id = session_id
        self.records = []

    def log(self, turn: int, tool: str, args: dict, duration_ms: int,
            result: str):
        summary = result[:200].replace("\n", " ")   # 结果摘要截断 200 字
        rec = {"turn": turn, "tool": tool, "args": args,
               "duration_ms": duration_ms, "result_summary": summary}
        self.records.append(rec)
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO agent_logs(session_id, turn, tool_name, arguments,"
                " duration_ms, result_summary, created_at) VALUES(?,?,?,?,?,?,?)",
                (self.session_id, turn, tool, json.dumps(args, ensure_ascii=False),
                 duration_ms, summary, datetime.now().isoformat()))
            conn.commit(); conn.close()
        except sqlite3.OperationalError:
            pass   # CLI 演示无库时忽略


# ════════════════════ LLM 决策层（real / mock 双模式） ════════════════════
class DecisionEngine:
    """一轮决策：输入对话 messages，输出 (文字回复 | 工具调用列表)。"""

    def __init__(self, mock_script: list | None = None):
        self.mock_script = mock_script or []   # mock 模式的预设决策队列
        self.mock_idx = 0

    def decide(self, messages: list[dict], tools: list[dict], api_cfg: dict):
        if api_cfg.get("api_key"):
            return self._real_decide(messages, tools, api_cfg)
        return self._mock_decide()

    def _real_decide(self, messages, tools, cfg):
        """真实模式：OpenAI 兼容协议 Function Calling。"""
        from openai import OpenAI
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        resp = client.chat.completions.create(
            model=cfg["model"], messages=messages, tools=tools, temperature=0.2)
        msg = resp.choices[0].message
        if msg.tool_calls:
            return [{"name": tc.function.name,
                     "args": json.loads(tc.function.arguments or "{}")}
                    for tc in msg.tool_calls]
        return msg.content

    def _mock_decide(self):
        """演示模式：按预设脚本依次弹出决策，用于回放典型轨迹。"""
        if self.mock_idx < len(self.mock_script):
            item = self.mock_script[self.mock_idx]
            self.mock_idx += 1
            # 统一返回格式：工具调用包成列表，纯文字直接返回
            return [item] if isinstance(item, dict) else item
        return "（演示模式：预设脚本已用完）"


# ════════════════════ 核心：Agent 执行循环 ════════════════════
def run_agent(question: str, history: list[dict], executor,
              session_ctx: dict, db_path: str, session_id: str = "cli",
              api_cfg: dict | None = None, confirm_hook=None,
              mock_script: list | None = None, verbose: bool = True):
    """
    执行 Agent 循环：模型决策 → 执行工具 → 结果回填 → 再决策，最多 5 轮。

    参数：
      question      学生本次问题
      history       最近 6 轮历史（阶段 4 会传真实会话历史）
      executor      ToolExecutor 实例
      session_ctx   会话级状态机（schedule_valid / student_confirmed 等）
      confirm_hook  确认中断回调：产生草稿时调用，签名为
                    hook(draft_text) -> bool（True=学生已确认）
      mock_script   演示模式的预设决策脚本
    返回：{answer, logs, status}
    """
    api_cfg = api_cfg or {"api_key": "", "base_url": "", "model": ""}
    logger = AgentLogger(db_path, session_id)
    engine = DecisionEngine(mock_script)

    # 组装消息：系统提示词（防线 1）+ 历史 + 当前问题
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history
    messages.append({"role": "user", "content": question})

    status = "done"
    for turn in range(1, MAX_ROUNDS + 1):
        # —— 1. 模型决策 ——
        decision = engine.decide(messages, None, api_cfg)

        # 决策是文字 → 循环结束
        if isinstance(decision, str):
            if verbose:
                print(f"\n[第{turn}轮·最终回答] {decision}")
            return {"answer": decision, "logs": logger.records, "status": status}

        # 决策是工具调用列表 → 逐个执行
        tool_results = []
        for call in decision:
            name, args = call["name"], call.get("args", {})

            # —— 2a. 确认中断检查：学生确认动作才是 submit 的唯一触发源 ——
            if name == "submit_request" and not session_ctx.get("student_confirmed"):
                if verbose:
                    print("\n[中断] submit_request 被防线拦截：学生尚未确认草稿")
                tool_results.append({"name": name,
                                     "result": json.dumps({"error": "GUARD_BLOCKED",
                                     "message": "草稿尚未经学生确认，无法提交"},
                                     ensure_ascii=False)})
                continue

            # —— 2b. 执行工具（防线 2 状态机校验在 ToolExecutor 内部） ——
            t0 = time.time()
            result = executor.execute(name, args, session_ctx)
            dur = int((time.time() - t0) * 1000)
            logger.log(turn, name, args, dur, result)
            if verbose:
                print(f"[第{turn}轮·工具] {name}({json.dumps(args, ensure_ascii=False)})"
                      f" 耗时{dur}ms → {result[:80]}…")
            tool_results.append({"name": name, "result": result})

            # —— 2c. 强制中断：产生草稿后暂停循环，流式发给学生确认 ——
            if name == "create_plan_draft":
                draft_id = json.loads(result).get("request_id")
                if draft_id and confirm_hook:
                    if verbose:
                        print("\n[确认中断] 草稿内容（流式发送给学生）：")
                    draft_text = json.dumps(args["content"], ensure_ascii=False,
                                            indent=2)
                    # 阶段 4 中 confirm_hook 是 SSE 推送草稿+等待按钮回调；
                    # CLI 演示中是 input() 交互
                    confirmed = confirm_hook(draft_text)
                    session_ctx["student_confirmed"] = bool(confirmed)
                    if verbose:
                        print(f"[确认中断] 学生{'已确认' if confirmed else '拒绝'}")
                    if not confirmed:
                        status = "cancelled"

        # —— 3. 结果回填 → 再决策（工具结果作为 user 消息拼回上下文） ——
        for tr in tool_results:
            messages.append({"role": "user",
                             "content": f"[工具 {tr['name']} 返回]\n{tr['result']}"})

    # 5 轮用完仍未得出最终回答 → 兜底
    fallback = "抱歉，这个问题我处理了几轮仍未完成，请换个说法重试或联系辅导老师。"
    if verbose:
        print(f"\n[达到5轮上限·兜底] {fallback}")
    return {"answer": fallback, "logs": logger.records, "status": "max_rounds"}
