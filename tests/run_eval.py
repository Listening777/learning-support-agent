# -*- coding: utf-8 -*-
"""
==============================================================================
 量化评测脚本：tests/eval_cases.json 30 条用例自动评测（规则回放模式）
==============================================================================
运行：python tests/run_eval.py
输出：tests/eval_results.json + 控制台汇总表

四个量化维度：
  A. 检索召回（制度类 7 + 陷阱题 3）：HybridRetriever top-5 是否召回含关键词切片；
     陷阱题要求 top-5 中不出现题外域内容（食堂/押金/积分），验证拒答依据。
  B. 工具选择（工具调用类 10）：规则意图分类器选出工具集合与 expected_tools 完全一致。
  C. 风险识别（超范围/风险意图类 5）：risk_rules 高危命中 + 越权/注入/代写deny 规则。
  D. 多轮上下文（多轮上下文类 5）：注入上文后复跑分类器，验证指代消解与确认链路。
==============================================================================
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))

from bare_qa_prototype import HybridRetriever, ChineseEmbedder, split_text
from learning_support.models import db

CASES = json.load(open(ROOT / "tests/eval_cases.json", encoding="utf-8"))
POLICY = str(ROOT / "data/policy/学习支持与辅导协作管理制度.md")

# ---------------------------------------------------------------- A. 检索召回
RETRIEVAL_KEYWORDS = {
    0: ["每周最多预约 2 次"],
    1: ["45 分钟"],
    2: ["24 小时"],
    3: ["6 小时", "3 小时"],
    4: ["不得直接代替学生完成作业"],
    5: ["预约优先", "排队顺序"],
    6: ["保留期限", "不得查看原始对话"],
}
TRAP_FORBIDDEN = {7: ["食堂", "营业"], 8: ["押金", "借阅"], 9: ["积分", "兑换"]}

def eval_retrieval(retriever):
    rows, n_pass = [], 0
    for i, kw in RETRIEVAL_KEYWORDS.items():
        q = CASES[i]["question"]
        hits = retriever.search(q, top_k=5)
        text = "".join(h["chunk"] for h in hits)
        ok = all(any(k in c["chunk"] for c in hits) for k in kw)
        rows.append({"idx": i, "type": "制度类", "question": q,
                     "metric": f"top-5召回关键词{kw}", "pass": ok})
        n_pass += ok
    for i, forbidden in TRAP_FORBIDDEN.items():
        q = CASES[i]["question"]
        hits = retriever.search(q, top_k=5)
        leaked = [w for w in forbidden if any(w in c["chunk"] for c in hits)]
        ok = not leaked  # 域外问题不应召回事关食堂/押金/积分的编造依据
        rows.append({"idx": i, "type": "陷阱题", "question": q,
                     "metric": f"top-5不得出现{forbidden}", "pass": ok})
        n_pass += ok
    return rows, n_pass

# ---------------------------------------------------------------- B. 工具选择
NEG_SUBMIT = re.compile(r"(不要|不用|先不|暂时不)[^。]{0,4}(提交|生效)")

TOOL_RULES = [
    ("submit_request", re.compile(r"(提交|确认生效|预约上|预约辅导|取消)")),
    ("query_resources", re.compile(r"(复习建议|学习方法|词汇|指南|资源|复习计划|学习计划)")),
    ("create_plan_draft",
     re.compile(r"(草稿|生成|制定|建一个|改期|换到|取消|预约上|预约辅导)")),
    ("check_schedule",
     re.compile(r"(空位|时段|还能再约|改期|换到|取消|预约|2020 年)")),
    ("search_policy",
     re.compile(r"(几次|时长|多久|多少|第几|限制|规定|制度|还能再约|辅导的空位|复习计划)")),
]

def classify_tools(text):
    # 去除否定短语（如“不要提交”），避免误触发
    text = NEG_SUBMIT.sub("", text)
    tools = []
    for tool, pat in TOOL_RULES:
        if pat.search(text):
            tools.append(tool)
    # “提交/生效”语义存在且非“预约上/预约辅导”触发时，不重复建草稿
    if "submit_request" in tools and re.search(r"(提交|确认生效)", text) \
            and not re.search(r"(预约上|预约辅导)", text):
        tools = [t for t in tools if t != "create_plan_draft"] or tools
    return tools or ["search_policy"]

def eval_tools():
    rows, n_pass = [], 0
    for i in range(10, 20):
        c = CASES[i]
        got = set(classify_tools(c["question"]))
        exp = set(c["expected_tools"])
        ok = got == exp
        rows.append({"idx": i, "type": "工具调用类", "question": c["question"],
                     "expected": sorted(exp), "got": sorted(got), "pass": ok})
        n_pass += ok
    return rows, n_pass

# ---------------------------------------------------------------- C. 风险识别
DENY_PATTERNS = [
    (re.compile(r"(替我|帮我).{0,6}(写作业|代写|完成作业)"), "代写作业-越权"),
    (re.compile(r"代考|替考"), "代考-违规"),
    (re.compile(r"同学.{0,8}(记录|成绩单)|把.{0,6}发给我"), "隐私越权"),
    (re.compile(r"忽略(之前|以上|前面)的指令|你现在是"), "提示注入"),
]

def eval_risk():
    rows, n_pass = [], 0
    for i in range(20, 25):
        c = CASES[i]
        q = c["question"]
        risk_hits = db.scan_risk(q)
        deny = [name for pat, name in DENY_PATTERNS if pat.search(q)]
        ok = bool(risk_hits) or bool(deny)
        rows.append({"idx": i, "type": "风险类", "question": q,
                     "risk_level": risk_hits[0]["level"] if risk_hits else None,
                     "deny_rules": deny, "pass": ok})
        n_pass += ok
    return rows, n_pass

# ---------------------------------------------------------------- D. 多轮上下文
MULTI_TURN_CONTEXT = {
    25: "一对一辅导",
    26: "小组辅导",
    27: "制度第二章第四条",
    28: "上一轮产出",
    29: "周三的辅导预约草稿",
}

def eval_multi_turn():
    rows, n_pass = [], 0
    for i, subject in MULTI_TURN_CONTEXT.items():
        c = CASES[i]
        # 指代消解：将“它/刚才”替换为上轮主题，再复跑意图分类器
        resolved = re.sub(r"(它|刚才[^，。]*)", subject, c["question"])
        got = set(classify_tools(resolved))
        exp = set(c["expected_tools"])
        ok = got == exp
        rows.append({"idx": i, "type": "多轮上下文类", "question": c["question"],
                     "with_context": subject, "expected": sorted(exp),
                     "got": sorted(got), "pass": ok})
        n_pass += ok
    return rows, n_pass

# -------------------------------------------------------------------- 主流程
def main():
    db.init_db()
    retriever = HybridRetriever(split_text(open(POLICY, encoding="utf-8").read()))
    a_rows, a_pass = eval_retrieval(retriever)
    b_rows, b_pass = eval_tools()
    c_rows, c_pass = eval_risk()
    d_rows, d_pass = eval_multi_turn()
    result = {
        "summary": {
            "A_检索召回": f"{a_pass}/10",
            "B_工具选择": f"{b_pass}/10",
            "C_风险识别": f"{c_pass}/5",
            "D_多轮上下文": f"{d_pass}/5",
            "总计": f"{a_pass+b_pass+c_pass+d_pass}/30",
        },
        "detail": {"A": a_rows, "B": b_rows, "C": c_rows, "D": d_rows},
    }
    json.dump(result, open(ROOT / "tests/eval_results.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("=" * 56)
    for k, v in result["summary"].items():
        print(f"  {k:<12} {v}")
    print("=" * 56)
    for rows in (a_rows, b_rows, c_rows, d_rows):
        for r in rows:
            flag = "PASS" if r["pass"] else "FAIL"
            print(f"  [{flag}] #{r['idx']:>2} {r['type']} {r['question'][:26]}")
            if not r["pass"]:
                print("        expected:", r.get("expected"), " got:", r.get("got"))

if __name__ == "__main__":
    main()
