# 学习支持与辅导协作智能体系统（Learning Support Agent）

面向学生的智能学习支持问答与辅导协作系统：基于 RAG 检索学习支持制度/课程 FAQ，
支持学习方法咨询、辅导预约（含时段校验与学生确认的 Agent 工具链）、管理后台
（知识库管理 / 提示词模板 / 对话审计）。

## 架构（四层）

```
入口层    学生聊天页(SSE流式) / 管理后台(SSR) / REST API
服务层    认证(JWT) / 会话 / 知识库RAG管道 / LLM封装(供应商可换) / 审计
智能体层  5轮执行循环 + 5个Function Calling工具 + 三层顺序约束防线
数据层    SQLite(SQLAlchemy兼容) + FAISS向量索引 + BM25
```

详细设计见 `docs/design.md`（提示词1需求清单、提示词2架构方案）。

## 快速开始

```bash
# 1. 安装依赖（Python 3.11+）
pip install -r requirements.txt

# 2. 配置 LLM（可选；不配置则降级为纯检索模式，流程同样可跑通）
cp .env.example .env   # 填入 LLM_API_KEY 等

# 3. 初始化数据库与知识库（含演示账号）
python scripts/init_db.py
#    学生：student / 123456   管理员：admin / admin123

# 4. 启动
python -m uvicorn learning_support.main:app --port 8000 --app-dir src
#    学生端  http://localhost:8000/chat
#    管理端  http://localhost:8000/admin
```

## 阶段成果（对应课程 10 个提示词）

| 阶段 | 提示词 | 产出 | 位置 |
|---|---|---|---|
| 1 | 需求分析 | 一页纸需求清单（10个评审问题+答案） | docs/design.md |
| 2 | 架构选型 | 四层架构图 / 目录结构 / 表设计 | docs/design.md |
| 3 | 裸问答原型 | 单脚本 RAG 问答（切片+FAISS+BM25混合检索） | examples/bare_qa_prototype.py |
| 4 | 用户聊天界面 | POST /chat SSE 接口 + 打字机前端 | src/.../api/routes.py, web/chat.html |
| 5 | 管理后台 | 登录鉴权/知识库/提示词/审计 四页 | web/admin/, src/.../api/routes.py |
| 6 | Agent 工具设计 | 5 个 Function Calling 工具 + 三层防线 | src/.../core/agent/tools.py |
| 7 | Agent 编排 | 5轮循环/日志表/确认中断/3条轨迹 | src/.../core/agent/loop.py, docs/agent-trajectories.md |
| 8 | 项目评测 | 30 条评测用例 JSON | tests/eval_cases.json |
| 9 | 上线检查 | 25 项检查清单 | docs/launch-checklist.md |
| 10 | 项目结构推送 | 标准目录结构 + CI | 本仓库 |

## 项目结构

```
├── README.md              # 本文件
├── LICENSE                # MIT 许可证
├── .gitignore             # 依赖/密钥/缓存
├── .env.example           # 环境变量模板（真实 .env 不入库）
├── CHANGELOG.md           # 版本记录（按10个阶段）
├── requirements.txt       # Python 依赖
├── config/default.yaml    # 非机密默认配置
├── src/learning_support/  # 源码主目录
│   ├── main.py            # FastAPI 入口
│   ├── config.py          # 配置中心
│   ├── core/              # 核心：RAG/LLM/安全/Agent
│   ├── api/routes.py      # 全部 REST 接口
│   ├── services/          # 知识库服务
│   ├── models/db.py       # SQLite 表（9张）
│   └── utils/
├── web/                   # 前端（chat.html + admin/ 4页）
├── docs/                  # 设计文档/接口说明/Agent轨迹/上线清单
├── tests/                 # 评测用例 + 测试
├── examples/              # 阶段1裸问答原型 / Agent演示
├── scripts/               # init_db.py 初始化
├── data/policy/           # 示例制度文档
└── .github/workflows/     # CI 自动测试
```

## 核心设计亮点

- **LLM 供应商可替换**：OpenAI 兼容协议，改 .env 三项配置即可切换 DeepSeek/智谱等；未配置自动降级为纯检索模式
- **三层防线防跳过校验**：系统提示词约束 → 编排层状态机拦截 → 数据层兜底，保证预约必须先校验时段、草稿必须学生确认
- **提示词热更新**：系统提示词存数据库，管理员修改立即生效
- **风险审计**：心理危机/超范围/提示词注入自动标记，提示转人工

## 测试

```bash
python examples/bare_qa_prototype.py           # 阶段1交互问答
python examples/agent_demo.py                  # 阶段7三条Agent轨迹
python -m pytest tests/ -v                     # 单元测试
```
