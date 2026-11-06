# -*- coding: utf-8 -*-
"""配置中心：集中读取环境变量，全项目唯一配置入口。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# .env 加载（不覆盖已有环境变量）
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# LLM 供应商配置（改 .env 即可换供应商）
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# 存储路径
DB_PATH = str(ROOT / "data" / "app.db")
POLICY_DIR = ROOT / "data" / "policy"
FAISS_DIR = ROOT / "data" / "faiss"

# JWT 配置（生产环境必须改 SECRET）
JWT_SECRET = os.getenv("JWT_SECRET", "course-demo-secret-change-me")
JWT_EXPIRE_HOURS = 24

# 业务参数
CHAT_HISTORY_ROUNDS = 6       # 每次请求携带最近 6 轮历史
CHUNK_SIZE = 500              # 制度文档切片大小
TOP_K = 3                     # 混合检索条数
MAX_UPLOAD_MB = 5             # 知识库上传大小限制
RATE_LIMIT_PER_MIN = 6        # 单用户每分钟问答次数（提示词9限流项）

# 默认系统提示词（提示词5老师给的模板；仅初始化与 reset 时使用）
DEFAULT_SYSTEM_PROMPT = (
    "你是学校学习支持制度助手，只根据提供的学习支持制度条文和学习资源回答，"
    "不确定时明确说“制度中未找到”，回答末尾附引用来源，不得编造辅导流程或学习标准。"
    "遇到超出智能体服务范围的情况（如紧急心理问题），应提示学生联系学校对应的真人辅导老师。"
)

# 系统强制追加段（防线1，管理员不可编辑）
SYSTEM_GUARD_PROMPT = """

【工具调用规则（系统强制，不可违反）】
1. 涉及制度、预约规则的问题，必须先调用 search_policy 查证；
2. 学习方法问题先调用 query_resources；
3. 预约类请求必须先调用 check_schedule：校验失败时只能告知学生原因，严禁建草稿；
4. 草稿必须等学生明确确认后才能 submit_request；
5. 不得虚构教师时段、限额数字，一律以工具返回结果为准。"""

# 心理危机/超范围风险规则（初始化写入 risk_rules 表，管理员可增删）
DEFAULT_RISK_RULES = [
    ("不想活|轻生|自杀|活着没.{0,2}意思", "high", "疑似心理危机，提示转人工/心理中心"),
    ("抑郁|睡不着|情绪崩溃|不想学了|不想上学|压力太大", "high", "疑似情绪困扰，提示联系心理中心"),
    ("替.{0,3}写作业|代写|代考|代.{0,2}考试", "medium", "超范围违规请求，给合规指引"),
    ("别人的|同学的.{0,4}(成绩|记录|对话)|隐私|成绩单发给我", "medium", "疑似索取他人隐私，拒绝并说明制度"),
    ("忽略之前的指令|无视.{0,3}规则|你是通用助手", "medium", "疑似提示词注入，坚持系统约束"),
]
