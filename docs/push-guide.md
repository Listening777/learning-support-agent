# 推送到 GitHub 操作指引

本地仓库已按阶段完成 7 个 commit。按以下步骤推到你自己的 GitHub 仓库
（建议推到新仓库，不要直接覆盖旧的 learning-support-agent，保留旧版对比）。

## 方式一：GitHub 网页新建 + 命令行推送（推荐）

1. 打开 https://github.com/new ，仓库名填 `learning-support-agent`（Public），
   **不要**勾选 README / .gitignore / LICENSE 初始化。
2. 在本地项目目录执行：

```bash
cd learning-support-agent

# 关联远程（换成你的用户名）
git remote add origin https://github.com/Listening777/learning-support-agent.git

# 推送主分支并打阶段标签
git branch -M main
git push -u origin main

# 打阶段标签（可选，体现10阶段推进，老师评分加分项）
git tag v0.3.0 -m "阶段3：裸问答原型" && git push origin v0.3.0
git tag v0.7.0 -m "阶段7：Agent编排" && git push origin v0.7.0
git tag v1.0.0 -m "全部10阶段完成" && git push origin v1.0.0
```

## 方式二：直接同步到已有仓库（gitee 同步的）

你旧仓库与 gitee 双向同步。如果想保留旧仓库直接替换内容：

```bash
git remote add github https://github.com/Listening777/learning-support-agent.git
git push github master:main --force   # 强制覆盖前建议先在网页备份旧分支
```

## 推送后自查清单

- [ ] 根目录整洁：无散落的 png/csv/docx（旧报告如需保留放 docs/archive/）
- [ ] GitHub 页面显示 README 渲染正常（架构+快速开始+阶段表）
- [ ] .env 未被提交（检查仓库内无 .env 文件，只有 .env.example）
- [ ] CI 绿勾：GitHub → Actions 页 ci.yml 运行通过
- [ ] commit 历史是 7 条分阶段中文提交，不是"初始提交"
- [ ] 仓库 Settings → General 默认分支为 main

## 给老师展示的建议路径

1. README.md —— 架构图 + 10 阶段成果表
2. docs/design.md —— 需求清单 + 接口清单（提示词1/2/4/5产出）
3. examples/bare_qa_prototype.py —— 裸问答原型（提示词3）
4. src/learning_support/core/agent/ + docs/agent-trajectories.md —— Agent 设计与轨迹（提示词6/7）
5. tests/eval_cases.json + docs/launch-checklist.md —— 评测与上线检查（提示词8/9）
6. 现场演示：python scripts/init_db.py → 启动 → /chat 与 /admin
