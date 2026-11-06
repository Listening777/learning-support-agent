# src 目录说明：MVP 工程架构与人工确认点

> 项目：北辰在线学院「学习支持与辅导协作智能体系统」（MVP）
> 本说明对应交付要求：① 问答助手归档 ② MVP 项目（工程架构 / Agent 框架 / 知识库 / 至少 3 处人工确认或审核点）

## 1. 问答助手归档（阶段 3 成果）

裸问答原型已归档至 [`examples/bare_qa_prototype.py`](../examples/bare_qa_prototype.py)，单文件包含：
`split_text` 切片（≤500 字）→ `ChineseEmbedder`（jieba bigram hash，归一化）→ `HybridRetriever`（FAISS 向量 0.6 + BM25 0.4 混合检索）→ LLM 流式回答（未配置 KEY 时回放检索原文）。
对应单元测试：`tests/test_core.py::test_split_text_*`、`test_embedder_normalized`。

## 2. MVP 工程架构

```
┌────────────── 表现层 ──────────────┐
│ web/chat.html（SSE 聊天）           │
│ web/admin/{dashboard,kb,prompt,audit}.html │
├────────────── 接口层 ──────────────┤
│ api/routes.py：/api/auth/login、/api/chat(SSE)、
│ /api/admin/{kb,prompt,audit,stats}，统一错误码
│ JWT 鉴权 + 角色守卫（student/admin）
├────────────── 业务层 ──────────────┤
│ core/agent/loop.py   Agent 五轮循环（Function Calling）
│ core/agent/tools.py  5 个工具执行器
│ core/security.py     PBKDF2 密码哈希 + HS256 JWT
│ core/llm_client.py   LLM 调用（流式/非流式，mock 回退）
│ services/kb_service.py 知识库切片/索引/重建
├────────────── 数据层 ──────────────┤
│ models/db.py  SQLite 9 张表：users / sessions / messages /
│   documents / chunks / support_requests / agent_logs /
│   system_prompts / risk_rules（幂等 init_db）
└───────────────────────────────────┘
```

## 3. Agent 框架

- **五轮循环**（`core/agent/loop.py`）：每轮 LLM 决策 → 工具执行 → 结果回填，最多 5 轮；日志逐轮写入 `agent_logs` 表可审计。
- **5 个工具**（`core/agent/tools.py`）：`search_policy`（制度检索）/ `query_resources`（学习方法资源）/ `check_schedule`（时段校验）/ `create_plan_draft`（生成草稿）/ `submit_request`（提交生效）。
- **顺序约束防线**：`create_plan_draft` 未执行时调用 `submit_request` → 返回 `GUARD_BLOCKED`（三层防线第二层）。
- **确认中断**：草稿生成后状态置 `draft`，学生下一轮回复「确认」才真正提交（`routes.py` 多轮确认分支 + `submit_request` 二次校验 `student_confirmed`）。

## 4. 知识库

- 语料：`data/policy/学习支持与辅导协作管理制度.md`（12 条制度 + 学习标准）。
- 切片与索引：`services/kb_service.py`，上传即切片（≤500 字）、建 FAISS + BM25 双索引（启动时 `rebuild_index()` 加载）。
- 管理端支持上传 / 删除 / 列表（`/api/admin/kb/*`），引用来源随回答以 `citations` 事件推送。

## 5. 人工确认 / 审核点（≥3 处，MVP 硬性要求）

| # | 确认点 | 位置（代码锚点） | 机制 |
|---|---|---|---|
| 1 | **学生确认预约/计划草稿** | `routes.py` chat 多轮确认分支；`tools.py::submit_request` 校验 `student_confirmed`；`support_requests.status: draft→confirmed` | 草稿先流式展示给学生，学生回复「确认」才提交，绝不静默生效 |
| 2 | **心理危机转人工** | `routes.py:226` `db.scan_risk(question)` 命中 high → 强制追加「联系心理健康教育中心（010-12345678）/真人辅导老师」提示，禁止 AI 自行疏导；`audit.html` 按风险等级红/黄标 | 数据层兜底防线，高危必转人工 |
| 3 | **越权/代写/注入 GUARD 拦截** | `loop.py:152` 工具结果 `GUARD_BLOCKED`；系统提示词约束（`config.SYSTEM_GUARD_PROMPT`）；答疑「仅讲思路，不得代写作业」（制度第五条） | 三层防线：提示词 → 编排层状态机 → 数据层 |
| 4 | **管理员审核提示词热更新** | `web/admin/prompt.html` → `POST /api/admin/prompt`，写入 `system_prompts` 表并记录 `updated_by`，可一键 `reset` 回默认 | 提示词变更留痕、可回滚 |
| 5 | **管理员审计风控标记** | `web/admin/audit.html` + `routes.py:433` `db.scan_risk` 复扫历史消息，红/黄风险标记 | 人工复核风险对话 |

## 6. 运行方式

```bash
pip install fastapi uvicorn jieba numpy faiss-cpu
python -m uvicorn learning_support.main:app --port 8000   # 在 src/ 下
# 学生端 http://127.0.0.1:8000/chat   管理端 /admin（admin/admin123）
```

单元测试 `python -m pytest tests/ -q`（5 passed）；量化评测 `python tests/run_eval.py`（30/30，详见 `tests/eval_results.json` 与 `docs/02-测试交付/`）。
