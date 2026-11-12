# R2 预注册：发布前几何约束的闭环评估

**冻结时间：2026-11-12 21:18:46 +08:00（Asia/Shanghai）**
**本文件在首批 API 调用之前写定。** 数据产生后不得修改臂、种子、主终点、判定规则或纳入规则；任何偏离必须如实标注为偏离并说明理由。

## 0. 冻结前的可行性检查（只读现有日志，不产生新数据）

仅统计既有逐 trial JSON 中**已经记录**的 `consistent` / `wrong_release` / `success` 字段，用于确定实验规模，不作为结果报告：

| 文件 | n | wrong_release | 其中 inconsistent | success | 其中 inconsistent |
| --- | --- | --- | --- | --- | --- |
| `design_leaseonly_planslip.json` | 10 | 7 | 7 | 0 | 0 |
| `design_gate_planslip.json` | 10 | 6 | 6 | 0 | 0 |
| `design_leaseonly.json` | 100 | 2 | 2 | 81 | 0 |
| `design_gate.json` | 100 | 0 | 0 | 0 | 0（`consistent=0`：B 被隔离，终态 `(12,4,18)` 不一致但未发布） |
| `design_gatepin.json` / `design_gaterassign.json` | 100 | 0 | 0 | 100 | 0 |

**设计推论**：若约束只在 `release_revision` 前置检查最终状态，理论上可拦住上述全部 15 次错误发布，且所有成功发布的状态都一致（误拒上界 0）。
**重要约束**：检查点**不得**放在 `set_param`。A 把 bore 改成 12 后、B 尚未同步 wall/outer 的中间态必然不满足 `outer = bore + 2×wall`（`design_gate.json` 的 `consistent=0` 即为该情形），在写入层拦截会误拒所有合法中间写入。

## 1. 目的

测量"发布前几何约束"在**闭环**中的收益与代价：错误发布是否被消除、拒绝后智能体重规划带来的浪费/tokens/卡死，以及是否误拒本来正确的发布。离线检查只能回答"会不会拦/会不会误拒"，**不能**回答拒绝后的闭环代价，因此需要新跑。

## 2. 实现（冻结）

- 检查点：`design_doc.py` 的 `op_release_revision`，在 `status == in_design` 校验之后、写入之前，检查 `consistent(snapshot(arb))`；不满足返回 `(False, "dimension_constraint")`。
- 开关：`arb.dimension_constraint`，默认 `False`；仅新臂置 `True`。**现有臂默认行为不得改变**。
- 不修改 `SYSTEM_PROMPT`（其中已有几何约束说明）、不修改 `+1` 注入方式、不修改门控/租约逻辑、不修改 `HORIZON`。
- 记录：arb.log 中 `detail == "dimension_constraint"` 的 op 数记为 `constraint_denies`。

## 3. 臂、条件与种子（同批、配对）

两个条件，各跑三臂：

- **条件 P（plan-slip）**：缓存正确，B 提交的 `wall`/`outer` 值各 `+1 mm`（复用 `apply_plan_slip`）。
- **条件 O（observation-bias）**：B 的 `bore` 缓存 `+1 mm`（复用 `inject_bore`）。

臂：`leaseonly`（对照）、`gate`（对照）、`gate_constraint`（新：gate 开 + 约束开）。
每臂每条件 **`n = 10`（首批）**。种子 `constraint-20261112-{cond}-{trial}`（cond = `P` / `O`，trial = 0…9），**arm 名不进种子**，保证同一注入集与模型分配；模型按 trial 奇偶在 `qwen-plus` / `deepseek-chat` 之间对调，温度 0.2，与现有细胞一致。
**对照组必须与新臂在同一批次、同一进程重跑**，不得复用旧日志作对照（在线 API 无日期化快照）。
输出目录 `prototype/constraint_runs/`，文件名 `constraint_{cond}_{arm}.json`，不写入 `design_doc_rerun/`。

## 4. 主终点（冻结）

1. 错误发布率：`wrong_release / n`（`status == released` 且 `params_correct == False`）。
2. `constraint_denies`：被约束拒绝的 release 次数。
3. 误拒数：终态满足 `params_correct` 且 `consistent`、却未成功发布的 trial 数。
4. 代价：`success`、`wasted_ops`、`tokens`、`llm_calls`、`denies`、未发布率、`HORIZON` 耗尽数。

## 5. 判定规则（冻结）

- 若条件 P 下 `gate_constraint` 的 `wrong_release` 低于同批 `gate`，**必须同时报告** `wasted_ops` / `tokens` / `success` / 卡死；结论写为"以额外代价换取消除错误发布"，**不得**写成"约束更优"。
- `wrong_release = 0` 若由拒绝规则保证，明确标为**定义性结果**，不得用 p 值包装成统计发现。
- 条件 O 只作兼容性与误拒检查，不参与主结论的显著性检验。
- 不追加 trial 直到出现期望结果；不为提高错误率改注入幅度、模板或提示词。

## 6. 明确不做什么

- 不把 `+1` 改成别的值、不换模型、不改提示词、不改 `HORIZON`、不改成功判定。
- 不把中间态不一致当作故障（约束只在 release 时检查）。
- 不把本细胞写成前瞻注册的确认性验证；它是探索性后加实验，不编造登记号。
- 不用旧日志做对照，不覆盖任何既有 `design_doc_rerun/` 文件。

## 7. 改稿规则

先跑完、汇总后再改 6 页稿。若约束有效且误拒为 0：在 `§IV-E` 增一小段、去掉 Table VI 该行的 `*`、同步 `§V`/`§VI`/`§VII`，并在 `ARTIFACT.md` 登记种子与计数；若出现卡死或非零误拒，如实报告代价与边界，不夸大。当前 PDF 已满 6 页，新增内容须删等量文字并重新编译核对页数。
