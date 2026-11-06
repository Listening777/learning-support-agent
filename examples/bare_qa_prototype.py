# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 1 · 裸问答原型（最小可运行版本，不涉及 Web）
 项目：学习支持与辅导协作智能体系统
==============================================================================
 功能流程：
   1. 读取本地学习支持制度 markdown 文件
   2. 按 500 字切片 → 向量化 → 存入 FAISS（RAG 路线）
   3. 命令行输入问题 → BM25 + 向量混合检索 top3 → 拼入提示词 → 调用 LLM
   4. LLM 调用封装成独立函数，供应商通过 .env 可配置

 ── 后续阶段复用的核心模块（★ 标注）─────────────────────────────────────
   ★ split_text()            文本切片            → 阶段2/5 知识库摄入管道复用
   ★ ChineseEmbedder         向量化器            → 阶段2/5 向量入库复用
   ★ HybridRetriever         BM25+向量混合检索   → 阶段2 聊天检索复用
   ★ build_prompt()          RAG 提示词组装      → 阶段2/3 提示词装配复用
   ★ call_llm() / stream_llm LLM 调用封装        → 全阶段复用（供应商可换）
 ─────────────────────────────────────────────────────────────────────────
 运行方式：
   pip install faiss-cpu rank_bm25 jieba openai numpy
   python bare_qa_prototype.py            # 交互问答
   python bare_qa_prototype.py "问题"     # 单次问答
 配置：复制 .env.example 为 .env，填入 LLM_API_KEY 等
==============================================================================
"""

import os
import re
import sys
import math
import time
import json
from pathlib import Path
from collections import Counter
from math import log

# ─────────────────────────────── 配置加载 ───────────────────────────────
# 简单解析 .env 文件（避免额外依赖 python-dotenv；也可替换为 dotenv）
def load_env(env_path: str = ".env") -> None:
    """逐行读取 .env 文件，写入环境变量。已存在的环境变量不覆盖。"""
    p = Path(env_path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

load_env()

# LLM 供应商配置：全部从环境变量读取，改 .env 即可换供应商（如 DeepSeek/智谱/OpenAI）
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai_compatible")  # 目前统一走 OpenAI 兼容协议
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")  # 供应商网关地址
LLM_API_KEY = os.getenv("LLM_API_KEY", "")                              # 密钥（不要写死在代码里！）
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")                     # 模型名

# 知识库与检索参数
POLICY_FILE = os.getenv("POLICY_FILE", "data/policy/学习支持与辅导协作管理制度.md")
CHUNK_SIZE = 500        # 每个切片最大字符数（老师要求 500 字）
TOP_K = 3               # 混合检索返回条数
VECTOR_WEIGHT = 0.6     # 混合打分中向量检索的权重（BM25 占 0.4）
EMBED_DIM = 512         # 演示用嵌入维度


# ════════════════════════════════════════════════════════════════════════
# ★ 核心模块 1：文本切片（阶段 2 知识库管理页、阶段 5 文档入库直接复用）
# ════════════════════════════════════════════════════════════════════════
def split_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """
    把制度文档切成 ≤500 字的片段。
    策略：优先按标题（## 开头）分段，段内再按 chunk_size 硬切，
    保证一个切片尽量落在同一章节内，检索语义更完整。
    返回：切片文本列表。
    """
    # 先按 markdown 二级标题切出章节
    sections = re.split(r"\n(?=## )", text)
    chunks: list[str] = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        # 章节不超过 chunk_size 直接作为一片
        if len(sec) <= chunk_size:
            chunks.append(sec)
            continue
        # 超长章节：按句子边界（。！？）聚合到 chunk_size 再切
        sentences = re.split(r"(?<=[。！？])", sec)
        buf = ""
        for s in sentences:
            if len(buf) + len(s) > chunk_size and buf:
                chunks.append(buf.strip())
                buf = s
            else:
                buf += s
        if buf.strip():
            chunks.append(buf.strip())
    return chunks


# ════════════════════════════════════════════════════════════════════════
# ★ 核心模块 2：向量化器（Embedding）
#   说明：课程演示无 embedding API 时，用「字符级哈希词袋」做轻量嵌入，
#   保证 FAISS 流程完整可跑；接真实供应商时只需替换 embed() 一个方法。
# ════════════════════════════════════════════════════════════════════════
class ChineseEmbedder:
    """轻量中文嵌入器：bigram 哈希 → 归一化向量。接口与真实 embedding API 一致。"""

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim

    def _tokenize(self, text: str) -> list[str]:
        # 用 jieba 分词 + 所有二字组合（bigram），兼顾词与上下文
        import jieba
        words = [w for w in jieba.lcut(text) if w.strip()]
        bigrams = [words[i] + words[i + 1] for i in range(len(words) - 1)]
        return words + bigrams

    def embed(self, text: str):
        """文本 → dim 维归一化 numpy 向量。"""
        import numpy as np
        vec = np.zeros(self.dim, dtype="float32")
        for tok in self._tokenize(text):
            idx = hash(tok) % self.dim          # 哈希到固定维度（演示用途）
            vec[idx] += 1.0
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec

    def embed_batch(self, texts: list[str]):
        import numpy as np
        return np.stack([self.embed(t) for t in texts])


# ════════════════════════════════════════════════════════════════════════
# ★ 核心模块 3：BM25 + 向量混合检索器（阶段 2 /chat 接口直接复用）
# ════════════════════════════════════════════════════════════════════════
class HybridRetriever:
    """
    混合检索器：
      - BM25：关键词精确匹配（专有名词、数字如“45 分钟”命中强）
      - 向量：语义相似（换一种说法也能命中）
      - 融合：score = 0.6*向量分 + 0.4*BM25 归一化分，取 top3
    """

    def __init__(self, chunks: list[str]):
        from rank_bm25 import BM25Okapi
        import faiss
        self.chunks = chunks
        self.embedder = ChineseEmbedder()

        # —— BM25 侧：jieba 分词后建索引 ——
        import jieba
        self.tokenized = [jieba.lcut(c) for c in chunks]
        self.bm25 = BM25Okapi(self.tokenized)

        # —— 向量侧：切片向量化后存入 FAISS（L2 距离的扁平索引）——
        matrix = self.embedder.embed_batch(chunks)
        faiss.normalize_L2(matrix)                 # 归一化后内积≈余弦相似度
        self.index = faiss.IndexFlatIP(EMBED_DIM)  # 内积索引
        self.index.add(matrix)

    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        """返回 top_k 个 {chunk, score, bm25_score, vector_score}。"""
        import numpy as np
        import jieba

        # BM25 分数
        bm_scores = self.bm25.get_scores(jieba.lcut(query))
        # 向量分数（切片数量小，直接全量内积比较；faiss 返回 (距离, 索引)）
        qv = self.embedder.embed(query).reshape(1, -1)
        distances, indices = self.index.search(qv, len(self.chunks))
        vec_full = np.zeros(len(self.chunks), dtype="float32")
        for rank, ci in enumerate(indices[0]):
            if ci >= 0:
                vec_full[ci] = distances[0][rank]

        # 归一化到 [0,1] 再加权融合
        def norm(x):
            rng = x.max() - x.min()
            return (x - x.min()) / rng if rng > 0 else x * 0

        fused = VECTOR_WEIGHT * norm(vec_full) + (1 - VECTOR_WEIGHT) * norm(bm_scores)
        top_idx = fused.argsort()[::-1][:top_k]
        return [
            {
                "idx": int(i),   # 切片在索引中的下标（供上层对齐文档元数据）
                "chunk": self.chunks[i],
                "score": float(fused[i]),
                "bm25_score": float(bm_scores[i]),
                "vector_score": float(vec_full[i]),
            }
            for i in top_idx
        ]


# ════════════════════════════════════════════════════════════════════════
# ★ 核心模块 4：RAG 提示词组装（阶段 2/3 提示词装配复用，模板可挪进数据库）
# ════════════════════════════════════════════════════════════════════════
DEFAULT_SYSTEM_PROMPT = (
    "你是学校学习支持制度助手，只根据提供的学习支持制度条文回答，"
    "不确定时明确说“制度中未找到”，回答末尾附引用来源（文件名+条文片段），"
    "不得编造辅导流程或学习标准。遇到超出智能体服务范围的情况"
    "（如紧急心理问题），应提示学生联系学校对应的真人辅导老师。"
)


def build_prompt(question: str, hits: list[dict], source_name: str) -> list[dict]:
    """把检索到的 top3 片段拼进系统提示词，返回 OpenAI 格式 messages。"""
    context = "\n\n".join(
        f"【条文 {i + 1}】\n{h['chunk']}" for i, h in enumerate(hits)
    )
    user_content = (
        f"以下是检索到的学习支持制度条文：\n{context}\n\n"
        f"学生问题：{question}\n"
        f"请仅依据以上条文回答；若条文中没有答案，回答“制度中未找到”。"
    )
    return [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ], context, source_name


# ════════════════════════════════════════════════════════════════════════
# ★ 核心模块 5：LLM 调用封装（供应商可配置，全项目唯一对外部 LLM 的依赖点）
#   阶段 2 的 SSE 流式接口将复用 stream_llm()；阶段 3 Agent 循环复用 call_llm()
# ════════════════════════════════════════════════════════════════════════
def _get_client():
    """
    创建 OpenAI 兼容客户端。
    任何兼容 OpenAI 协议的供应商（DeepSeek/智谱/Kimi/通义等）
    只需在 .env 改 LLM_BASE_URL、LLM_MODEL、LLM_API_KEY 三项即可切换。
    """
    if not LLM_API_KEY:
        raise RuntimeError("未配置 LLM_API_KEY，请复制 .env.example 为 .env 并填写")
    from openai import OpenAI
    return OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)


def call_llm(messages: list[dict], temperature: float = 0.3) -> str:
    """同步调用 LLM，返回完整回答文本。（阶段 3 Agent 循环用这种）"""
    client = _get_client()
    resp = client.chat.completions.create(
        model=LLM_MODEL, messages=messages, temperature=temperature,
    )
    return resp.choices[0].message.content


def stream_llm(messages: list[dict], temperature: float = 0.3):
    """
    流式调用 LLM，逐段 yield 文本。（阶段 2 聊天界面的打字机效果用这种）
    """
    client = _get_client()
    stream = client.chat.completions.create(
        model=LLM_MODEL, messages=messages, temperature=temperature, stream=True,
    )
    for event in stream:
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content


# ─────────────────────────────── 主流程 ───────────────────────────────
def build_knowledge_base(policy_path: str) -> HybridRetriever:
    """
    构建知识库：读文件 → 切片 → 混合检索索引。
    阶段 2 中此函数会拆到 kb_service（上传→切片→入库→可删除），
    本阶段保持单脚本。★ 入库流程可复用
    """
    path = Path(policy_path)
    if not path.exists():
        sys.exit(f"[错误] 找不到制度文件：{policy_path}")
    text = path.read_text(encoding="utf-8")
    chunks = split_text(text)
    print(f"[知识库] 已加载 {path.name}，共 {len(chunks)} 个切片（每片≤{CHUNK_SIZE} 字）")
    return HybridRetriever(chunks), path.name


def answer(retriever: HybridRetriever, source_name: str, question: str,
           use_llm: bool = True) -> str:
    """完整 RAG 问答：检索 → 组装提示词 → 调用 LLM（未配 key 时降级为只返回检索片段）。"""
    t0 = time.time()
    hits = retriever.search(question)
    print(f"[检索] 耗时 {time.time() - t0:.3f}s，top{TOP_K} 命中：")
    for i, h in enumerate(hits):
        print(f"  {i + 1}. 融合分 {h['score']:.3f} "
              f"(BM25 {h['bm25_score']:.2f} | 向量 {h['vector_score']:.3f}) "
              f"→ {h['chunk'][:40].replace(chr(10), ' ')}…")

    messages, context, src = build_prompt(question, hits, source_name)
    if not use_llm or not LLM_API_KEY:
        print("\n[提示] 未配置 LLM_API_KEY，以下仅展示检索到的条文（配置后可获得组织好的回答）：\n")
        print(context)
        return context
    print("\n[回答]")
    out = ""
    for piece in stream_llm(messages):
        print(piece, end="", flush=True)
        out += piece
    print("\n\n[引用来源]")
    print(f"  · {src}（命中 {len(hits)} 个片段）")
    return out


def main():
    retriever, source_name = build_knowledge_base(POLICY_FILE)

    # 命令行参数模式：python bare_qa_prototype.py "问题" → 单次问答后退出
    if len(sys.argv) > 1:
        answer(retriever, source_name, " ".join(sys.argv[1:]))
        return

    # 交互模式：循环问答，输入 q 退出
    print("=" * 60)
    print("学习支持制度助手（阶段 1 裸问答原型）  输入 q 退出")
    print("=" * 60)
    while True:
        try:
            q = input("\n你的问题 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q.lower() in {"q", "quit", "exit"}:
            break
        answer(retriever, source_name, q)


if __name__ == "__main__":
    main()
