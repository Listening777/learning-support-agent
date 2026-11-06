# -*- coding: utf-8 -*-
"""
知识库服务（提示词5）：文档上传 → 解析 → 500字切片 → 向量化入库 → 可删除。
复用阶段1的 ★split_text / ★ChineseEmbedder / ★HybridRetriever。
FAISS 索引按文档集合整体重建（演示规模下简单可靠）。
"""
import json
import re
from datetime import datetime
from ..models import db
from .. import config

# 进程内检索器缓存：{("kb",): HybridRetriever}
_retriever = None


def _extract_text(filename: str, raw: bytes) -> str:
    """按文件类型提取文本：md/txt 直接解码；pdf 用 pdfplumber。"""
    if filename.lower().endswith((".md", ".txt", ".markdown")):
        return raw.decode("utf-8", errors="ignore")
    if filename.lower().endswith(".pdf"):
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    raise ValueError("不支持的文件类型")


def _load_all_chunks() -> list[dict]:
    """从 doc_chunks 表读出全部切片（含文档名），供重建索引。"""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT c.id, c.content, d.filename FROM doc_chunks c "
        "JOIN policy_documents d ON c.document_id=d.id "
        "WHERE d.status='active'").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def rebuild_index() -> int:
    """用当前 active 文档的切片重建 FAISS+BM25 索引。返回切片数。"""
    global _retriever
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "examples"))
    from bare_qa_prototype import HybridRetriever

    chunks = _load_all_chunks()
    if chunks:
        _retriever = (HybridRetriever([c["content"] for c in chunks]),
                      {i: c for i, c in enumerate(chunks)})
    else:
        _retriever = None
    return len(chunks)


def get_retriever():
    """取当前检索器；为空时尝试重建一次。"""
    global _retriever
    if _retriever is None:
        rebuild_index()
    return _retriever


def upload_document(filename: str, raw: bytes, doc_type: str,
                    uploader_id: int) -> dict:
    """上传文档全流程：解析→切片→入库→重建索引。失败抛异常由路由转错误码。"""
    text = _extract_text(filename, raw)
    if len(text.strip()) < 10:
        raise ValueError("文档内容为空或过短")
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "examples"))
    from bare_qa_prototype import split_text

    chunks = split_text(text, config.CHUNK_SIZE)
    conn = db.get_conn()
    now = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO policy_documents(filename,doc_type,status,chunk_count,"
        "uploaded_by,created_at) VALUES(?,?,'active',?,?,?)",
        (filename, doc_type, len(chunks), uploader_id, now))
    doc_id = cur.lastrowid
    for i, chunk in enumerate(chunks):
        conn.execute(
            "INSERT INTO doc_chunks(document_id,chunk_index,content,embedding_id,"
            "created_at) VALUES(?,?,?,?,?)",
            (doc_id, i, chunk, i, now))
    conn.commit(); conn.close()
    rebuild_index()
    return {"document_id": doc_id, "chunk_count": len(chunks)}


def delete_document(doc_id: int) -> bool:
    """软删文档 + 删除切片 + 重建索引（列表可删除需求）。"""
    conn = db.get_conn()
    n = conn.execute("UPDATE policy_documents SET status='deleted' "
                     "WHERE id=?", (doc_id,)).rowcount
    conn.execute("DELETE FROM doc_chunks WHERE document_id=?", (doc_id,))
    conn.commit(); conn.close()
    if n:
        rebuild_index()
    return bool(n)


def hybrid_search(query: str, top_k: int = config.TOP_K) -> list[dict]:
    """对外检索接口：返回 [{filename, snippet, score}]。"""
    cached = get_retriever()
    if not cached:
        return []
    retriever, meta = cached
    hits = retriever.search(query, top_k)
    return [{"filename": meta[h["idx"]]["filename"],
             "snippet": h["chunk"], "score": h["score"]} for h in hits]
