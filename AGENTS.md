# AGENTS.md —— 给 AI 助手与协作者的仓库约定

本文件是给在本仓库里写代码的 AI 助手（以及人类协作者）读的。
只写**会影响你的动作**的约定；技术细节去 [README.md](README.md) 与
[`docs/api/测试说明.md`](docs/api/测试说明.md)。

---

## 1. 提交规范（硬约束）

**commit message 里不加 `Co-Authored-By:`，也不加任何其他 AI/agent 署名**
（如 `Generated with [Claude Code]`、`🤖 Generated with …` 之类）。

commit message 只描述**代码变更本身**，不记录「谁参与了写这段文字」。

- 一条都不要加，不是「少加一条」。默认不加。
- 也不要为了「显得规范」而把 AI 署名以别的形式塞进正文或 footer。
- 这条约束适用于本仓库所有分支、所有 commit，包括修 bug 和 chore。

参考：2026-10-06 曾有两个 commit 误带 `Co-Authored-By`，已用
`git filter-branch --msg-filter` 改写全历史并 `--force-with-lease` 重推。
若再次发生，按同样方式清理（注意 `filter-branch` 有未暂存改动时会拒绝执行，
且必须手工删除 `refs/original/*`，否则旧 commit 仍可达）。

提交信息风格沿用仓库现状：Conventional Commits 前缀
（`fix(api):` / `docs(changelog):` / `test(report):` …），正文用中文，
写清**为什么**这么改而不是只写改了什么。

---

## 2. 命令与验证

```bash
# 后端测试（在仓库根跑）
pytest -q                                       # 预期 283 passed
cd apps/web && npm run typecheck              # 前端类型检查
npm run test:e2e                              # 需先起服务，预期 44 passed / 6 skipped
pwsh -File scripts/firesight.ps1 start         # 起服务并等就绪
```

**跑 pytest 必须在环境里设 `FIRESIGHT_LLM_API_KEY`**（随便给个假值即可，如
`FIRESIGHT_LLM_API_KEY=sk-test-not-real`）。否则
`tests/test_28_report_api.py::test_duplicate_generation_is_refused` 会失败——测试模块
里唯一设置该变量的地方是 fixture，不设等于断言「未配置模型通道」，与被测代码无关。

**ruff 当前不是 CI 门禁**：仓库现存 858 个 ruff 错误（集中在 `UP045`/`UP035`/
`UP031` 存量代码）。所以**不要顺手全仓 `ruff --fix`**——那会淹没真正的改动。
只保证自己新增的代码不引入新的错误（改前改后各跑一次对比增量即可）。

改代码前先跑一遍基线，**没有基线就不知道你有没有弄坏什么**。

---

## 3. 数据集约定

当前数据集是 **`D-Fire/raw`**，即直接用 `data/raw/D-Fire/` 训练，
`data/processed/D-Fire/` 下的旧转换产物已于 2026-10-06 删除（可用 seed 42 完全重建）。

`--dataset` 接受三种形式，代码与文档都按这个写：

| 形式 | 例子 | 解析到 |
| --- | --- | --- |
| `<name>/raw` | `"D-Fire/raw"` | `data/raw/<name>/data.yaml` |
| 转换产物 | `"BowFire"` / `"BowFire/train"` | `data/processed/<rel>/data.yaml` |
| 路径 | `data/raw/D-Fire/data.yaml` | 直接用 |

**两种标签布局都要认**，别写死一种：

- 转换产物（pipeline 产物）：`train: <root>/images/train`，标签在 `<root>/labels/<split>/`
- 提供方已划分（`split_strategy: provided`）：`train: <root>/train/images`，标签在
  `<root>/train/labels/` —— 与 images **同级**，不是 `<root>/labels/<split>/`

> 这个坑踩过一次：`data_validation/snapshot.py` 曾硬编码 `<root>/labels/<split>`，
> 导致 `fs-validate --dataset "D-Fire/raw"` 报「缺失标签 21527/21527（100.0%）」，
> 而实际上标签一个不缺。现在由 `snapshot._label_dir()` 同时处理两种布局，
> **检查项不要自己再推算标签目录**，一律从 `CheckContext.label_dirs` 取。

另外：`data/raw/` 是只读的。校验报告写进 `.runtime/quality/`，别往 raw 里写。

---

## 4. 不要做的事

- **不要删文件**。用户没明确点头之前，一个文件都不删（包括临时文件和缓存）。
  清理前先给清单和依据，等用户决策。
- **不要 `git commit --amend` 或改写已推送的历史**，除非用户明确要求。
- **不要顺手改无关代码**。发现别处的 bug 就报告，不要在当前改动里一起修。
- **不要把 `data/`、`models/trained/*/`、`runs/`、` .runtime/` 提交进去**，
  `.gitignore` 已覆盖；也别为了让 `.gitignore` 生效去 `git add -f`。

## 5. 仓库里已知的脏东西（用户知情，勿动）

根目录有未跟踪的个人文件（3 个报告 docx/pdf、`pre-result.txt`、
`graphify-out/`、`.archify/`、`.report-work/`、`.acl-recovery/`），
以及 `HANDOFF.md`（已滞后于当前版本）。这些都不属于你，**不要提交、不要删除**。

## 6. Windows 环境注意事项

- shell 是 PowerShell，不是 bash；路径用 `E:\FireSight\...`。
- 若在受限沙箱里对 `E:\FireSight` 的**子目录**写文件/删文件遇到 `Access denied`，
  那是沙箱令牌限制、不是 NTFS 权限问题——用 unrestricted 权限重试一次即可，
  **不要去改 ACL**。
- 项目根有 `.firesight-workspace` 标记文件，**不要删**。