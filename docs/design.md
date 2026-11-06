# 设计文档

## 一、需求清单（提示词 1 产出，一页纸）

**项目**：学习支持与辅导协作智能体系统
**目标用户**：学生（学习者）、管理员（辅导教师/教务管理员）
**主线流程**：学生问答 → 制度检索 → 辅导预约（校验+确认） / 管理员维护知识库与审计

### 10 个需求评审问题与结论

| # | 问题 | 结论 | 架构影响 |
|---|---|---|---|
| 1 | 辅导场景类型 | 课程答疑+学习方法为主，心理类仅转人工兜底 | 无需长周期学生画像，安全设计集中在风险识别 |
| 2 | 知识库来源 | 校内制度文件（md/pdf）+ 课程 FAQ | 需要文档解析管道（md直读/pdf提取） |
| 3 | 知识库更新频率 | 管理员按需更新 | 必须有知识库管理页（上传→切片→入库→删除） |
| 4 | 预约方式 | 系统内完成（查时段→校验→确认→生效） | 问答型升级为事务型 Agent，需工具链与确认中断 |
| 5 | 教师日程来源 | 管理员手动录入 | 只需 teacher_slots 数据，不做外部集成 |
| 6 | 对话隐私 | 管理员可审计但只看统计+风险标记片段 | 审计页不展示原文，符合制度第九条 |
| 7 | 用户规模 | 演示级 <50 人 | SQLite + 单进程 FastAPI 足够，ORM 预留迁移 |
| 8 | LLM 供应商 | 可替换（OpenAI 兼容协议） | 统一 LLMClient 封装，.env 三项配置切换 |
| 9 | 部署方案 | 演示环境本地运行 | 无需 Docker/HTTPS，预留 scripts/deploy |
| 10 | 与校内系统集成 | 暂不集成，预留 JWT/API 扩展 | 用户体系自含，接口标准化 |

**验收要点**：制度外问题答"未找到"；预约必须先校验时段且学生确认；心理危机转人工。

## 二、技术架构（提示词 2 产出）

### 四层架构

```
入口层    学生聊天页(chat.html, SSE流式) / 管理后台(admin/, SSR) / REST API
服务层    认证(JWT) / 会话 / 知识库RAG管道 / LLM封装(可替换) / 审计
智能体层  5轮执行循环 / 5个工具(Function Calling) / 三层顺序约束防线 / 日志
数据层    SQLite(9张表) / FAISS向量索引 / BM25
```

### 数据库表（9 张）

users、sessions、messages、policy_documents、doc_chunks、
support_requests（草稿→确认）、agent_logs（工具轨迹）、system_prompts（热更新）、
risk_rules（风险规则）。字段详见 `src/learning_support/models/db.py`。

### 三层防线（Agent 顺序约束）

1. 系统提示词约束（防线1，引导模型）
2. 编排层状态机拦截（防线2，代码强制：create_plan_draft 前检查 check_schedule 通过）
3. 数据层兜底（防线3，submit 前复查草稿状态与确认标记）

## 三、接口说明（api.md）

| 接口 | 方法 | 角色 | 说明 |
|---|---|---|---|
| /api/auth/login | POST | 公开 | 登录，返回 JWT |
| /api/auth/me | GET | 登录 | 当前用户信息 |
| /api/chat | POST(SSE) | 学生 | 问答：session→status→delta→citations→done 事件流 |
| /api/sessions | GET | 学生 | 会话列表 |
| /api/sessions/{id}/messages | GET | 学生 | 历史消息回显 |
| /api/admin/stats | GET | 管理员 | 仪表盘统计 |
| /api/admin/kb/upload | POST | 管理员 | 文档上传入库（md/pdf→切片→向量化） |
| /api/admin/kb/list | GET | 管理员 | 文档列表 |
| /api/admin/kb/{id} | DELETE | 管理员 | 删除文档并重建索引 |
| /api/admin/prompt | GET/PUT | 管理员 | 读取/更新系统提示词（存库即生效） |
| /api/admin/prompt/reset | POST | 管理员 | 恢复默认模板 |
| /api/admin/audit | GET | 管理员 | 对话审计（学生/时间筛选+风险标记） |
| /api/health | GET | 公开 | 健康检查 |

错误码：0 成功 / 4001 参数 / 4003 无权 / 4011 未登录 / 4012 需管理员 /
4013 学生不可访问 / 4004 文件不支持 / 4293 限流 / 5001 LLM失败 / 5002 检索异常 / 5003 解析失败。
