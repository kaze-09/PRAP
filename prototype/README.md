# PRAP prototype

Official confirmatory results, the unified-seed runner, and the offline table reprint live in this folder:

- unified_seed_rerun/ — official trial logs (do not overwrite)
- 
un_unified_confirmatory.py — confirmatory runner
- summarize_confirmatory.py — reprint tables without an API key

See [../ARTIFACT.md](../ARTIFACT.md) for the official counts and measurement contract. The notes below describe the earlier prototype and are kept as a historical guide.

---

# PRAP 原型（最小实现）

对应 `../PRAP_protocol_spec.md` v0.1 的最小可运行实现，用于验证协议关键路径后再扩展为完整测试床。

## 运行

```powershell
python run_e1.py            # 脚本 agent 协议验证（无网络，秒级）
python run_llm_smoke.py     # 真实 LLM agent 冒烟（需先填 api_keys.json）
python run_llm_smoke.py --stuck   # + 挂死注入场景
```

LLM 冒烟支持 Qwen（DashScope 兼容模式）与 DeepSeek，均为 OpenAI 兼容接口、零额外依赖（stdlib urllib）。
key 二选一方式：填 `api_keys.json`（勿提交到 git）或设环境变量 `QWEN_API_KEY` / `DEEPSEEK_API_KEY`。

## 文件

| 文件 | 内容 | 对应规范 |
|---|---|---|
| `prap_core.py` | 仲裁器：L0 权威状态 + WAL、L1 TTL 租约、L2 确定性仲裁（tie-break + 老化）、L3 DENY_WITH_STATE、L4 信念校验（版本号比较二分类）、L5 订阅推送 | §3–§6 |
| `testbed.py` | 5x5 网格测试床：确定性算子 move/grab/place、脚本化 agent（薄 SDK，stuck 模拟挂死）、离散事件仿真（虚拟时钟） | §2、§8.4 |
| `run_e1.py` | E1 最小实验：TTL 释放、续约、L4 分类、确定性重放、L5 订阅，共 17 项断言 | §7 |

## 设计要点

- **确定性**：无随机源；全序 `(epoch, seq)`；tie-break = 有效优先级(降) → 等待时长(升) → agent_id → seq。同一场景脚本两次运行的日志与终态逐字节一致（场景 3 验证）。
- **虚拟时钟**：整数 tick 离散事件仿真，TTL 到期、推理延迟（think_time）都在虚拟时间上推进，实验秒级完成且可复现。
- **无死锁机制**：INTENT 原子授予（all-or-nothing）消除 hold-and-wait；TTL 过期自动释放（无需 undo），挂死 agent 的租约被等待者在到期 tick 接管（场景 1/1b 验证）。
- **L4 无循环论证**：分类纯靠版本号比较（落后 = stale；key 缺失/版本超前/digest 不符 = deviation），oracle 真值由故障注入给出（场景 2）。

## 下一步（对应可行性分析 §4.2 / 规范 §7）

1. 接入真实 LLM agent（LangGraph/AutoGen 薄封装，SDK 接口已就位：`submit_intent / on_msg / commit`）
2. E1 完整版：故障注入比例扫描（卡死/幻觉 agent 占比）
3. E2：信念噪声扫描 -> L4 分类 ROC 曲线
4. E3：订阅阈值扫描 -> 校准曲线（`subscribe` 的 decision 字段已埋点）
5. 移植 Overcooked 作为真实案例
