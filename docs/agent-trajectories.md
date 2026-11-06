# Agent 典型对话轨迹（提示词 7 第 4 项）

> 以下轨迹已在 `examples/agent_demo.py` 演示模式下验证通过。
> 演示模式用预设决策脚本回放轨迹；配置 LLM_API_KEY 后由真实模型 Function Calling 决策，机制完全一致。

## 轨迹 a：只查制度/学习方法，不调写入工具

**问题**："期末复习一般每天学几个小时比较合理？"

```
第1轮  query_resources(期末复习时长, 高数) → 命中制度第四章第七条
第2轮  最终回答（引用来源：制度第四章第七条）
```
- 共 2 轮，1 次只读工具调用，**不触发** check_schedule / create_plan_draft
- 落库：agent_logs 1 条；support_requests 无记录

## 轨迹 b：完整链路（查制度→查资源→查时段→校验→建草稿→确认→提交）

**问题**："帮我制定下个月的高数期末复习计划并预约辅导"

```
第1轮  search_policy(预约流程限额) ∥ query_resources(期末复习计划)   ← 一轮并行两调用
第2轮  check_schedule(未来第3天 15:00) → {valid: true}
第3轮  create_plan_draft(booking, 计划+预约信息) → request_id=1 (draft)
       ── ★强制确认中断：草稿流式发给 学生 → 确认 → student_confirmed=true ──
第4轮  submit_request(1) → status: confirmed
第5轮  最终回答（预约已生效 + 三周计划）
```
- 5 轮内完成（若模型每轮只调一个工具会撞上限触发兜底，这正是 5 轮上限的意义）
- 落库：support_requests #1 状态 `confirmed`，schedule_check 存校验结果；agent_logs 5 条

**防线路径验证**（会话上下文 `schedule_valid` 未置位时）：
`create_plan_draft` 被 GUARD_BLOCKED 拦截并回填"必须先通过 check_schedule"→ 模型下一轮转去先校验。

## 轨迹 c：解题检查类（检索 FAQ/答疑规范 → 给结论与依据）

**问题**："我这道题的解法对吗"

```
第1轮  search_policy(答疑规范 作业代做) → 命中制度第三章第五条
第2轮  query_resources(解题思路检查, 程序设计)
第3轮  最终回答：引用第五条"仅讲思路不代做"，邀请学生发解题思路并给检查清单
```
- 合规要点：不直接替代学生做作业，答复有制度依据

## 关键机制对照老师要求

| 老师要求 | 实现位置 |
|---|---|
| 模型决策→执行工具→结果回填→再决策，最多 5 轮 | `loop.py run_agent()` 的 for 循环，超限走兜底话术 |
| 每轮决策记日志（工具名/参数/耗时/结果摘要） | `AgentLogger` → agent_logs 表（已验证 8 条） |
| create_draft 前强制中断、草稿流式确认后才可执行 | 确认中断钩子 `confirm_hook`；CLI 用 input，阶段 4 换 SSE 推送+按钮 |
| 顺序约束 | 三层防线：系统提示词（防线1）+ ToolExecutor 状态机（防线2）+ 数据层 status 复查（防线3） |
