# -*- coding: utf-8 -*-
"""
数据库层：SQLite 建表 + 连接工具。
表：users / sessions / messages / policy_documents / doc_chunks /
    support_requests / agent_logs / system_prompts / risk_rules
"""
import sqlite3
from datetime import datetime
from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE, password_hash TEXT, role TEXT, real_name TEXT,
    created_at TEXT);
CREATE TABLE IF NOT EXISTS sessions(
    id TEXT PRIMARY KEY, user_id INTEGER, title TEXT,
    created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT, role TEXT, content TEXT, citations TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS policy_documents(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT, doc_type TEXT, status TEXT, chunk_count INTEGER,
    uploaded_by INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS doc_chunks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER, chunk_index INTEGER, content TEXT,
    embedding_id INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS support_requests(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT, user_id INTEGER, request_type TEXT, draft_content TEXT,
    status TEXT, schedule_check TEXT, created_at TEXT, confirmed_at TEXT);
CREATE TABLE IF NOT EXISTS agent_logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT, turn INTEGER, tool_name TEXT, arguments TEXT,
    duration_ms INTEGER, result_summary TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS system_prompts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT, is_default INTEGER, updated_by INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS risk_rules(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT, level TEXT, note TEXT, enabled INTEGER DEFAULT 1);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(admin_password: str = "admin123") -> None:
    """建表 + 初始化管理员账号、默认系统提示词、风险规则。幂等。"""
    conn = get_conn()
    conn.executescript(SCHEMA)
    now = datetime.now().isoformat()
    # 管理员账号（密码 PBKDF2 哈希，不存明文）
    from ..core.security import hash_password
    if not conn.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        conn.execute("INSERT INTO users(username,password_hash,role,real_name,created_at)"
                     " VALUES('admin',?,'admin','教务管理员',?)",
                     (hash_password(admin_password), now))
    # 默认系统提示词（存数据库，不写死在代码里）
    if not conn.execute("SELECT 1 FROM system_prompts").fetchone():
        conn.execute("INSERT INTO system_prompts(content,is_default,updated_by,created_at)"
                     " VALUES(?,1,1,?)", (config.DEFAULT_SYSTEM_PROMPT, now))
    # 风险规则
    if not conn.execute("SELECT 1 FROM risk_rules").fetchone():
        for pattern, level, note in config.DEFAULT_RISK_RULES:
            conn.execute("INSERT INTO risk_rules(pattern,level,note,enabled)"
                         " VALUES(?,?,?,1)", (pattern, level, note))
    conn.commit(); conn.close()


def get_system_prompt() -> str:
    """读当前生效的系统提示词（最新版本），无记录时用内置默认兜底。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT content FROM system_prompts ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row["content"] if row else config.DEFAULT_SYSTEM_PROMPT


def get_risk_rules() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT pattern, level, note FROM risk_rules"
                        " WHERE enabled=1").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def scan_risk(text: str) -> list[dict]:
    """对话风险扫描：返回命中的规则（审计页标红 + 触发兜底话术）。"""
    import re
    hits = []
    for rule in get_risk_rules():
        if re.search(rule["pattern"], text, re.I):
            hits.append(rule)
    return hits
