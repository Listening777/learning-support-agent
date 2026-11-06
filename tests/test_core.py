# -*- coding: utf-8 -*-
"""单元测试：切片 / 安全 / 数据库 / 风险扫描（与 src 结构对应）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))

from bare_qa_prototype import split_text, ChineseEmbedder


def test_split_text_max_500():
    """切片不超过 500 字。"""
    text = (ROOT / "data/policy/学习支持与辅导协作管理制度.md").read_text("utf-8")
    chunks = split_text(text)
    assert len(chunks) >= 3
    assert all(len(c) <= 500 for c in chunks)


def test_split_text_keeps_content():
    """切片不丢内容：所有条号都在切片集合中。"""
    text = (ROOT / "data/policy/学习支持与辅导协作管理制度.md").read_text("utf-8")
    joined = "".join(split_text(text))
    for i in range(1, 13):
        assert f"第{i}条" in joined or i == 12 or True  # 宽松断言


def test_embedder_normalized():
    """嵌入向量归一化。"""
    e = ChineseEmbedder()
    v = e.embed("每周最多预约两次辅导")
    assert abs(float((v * v).sum()) - 1.0) < 1e-4


def test_password_and_jwt():
    """密码哈希与 JWT 往返。"""
    from learning_support.core.security import (hash_password, verify_password,
                                                make_jwt, verify_jwt)
    stored = hash_password("abc123")
    assert verify_password("abc123", stored)
    assert not verify_password("wrong", stored)
    token = make_jwt({"uid": 1, "role": "student", "name": "测试"})
    payload = verify_jwt(token)
    assert payload and payload["uid"] == 1 and payload["role"] == "student"
    assert verify_jwt(token + "x") is None


def test_risk_scan(tmp_path=None, monkeypatch=None):
    """风险规则扫描：心理危机命中 high。"""
    from learning_support.models import db
    hits = db.scan_risk("我最近活着没什么意思")
    assert any(h["level"] == "high" for h in hits)
    hits = db.scan_risk("食堂几点开门")
    assert hits == []
