# -*- coding: utf-8 -*-
"""
初始化脚本：建库建表 + 创建演示账号 + 导入示例制度文档。
运行：python scripts/init_db.py
演示账号：admin/admin123（管理员）、student/123456（学生）
"""
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from learning_support.models import db
from learning_support.core.security import hash_password
from learning_support import config


def main():
    db.init_db()
    conn = db.get_conn()
    # 学生演示账号
    if not conn.execute("SELECT 1 FROM users WHERE username='student'").fetchone():
        conn.execute("INSERT INTO users(username,password_hash,role,real_name,created_at)"
                     " VALUES('student',?,'student','张小明',?)",
                     (hash_password("123456"), datetime.now().isoformat()))
        print("[初始化] 学生演示账号：student / 123456")
    conn.commit(); conn.close()

    # 导入示例制度文档（知识库入库）
    from learning_support.services import kb_service
    policy = config.POLICY_DIR / "学习支持与辅导协作管理制度.md"
    if policy.exists():
        conn = sqlite3.connect(config.DB_PATH)
        n = conn.execute("SELECT COUNT(*) FROM policy_documents WHERE filename=?",
                         (policy.name,)).fetchone()[0]
        conn.close()
        if n == 0:
            result = kb_service.upload_document(
                policy.name, policy.read_bytes(), "policy", 1)
            print(f"[初始化] 制度文档已入库，切片 {result['chunk_count']} 片")
        else:
            print("[初始化] 制度文档已存在，跳过")
    kb_service.rebuild_index()
    print("[完成] 数据库与知识库初始化完毕")


if __name__ == "__main__":
    main()
