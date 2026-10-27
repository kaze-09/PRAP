# PRAP 协议规范 v0.2（草案）

**PRAP: Preemption & Resource Arbitration Protocol for Multi-LLM-Agent Systems**

**版本**：v0.2（v0.1 + 信念隔离机制，2026-10-26；对应可行性分析时间线第 1–3 周产出）
**日期**：2026-10-26
**前置文档**：`PRAP_feasibility_novelty_analysis.md`（Gap 分析）、`PRAP_paper_reading_notes.md`（CoAgent/DPBench 精读）
**状态**：待评审 → 之后进入仲裁器最小实现（下一步）

---

## 1. 设计目标与非目标

### 1.1 目标
| 编号 | 目标 | 对应创新点 |
|---|---|---|
| G1 | 互斥资源上无死锁、无饥饿，且执行**可复现**（确定性全序） | Gap 3 |
| G2 | 抢占不需要 undo：TTL 过期自动失效，覆盖 LLM agent 卡死/超时 | Gap 3 |
| G3 | 区分两类故障：**传输时序型信念过期** vs **真实信念偏差（幻觉）** | Gap 1（核心） |
| G4 | 同步/观察本身有代价，协议内建订阅决策（观察经济学） | Gap 2 |
| G5 | 协议开销可度量（消息数、token 成本、阻塞时长） | Gap 4 |

### 1.2 非目标
- 不处理 CRDT 可合并状态（AgentRoom 已覆盖）。
- 不保证跨不可逆操作的事务原子性（CoAgent 的 saga 路线，依赖 undo 预注册；PRAP 明确放弃 undo，见 §2.1 故障模型）。
- 不优化单 agent 内部推理质量。

### 1.3 系统模型
- 一个**权威仲裁器**（arbiter，单点、可审计）持有 L0 权威状态。
- N 个 LLM agent，每个运行长推理事务（数分钟级），通过薄 SDK 与仲裁器通信。
- 资源 = 可命名实体（文件、工具、环境格子、配额），key 唯一标识。
- 通信：可靠传输、at-least-once、所有消息携带 `(lease_id, seq)` 保证幂等。

## 2. 故障模型

| 故障 | 表现 | PRAP 处理 |
|---|---|---|
| agent 崩溃 | 持租约后失联 | TTL 过期自动释放（L1） |
| agent 挂死（LLM 超时/循环） | 长时间不续约、不动作 | TTL 过期自动释放；续约失败即降权 |
| 信念过期（别人改了状态，我没同步） | agent 计划基于旧状态 | L3 和解消息 + L4 分类为"时序型"，修补即可 |
| 信念偏差（幻觉状态，从未真实观察过） | agent 上报的信念摘要与权威状态不符，且**并非**由错过更新解释 | L4 分类为"偏差型"，走校验/重观察路径 |
| 消息丢失/重复 | 网络层 | 幂等重放，仲裁器去重 |

**与 CoAgent 的决定性差异**：CoAgent 的建议式修复假设 agent 收到通知后能正确重判断；当 agent 信念本身错误时它会"自信地修补一个错误计划"，且运行时**无机制检测**这一点。PRAP 的 L4 显式建模该故障（对应 2605.12920 的实证：对话减冲突却降成功率）。

## 3. 分层架构

**设计来源**：各层均有游戏状态同步技术的工程先例作为迁移原型——L0←权威服务器，L1←（反）快照回滚（TTL 替代 undo），L2←帧同步/确定性锁步，L3←客户端预测+和解（reconciliation），L5←兴趣管理（AOI，附加经济学）。迁移中新增的、游戏栈中不存在的是 L4（游戏客户端不会"想错"，LLM agent 会幻觉）——这是本协议相对纯迁移的研究贡献所在。

```
L0  权威状态层    版本化状态存储 + 确定性重放日志
L1  租约层        意图 + TTL 租约（互斥的授予与自动失效）
L2  仲裁层        确定性全序 + 确定性 tie-break
L3  和解层        deny_with_state：拒绝时附带权威状态差量
L4  信念校验层    故障二分类：时序型 vs 偏差型
L5  订阅决策层    Beta-Bernoulli 后验阈值（观察经济学）
```

### 3.1 L0 权威状态层
- 状态 = `{key → (value, version, last_writer, last_change_seq)}`。
- 所有变更追加到 WAL 式日志 `(epoch, seq, lease_id, op, args)`；重放日志可确定性重建状态（op 需确定性执行）。
- `epoch` 在仲裁器重启后递增，租约跨 epoch 全部失效（简单优先，恢复语义后续版本再扩展）。

### 3.2 L1 意图 + TTL 租约
- agent 提交 **INTENT**：声明目标资源 key 集合 + 计划操作类型（读/写）+ 预估持有时长。
- 仲裁器**原子地**授予或拒绝整个 key 集合（all-or-nothing）→ 从结构上消除 hold-and-wait，死锁不可能（见 §6.2）。
- 租约携带 `ttl`；到期未续约自动释放，无需 undo、无需协调。**抢占 = 不再续约的等待者按全序接管**。
- 续约上限：租约可续，但累计持有时间超过 `max_hold` 后优先级随等待时间老化（见 §3.3）。

### 3.3 L2 确定性仲裁
- 全序：所有授权/拒绝按 `(epoch, seq)` 记录，seq 单调。
- 等待队列排序 tie-break（确定性、可复现）：`priority(降序) → wait_since(升序，即老化) → agent_id(字典序) → intent_seq(升序)`。
- `priority` 由 agent 声明（0–9），但**随等待时间线性老化**（每 `aging_unit` 加 1），保证无饥饿（§6.3）。

### 3.4 L3 和解层
- 拒绝时不是裸 `DENY`，而是 `DENY_WITH_STATE`：附带给定 key 集合的权威状态差量 `(key, version, value_digest, holder)`。
- agent 收到后本地重规划，**无需额外观察动作**——把"失败 → 重新看世界"的两步压缩为一步，节省观察代价（与 L5 的经济学目标一致）。
- 差量只含 INTENT 声明的 key 集合，天然最小化（相当于游戏 interest management 的"只同步相关实体"）。

### 3.5 L4 信念校验层（主要研究工作量）
- 每个 agent 维护对共享状态的**信念摘要**：`(key → belief_version, belief_digest)`，随 INTENT 一并上报（可选字段，开启后进入校验模式）。
- 仲裁器对每个上报的 key 计算分类：
  - `belief_version == authority.version 且 digest 相符` → 信念正确；
  - `belief_version < authority.version` → **时序型过期**（agent 落后于世界；deny_with_state 足以修复）；
  - `belief_version >= authority.version 但 digest 不符` 或 `引用了权威状态中不存在的 key` → **偏差型**（agent 幻觉；同步再多也无法修复）。
- 判定为偏差型时仲裁器返回 `BELIEF_VERDICT(deviation, evidence)`，agent 侧策略：强制重观察 + 计划降级 + 上报置信度。
- **v0.2 信念隔离（enforcement）**：判为 deviation 的 agent 进入 `suspect` 状态——其后续 COMMIT 一律被拒（`COMMIT_REJECTED`），租约强制收回（资源立即让给健康 agent），直到它以正确信念重新 INTENT（判 ok/stale 即解除）。动机：持续幻觉的 agent 若仍可执行操作，会把过期/错误的世界观写入共享状态；隔离保证"坏信念不产生任何作用"。解除条件使隔离不惩罚可修复故障：时序型（stale）与健康的 agent 不受影响。
- **该规则完全基于版本号比较，不依赖 LLM 判断——避免循环论证**；LLM 自由判断的准确率作为对比单独报告（对应可行性分析 §4.3 风险缓解）。
- 关键实证问题（论文核心实验 ②）：偏差型与时序型在真实 LLM agent 行为分布上是否可分？噪声扫描（对应 h5 的概率化证据）下分类的 ROC 曲线。

### 3.6 L5 订阅决策层（贝叶斯观察经济学）
- 对每个关注的 key，agent 维护 Beta-Bernoulli 后验：n = 观察到的"变化机会"次数，k = 该 key 在机会窗口内实际变化的次数，后验均值 `\hat p = (k+α)/(n+α+β)`。
- 订阅（持续接收 STATE_UPDATE）当且仅当：
  `E[漏发代价] = \hat p × L_miss > C_sub + \hat p × C_recheck`
  其中 `L_miss` = 基于过期状态行动的期望损失（动作类型决定），`C_sub` = 订阅开销（消息/token），`C_recheck` = 不订阅时失败后重观察重规划的开销。
- 该判据是标准的贝叶斯序贯决策（后验估计 + 期望代价比较），独立自洽，不依赖其他项目的结果。历史注记：同型判据曾在本组 Collab-Overcooked 项目中以"请求帮助定价"形式独立出现（h5），本协议将其一般化到任意资源 key；两线技术解耦，互引与否属投稿期写作策略。
- **决策模式（E3 定型）**：
  - `mean`：后验均值 > p* —— 激进；证据少时结构性误订阅
  - `lower_bound`：后验均值 − z·标准差 > p*（Beta 高斯近似）—— 保守；证据不足时自动要求更多观察，即"观察到多少次才敢"的机制化
  - 实测（p*=0.125，seed 固定）：lower_bound 需 n≈128 达 95% 决策一致性；mean 结构性封顶 ~92%（p* 附近的不可约边界质量）。两模式呈互补权衡：mean 吃亏于阈值下方（误订阅），lower_bound 吃亏于阈值上方（保守漏订阅）
- 产出：订阅阈值扫描的**校准曲线**（观察多少次、可靠度多少才值得订阅），见 `run_e3.py` / `e3_results.json`。

## 4. 消息定义（v0.1）

所有消息公共头：`(proto_ver, epoch, sender_id, seq, lease_id?)`。

| 消息 | 方向 | 字段 | 语义 |
|---|---|---|---|
| `INTENT` | A→R | `keys[{key, mode: r/w}], ops_digest, ttl_est, priority, belief_summary?` | 请求原子授予 keys |
| `GRANT` | R→A | `lease_id, ttl, granted_keys, queue_pos?` | 授予；`queue_pos` 供等待方估计 |
| `DENY_WITH_STATE` | R→A | `keys, state_diff[{key, version, digest, holder}], retry_hint` | 拒绝 + 权威差量 |
| `RENEW` | A→R | `lease_id, ttl_ext` | 续约；超时未续 = 释放 |
| `RELEASE` | A→R | `lease_id` | 主动释放 |
| `EXPIRED` | R→A | `lease_id` | 通知（异步，权威已释放） |
| `COMMIT` | A→R | `lease_id, ops[]` | 在租约内提交确定性操作 |
| `STATE_UPDATE` | R→A | `key, version, digest`（仅订阅者） | L5 订阅推送 |
| `BELIEF_REPORT` | A→R | `summary[{key, version, digest}]` | L4 校验输入（可与 INTENT 合并） |
| `BELIEF_VERDICT` | R→A | `class: {ok, stale, deviation}, evidence[]` | L4 分类结果 |
| `COMMIT_REJECTED` | R→A | `verdict` | v0.2：suspect agent 的提交被拒，租约已强制收回 |
| `SUBSCRIBE` / `UNSUBSCRIBE` | A→R | `keys, decision_basis{p_hat, L_miss, C_sub}` | L5 决策（记录用于校准曲线） |

- 仲裁器是唯一写 L0 的主体；agent 的 COMIT 操作必须确定性（同参数同结果），保证重放一致。
- 所有请求幂等：`(sender_id, seq)` 去重。

## 5. 状态机

### 5.1 Agent 侧事务状态机
```
IDLE → PROPOSING(发 INTENT)
  ├─ GRANT      → ACTIVE（租约有效期内执行 COMMIT，可 RENEW）
  │                 ├─ 完成 → RELEASE → IDLE
  │                 ├─ EXPIRED → IDLE（工作保留但需重取租约再提交）
  │                 └─ BELIEF_VERDICT(deviation) → REPLANNING（强制重观察）
  └─ DENY_WITH_STATE → REPLANNING（吸收差量，可立即重发 INTENT）
REPLANNING → PROPOSING
```

### 5.2 租约状态机（仲裁器侧，每资源组）
```
FREE → HELD(lease_id, ttl)
  ├─ RENEW      → HELD(ttl 重置；累计持有 > max_hold 则标记 aged)
  ├─ RELEASE    → FREE
  ├─ TTL 过期   → FREE（+ 异步 EXPIRED 通知）
  └─ epoch 重启 → FREE（全部失效）
FREE 上有等待队列时：FREE →(按 §3.3 全序取队首) HELD
```

## 6. 正确性论证提纲（论文理论节骨架）

1. **互斥/安全性**：授权是仲裁器内的单点串行决策，每 key 组同一时刻至多一个 HELD 租约；COMMIT 校验 lease_id 有效 ⇒ 冲突写不可能并发生效。
2. **无死锁**：INTENT 原子授予（all-or-nothing）消除 hold-and-wait；TTL 保证任何持有有限期释放；等待不阻塞推理（agent 在等待期可继续规划），故不存在循环等待链。形式化目标：证明任意 INTENT 在有限时间内得到 GRANT/DENY 之一。
3. **无饥饿**：老化优先级 ⇒ 等待时间越长 priority 越高；给出等待时间上界 `W ≤ f(队列长度, ttl, aging_unit)`，并论证对 `max_hold` 的单调性。
4. **确定性/可复现**：`(epoch, seq)` 全序 + 确定性 op + 确定性 tie-break ⇒ 相同输入日志重放出相同状态轨迹。这是评测中"同 seed 同结果"的基础。
5. **L4 分类可靠性**：版本号比较规则的可证命题——偏差型误报仅当 L0 状态本身损坏（可排除）；时序型漏报仅当 agent 跳过上报（协议可选字段）。真实不确定性（LLM 上报噪声）交给 oracle 实验（受控故障注入，已知真值）。
6. **L5 阈值最优性**：在损失模型 `L_miss, C_sub, C_recheck` 给定的假设下，阈值判据等价于单步贝叶斯决策的最优停止条件；讨论与 Collab-Overcooked h3（纯随机队友 = 最坏情况）结论的衔接——后验不可集中时阈值永不满足，即"永不订阅"，与 v6 结论一致。

## 7. 评测设计挂钩（对应创新点，供测试床实现对齐）

| 实验 | 操纵变量 | 指标 | 预期产出 |
|---|---|---|---|
| E1 故障注入鲁棒性 | 卡死 agent 比例、幻觉 agent 比例 | 任务成功率、状态损坏数 | PRAP(TTL 租约) vs CoAgent 式 undo vs 悲观锁 |
| E2 L4 分类准确率 | 信念噪声强度（对应 h5 概率证据） | ROC/AUC（oracle 真值） | 时序型/偏差型可分性 |
| E3 订阅阈值扫描 | α/β 先验、L_miss/C_sub 比值 | 校准曲线、总观察成本 | L5 阈值的最优性验证 |
| E4 协议结构对比 | DPBench 式三变量（协议/通信/规模） | 死锁率、饥饿率、吞吐、token 开销 | 协议层 vs prompt 级 vs 合同网 vs 无协议 |

基线：悲观锁（先抢后干）、CoAgent 式 OCC（含建议式修复）、合同网（CNP）、无协议纯竞争、prompt 级预承诺通信（DPBench 已验证有效的那条）。

## 8. 开放问题（实现前需定）

1. `ttl` 与 `max_hold` 默认值如何随"预估持有时长"自适应？（可能做成 L1 的一个小贡献点）
2. `DENY_WITH_STATE` 的差量在 key 很多时的分页/截断策略。
3. 多仲裁器（分区仲裁）是否进入 v1——建议否，单点即可支撑论文实验，分区留作 future work。
4. COMIT 操作集的最小确定性算子集（网格测试床：move/grab/place 足够）。
5. L4 的 `belief_summary` 上报频率：仅 INTENT 时 vs 周期性（影响 token 开销，进 E4 指标）。

## 9. 下一步（对应可行性分析 §6.3）

- [ ] 评审本规范，冻结 v0.1 消息集与状态机
- [ ] 实现仲裁器最小原型（Python，单文件即可起步）+ 网格测试床（1–2 周口径）
- [ ] E1 最小版：2 agent + 1 卡死故障注入，验证 TTL 释放路径
