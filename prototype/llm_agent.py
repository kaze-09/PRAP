"""真实 LLM agent 适配器：把 ScriptedAgent 的三个接入点替换为 LLM 调用。

接入点映射（见 testbed.py 注释）：
  1. _submit    -> LLM 规划：观察 -> JSON 计划 {keys, ops, ttl, priority}
  2. on_msg     -> 继承协议处理；DENY_WITH_STATE 的差量更新信念缓存
  3. _commit    -> COMMIT 后检查失败算子，失败且有余额则用新观察重规划（L3 路径）

信念缓存（L4 的真实 agent 语义）：
  agent 只"知道"它观察过的 (key, version, digest)； belief_summary 由 SDK
  从缓存构造，幻觉注入 = 给它一份被篡改的观察（值错、版本相同）-> digest
  不符 -> deviation。

注意：虚拟时钟不反映真实 LLM 网络延迟（协议机制验证不依赖真实时延）。
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.request
from typing import Any, Optional

from prap_core import Arbiter, Lease, digest
from testbed import ScriptedAgent, fmt_pos

MAX_OPS = 24          # 单次计划算子数上限（防失控）
W, H = 5, 5


# ---------- OpenAI 兼容客户端（零依赖） ----------

class LLMClient:
    def __init__(self, name: str, base_url: str, api_key: str, model: str,
                 temperature: float = 0.2, timeout: int = 120):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def chat(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.temperature,
        }
        last_err: Optional[Exception] = None
        # 指数退避重试：DashScope 间歇性 400（限流窗口）实测需要更长等待
        for attempt, wait in enumerate([2, 4, 8, 16, 30, 60]):
            try:
                req = urllib.request.Request(
                    f"{self.base_url}/chat/completions",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {self.api_key}"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                u = data.get("usage", {})
                self.usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                self.usage["completion_tokens"] += u.get("completion_tokens", 0)
                self.usage["calls"] += 1
                return data["choices"][0]["message"]["content"]
            except Exception as e:                     # noqa: BLE001
                last_err = e
                print(f"  [{self.name}] 调用失败(第{attempt + 1}次): {e} -> {wait}s 后重试")
                time.sleep(wait)
        raise RuntimeError(f"{self.name} chat 失败（重试 6 次）: {last_err}")

    @staticmethod
    def extract_json(text: str) -> Optional[dict]:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def load_clients(path: Optional[str] = None) -> dict[str, LLMClient]:
    """从 api_keys.json 或环境变量（QWEN_API_KEY / DEEPSEEK_API_KEY）加载。"""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_keys.json")
    cfg: dict[str, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    defaults = {
        "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 "api_key": os.environ.get("QWEN_API_KEY", ""),
                 "model": "qwen-plus"},
        "deepseek": {"base_url": "https://api.deepseek.com/v1",
                     "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
                     "model": "deepseek-chat"},
    }
    clients = {}
    for name, d in defaults.items():
        c = dict(d)
        c.update({k: v for k, v in cfg.get(name, {}).items() if v})
        if not c["api_key"]:
            continue
        clients[name] = LLMClient(name, c["base_url"], c["api_key"], c["model"])
    return clients


# ---------- 提示词 ----------

def system_prompt(w: int, h: int) -> str:
    """系统提示词（按世界尺寸参数化——T4 7x7 等任务需要）。"""
    return f"""你是 {w}x{h} 网格世界中的具身 agent。坐标 (x,y)：x 向右，y 向下，范围 0..{w - 1}（x）、0..{h - 1}（y）。

状态 key 约定：item:{{名字}}=物品位置，pos:{{你的ID}}=你的位置，hold:{{你的ID}}=你持有的物品。
可用算子（确定性，由权威仲裁器执行）：
  ["move", {{"dx": ±1 或 0, "dy": ±1 或 0}}]   一步移动，越界则失败
  ["grab", {{"key": "item:名字"}}]              必须与物品同格，且物品在世界上
  ["place", {{"key": "item:名字"}}]             在当前格放下持有的物品

事务规则：对你要操作的物品 key，必须先通过 INTENT 锁定（keys 字段），获得 GRANT
后你的 ops 才会被执行。若被 DENY，你会停在等待队列，稍后自动获得授权。

只输出一个 JSON 对象，不要任何其他文字：
{{"thought": "简短推理", "keys": ["item:xxx"], "ops": [["move", {{...}}], ...],
 "ttl": 5, "priority": 5,
 "belief": {{"item:xxx": {{"value": "world@(x,y)", "version": 3}},
             "pos:你": {{"value": "(x,y)", "version": 2}}}}}}
ops 是从你当前位置到物品、抓取、再到投放点的完整序列（仔细核对每一步的坐标算术）；
ttl=预计占用 tick 数；priority 0-9（越急越高）。belief 的 value 填你当前相信的各
key 的值（如实作答，格式必须与状态里的写法完全一致），version **原样抄写**状态里
该 key 的 version 数字——value 与 version 都由你自报，协议将据此校验你的信念：
版本落后判"滞后"（补课即可），版本不落后但值错误判"偏差"（触发隔离）。"""


# ---------- LLM Agent ----------

class LLMAgent(ScriptedAgent):
    """goal 用自然语言给出；keys/ops 由 LLM 规划，SDK 负责协议与信念缓存。"""

    SYSTEM_PROMPT_OVERRIDE: Optional[str] = None   # E4 提示词修复基线用

    def __init__(self, sim, agent_id: str, client: LLMClient, goal: str,
                 item_keys: list[str], start_pos, max_replans: int = 2,
                 belief_override: Optional[dict[str, str]] = None,
                 stuck: bool = False, think: int = 2,
                 belief_mode: str = "sdk_cache",
                 ttl_override: Optional[int] = None,
                 sub_policy: Optional[str] = None,
                 visible_keys: Optional[set] = None,
                 override_prob: float = 0.0,
                 obs_seed: Optional[str] = None):
        # think>0：GRANT 与 COMMIT 之间保留"推理区间"，租约在 LLM 规划期间仍被持有
        #（CoAgent 所说的事务=数分钟推理；虚拟时钟上以 2 tick 代表）
        super().__init__(sim, agent_id, (), [], ttl=5, think=think, stuck=stuck)
        self.client = client
        self.observed: dict[str, tuple[int, str]] = {}   # 信念缓存：key -> (version, digest)
        self.claimed_belief: dict[str, str] = {}         # LLM 自报的信念（llm_claimed 模式用）
        self.belief_checks: list[dict] = []              # 每次规划的"自报 vs 计划时刻真值"记录
        # belief_mode: sdk_cache = SDK 从观察缓存构造 belief_summary（E1 口径）
        #              llm_claimed = 由 LLM 自报的信念构造（E2 口径：噪声来自 LLM 本身）
        self.belief_mode = belief_mode
        self.goal = goal
        self.item_keys = list(item_keys)
        self.start_pos = start_pos
        self.max_replans = max_replans
        self.replans_left = max_replans
        self.belief_override = belief_override or {}
        # R4 间歇偏置：override_prob > 0 时每次观察以该概率看到错位值
        #（下一次观察可恢复真值——对比持续性注入）。obs_seed 保证逐局可复现。
        self.override_prob = override_prob
        self._obs_rng = random.Random(obs_seed) if obs_seed is not None else None
        # R4：被隔离拦截后的重新申请余额（规范的解除路径 = 重新观察 -> 正确信念
        # INTENT）。上限 3 次，防止持续性故障下的无限重申烧 token。
        self._reapplies_left = 3
        self.ttl_override = ttl_override   # E4 悲观锁基线：租约永不过期
        self.plans: list[dict] = []       # LLM 计划记录（供报告）
        self.last_verdict: Optional[str] = None
        # ---- L5 端到端消融（P1-3）：订阅策略 ----
        self.sub_policy: Optional[str] = sub_policy   # None/never/always/threshold/oracle
        self.subscribed = False
        self.updates_recv = 0                    # 收到的 STATE_UPDATE 数
        self.post_n = 0                          # 伯努利证据：授予次数
        self.post_k = 0                          # 其中计划已过期（stale）的次数
        self.proactive_refresh = 0               # 提交前主动刷新（省掉失败算子）
        self.reactive_replans = 0                # 失败后被动重规划（L3 路径）
        self.plan_digests: dict = {}             # 计划时刻快照 {key: (version, digest)}
        self.p_star = 1 / 3.5                    # E3 口径：C_sub=1, L_miss=4, C_recheck=0.5
        self.visible_keys = visible_keys         # E5：部分观察（None = 全量；P1-5）

    # -- 观察 -> 信念缓存 --

    def _observe(self) -> dict:
        """世界观察（冒烟版给全量；兴趣管理/订阅是 L5 的后续工作）。

        幻觉注入：假观察以"当前版本号 + 假值 digest"入缓存 —— 版本不落后、
        值是错的，L4 应判 deviation（同步无法修复）而非 stale。
        """
        obs = {}
        for key, entry in self.sim.arbiter.state.items():
            if self.visible_keys is not None and key not in self.visible_keys:
                continue   # E5 部分观察：可见集之外的 key 对 agent 不可见
            val = entry.value
            if key in self.belief_override:
                if self._obs_rng is None:
                    val = self.belief_override[key]   # 持续性注入（E1/E6 口径）
                elif self._obs_rng.random() < self.override_prob:
                    val = self.belief_override[key]   # R4 间歇偏置：本次观察被偏置
            obs[key] = {"value": val, "version": entry.version}
            self._cache(key, entry.version, digest(val))
        return obs

    def _cache(self, key: str, version: int, dig: str) -> None:
        self.observed[key] = (version, dig)

    def _build_belief(self) -> list:
        """构造 belief_summary（版本 = 最近一次观察时点，值 = 相信的内容）。

        sdk_cache 模式：值取自观察缓存（E1 口径，噪声仅在注入的观察里）。
        llm_claimed 模式（v0.2.2）：值**与版本号**都取自 LLM 自报——L4 的校验
        对象是模型自身的报告而非 SDK 缓存；LLM 漏报的 key 记 never_claimed
        （版本 -1 < 权威版本 -> 走 stale 修复路径）。
        """
        belief = []
        for k in self.keys:
            if self.belief_mode == "llm_claimed":
                if k in self.claimed_belief:
                    cb = self.claimed_belief[k]
                    belief.append((k, cb["version"], digest(cb["value"])))
                else:
                    belief.append((k, -1, "never_claimed"))
            elif k in self.observed:
                v, d = self.observed[k]
                belief.append((k, v, d))
            else:
                belief.append((k, -1, "never_observed"))
        return belief

    # -- 接入点 1：LLM 规划 -> INTENT --

    def _plan(self, obs: dict) -> dict:
        """LLM 规划：观察 -> {thought, keys, ops, ttl, priority}（含清洗与兜底）。"""
        user = (f"你的ID: {self.id}\n任务: {self.goal}\n"
                f"世界状态: {json.dumps(obs, ensure_ascii=False)}\n输出 JSON 计划。")
        plan = None
        sys_prompt = self.SYSTEM_PROMPT_OVERRIDE or system_prompt(
            self.sim.arbiter.width, self.sim.arbiter.height)
        for _ in range(2):   # JSON 解析失败重试一次
            plan = self.client.extract_json(self.client.chat(sys_prompt, user))
            if plan and plan.get("keys") and isinstance(plan.get("ops"), list):
                break
        if plan is None:
            plan = {"thought": "LLM 输出不可解析，退化：锁全部目标物品",
                    "keys": list(self.item_keys), "ops": [], "ttl": 5, "priority": 5}
        plan["keys"] = tuple(plan["keys"])[:5]
        plan["ops"] = self._sanitize_ops(plan["ops"])[:MAX_OPS]
        plan["ttl"] = max(int(plan.get("ttl", 5)), 2)
        plan["priority"] = max(0, min(int(plan.get("priority", 5)), 9))
        # LLM 自报信念（v0.2.2：value 与 version 都由 LLM 自报——L4 的校验对象
        # 从 SDK 缓存改为模型自身的报告；规范化空白防格式性误报）
        claimed: dict[str, dict] = {}
        for k, v in (plan.get("belief") or {}).items():
            if isinstance(v, dict) and "value" in v:
                try:
                    ver = int(v.get("version", -1))
                except (TypeError, ValueError):
                    ver = -1
                claimed[str(k)] = {"value": str(v["value"]).replace(" ", ""),
                                   "version": ver}
            else:   # 兼容旧平面格式：version 视为未自报（-1 -> 走 stale 修复路径）
                claimed[str(k)] = {"value": str(v).replace(" ", ""), "version": -1}
        self.claimed_belief = claimed
        # 自然误报率指标（v0.2.1 修复口径）：与"计划时刻"的权威真值对比，
        # 而非终局状态 —— 苹果会中途移动，终局对比会把时序滞后误算成幻觉
        for k, cb in self.claimed_belief.items():
            entry = self.sim.arbiter.state.get(k)
            if entry is None:
                continue
            truth = str(entry.value).replace(" ", "")
            self.belief_checks.append({
                "tick": self.sim.now, "key": k,
                "claimed": cb["value"], "truth": truth,
                "agree": cb["value"] == truth,
                "claimed_version": cb["version"], "truth_version": entry.version,
                "version_agree": cb["version"] == entry.version,
                "injected": bool(self.belief_override),
                "partial": self.visible_keys is not None,
            })
        self.plans.append({"thought": plan.get("thought", ""), "keys": plan["keys"],
                           "n_ops": len(plan["ops"]),
                           "ops": plan["ops"],   # 全量算子（供滑差/路径逐算子分析）
                           "replan": self.max_replans - self.replans_left})
        # L5：计划时刻快照（提交前过期检测的基准——对比权威状态即可判定计划是否失效）
        self.plan_digests = {k: (self.sim.arbiter.state[k].version,
                                 digest(self.sim.arbiter.state[k].value))
                             for k in plan["keys"] if k in self.sim.arbiter.state}
        print(f"  [{self.id}/{self.client.name}] 计划: keys={plan['keys']} "
              f"ops={len(plan['ops'])} | {plan.get('thought', '')[:80]}")
        return plan

    def _submit(self) -> None:
        plan = self._plan(self._observe())
        self.keys = plan["keys"]
        self.ops = plan["ops"]
        # E1 实证：LLM 自报 TTL 可能短于自身推理区间+执行窗口（C2 中导致
        # 租约在 COMMIT 前过期）。SDK 施加下界 = think + 算子数 + 余量；
        # LLM 原始估计与实际生效值一并记录（供"自估价可靠性"分析）。
        floor = self.think + len(self.ops) + 2
        self.ttl = max(plan["ttl"], floor)
        plan["ttl_requested"] = plan["ttl"]
        plan["ttl_enforced"] = self.ttl
        if self.ttl_override is not None:
            # E4 悲观锁基线：TTL = 无穷大（拿到即不放，直到显式 release）
            self.ttl = self.ttl_override
            plan["ttl_enforced"] = self.ttl
        self.priority = plan["priority"]
        self.sim.arbiter.submit_intent(self.id, self.keys, ops=self.ops,
                                       ttl=self.ttl, priority=self.priority,
                                       belief=self._build_belief())

    def _sanitize_ops(self, ops: list) -> list:
        """校验算子名、注入 agent id、规整参数（LLM 输出不可全信）。"""
        clean = []
        for op in ops:
            if not (isinstance(op, list) and len(op) == 2):
                continue
            name, args = op
            if name not in ("move", "grab", "place", "consume"):
                continue
            args = dict(args) if isinstance(args, dict) else {}
            if name == "move":
                dx, dy = int(args.get("dx", 0)), int(args.get("dy", 0))
                if dx == 0 and dy == 0:
                    continue
                if dx != 0 and dy != 0:
                    # v0.2.3：斜向意图展开为两步同轴移动（此前直接丢弃 ->
                    # 计划静默缺步 -> grab 级联失败，L5 消融逐算子捕获的
                    # qwen 特异性病理的根源）。展开保持模型意图，算子集不变。
                    for _ in range(abs(dx)):
                        clean.append(("move", {"agent": self.id,
                                               "dx": 1 if dx > 0 else -1, "dy": 0}))
                    for _ in range(abs(dy)):
                        clean.append(("move", {"agent": self.id,
                                               "dx": 0, "dy": 1 if dy > 0 else -1}))
                    continue
                # 多步压缩移动（qwen 常见，如 dx=2）确定性展开为单步序列，
                # 避免对输出风格不同的模型产生系统性偏置
                sx = (1 if dx > 0 else -1) if dx else 0
                sy = (1 if dy > 0 else -1) if dy else 0
                for _ in range(max(abs(dx), abs(dy))):
                    clean.append(("move", {"agent": self.id, "dx": sx, "dy": sy}))
            else:
                key = str(args.get("key", ""))
                if key:
                    clean.append((name, {"agent": self.id, "key": key}))
        return clean

    # -- 接入点 2：协议响应 --

    def on_msg(self, msg) -> None:
        super().on_msg(msg)
        if msg.type == "DENY_WITH_STATE":
            # L3：吸收权威差量到信念缓存（version+digest），无需额外观察
            for key, (ver, dig, _holder) in msg.state_diff:
                self._cache(key, ver, dig)
            if msg.verdict:
                self.last_verdict = msg.verdict["class"]
            # R4：隔离拦截后的解除路径——重新观察并以（可能已恢复的）信念重申。
            # 有限次数，间歇偏置下数步内即可携带正确信念解除隔离。
            ev = (msg.verdict or {}).get("evidence") or []
            if any(isinstance(e, (list, tuple)) and e and e[0] == "suspect_blocked"
                   for e in ev) and self._reapplies_left > 0:
                self._reapplies_left -= 1
                print(f"  [{self.id}] 被隔离拦截 -> 重新观察并申请解除（剩 "
                      f"{self._reapplies_left}）")
                self.sim.schedule(self.sim.now + 1, self._submit)
            # L5（P1-3）：被拒后按策略订阅目标 key——订阅期间 STATE_UPDATE
            # 免费推送（协议消息，无 LLM 开销），用于提交前过期检测。
            # oracle 不订阅（"全知"上界：无需消息即知过期）。
            if self.sub_policy in ("always", "threshold"):
                self.sim.arbiter.subscribe(self.id, self.keys,
                                           decision={"policy": self.sub_policy})
                self.subscribed = True
        elif msg.type == "GRANT" and msg.verdict:
            self.last_verdict = msg.verdict["class"]
        elif msg.type == "STATE_UPDATE":
            self.updates_recv += 1

    def _plan_stale(self) -> bool:
        """计划时刻快照 vs 权威现状：任一目标 key 已变化即为过期。"""
        for k, (v, d) in self.plan_digests.items():
            e = self.sim.arbiter.state.get(k)
            if e is None or (e.version, digest(e.value)) != (v, d):
                return True
        return False

    # -- 接入点 3：提交后失败检测 -> L3 重规划路径 --

    def _commit(self) -> None:
        if self.stuck or self.lease_id is None:
            return
        arb: Arbiter = self.sim.arbiter
        lease = arb.leases.get(self.lease_id)
        if lease is None or lease.ttl_end <= arb.now:
            # 租约在提交前已失效（E1 实证的"自报 TTL 过短"路径）：
            # 事务没有执行，不得记为完成 —— 转入重新申请/重规划
            self.lease_id = None
            print(f"  [{self.id}] 租约在 COMMIT 前过期（自报 TTL 过短）"
                  f" -> {'重新申请' if self.replans_left > 0 else '重规划余额耗尽'}")
            if self.replans_left > 0:
                self.replans_left -= 1
                self.sim.schedule(self.sim.now + 1, self._submit)
            return
        # L5（P1-3）：提交前过期检测 —— 订阅策略决定"主动刷新"还是"提交后失败"
        #   always/oracle：检测到过期即刷新（省掉失败算子，代价一次 LLM 重规划）
        #   threshold：仅订阅期间刷新（退订后退化为 never 的被动路径）
        #   never：无此路径 —— 过期计划直接提交，靠失败算子触发 L3 被动重规划
        stale_at_commit = self._plan_stale() if self.sub_policy else False
        if (self.sub_policy in ("always", "threshold", "oracle")
                and stale_at_commit and self.replans_left > 0
                and (self.sub_policy in ("always", "oracle") or self.subscribed)):
            self.replans_left -= 1
            self.proactive_refresh += 1
            print(f"  [{self.id}] L5 提交前过期检测：计划已失效 -> 主动刷新"
                  f"（剩 {self.replans_left}）")
            arb.release(self.lease_id)
            self.lease_id = None
            self.sim.schedule(self.sim.now + 1, self._submit)
            return
        self._committed = True
        arb.commit(self.lease_id, self.ops)
        if self.lease_id not in arb.leases:
            # v0.2：COMMIT 被信念隔离拦截，租约已被强制收回。
            # 恢复路径：重新观察 -> 以当前真实状态自报信念 -> 判 ok 即解除隔离。
            self.lease_id = None
            print(f"  [{self.id}] COMMIT 被信念校验拦截 -> 重新观察并申请"
                  if self.replans_left > 0 else
                  f"  [{self.id}] COMMIT 被信念校验拦截 -> 重规划余额耗尽")
            if self.replans_left > 0:
                self.replans_left -= 1
                self.sim.schedule(self.sim.now + 1, self._submit)
            return
        failed = [e for e in arb.log if e[0] == "op" and e[2] == self.lease_id and not e[5]]
        arb.release(self.lease_id)
        self.report.completed_at = self.sim.now
        # L5 伯努利证据：本次授予的计划是否过期（供 threshold 判据更新后验）
        self.post_n += 1
        if stale_at_commit:
            self.post_k += 1
        if self.sub_policy == "threshold" and self.post_n >= 3:
            p_hat = (self.post_k + 1) / (self.post_n + 2)
            if p_hat < self.p_star:
                self.sub_policy = "never"
                self.subscribed = False
                print(f"  [{self.id}] L5 阈值判据：p_hat={p_hat:.2f} < "
                      f"p*={self.p_star:.2f} -> 退订（低波动 key 不值得订阅）")
        if failed:
            self.reactive_replans += 1
            print(f"  [{self.id}] {len(failed)} 个算子失败: {[f[3] for f in failed]}"
                  f" -> {'重规划' if self.replans_left > 0 else '重规划余额耗尽'}")
            if self.replans_left > 0:
                self.replans_left -= 1
                self.sim.schedule(self.sim.now + 1, self._submit)


class NoProtocolAgent(LLMAgent):
    """无协议基线：不做 INTENT/租约，计划完成后逐 tick 直接执行算子。

    用于 E1 对比：同等 LLM 规划能力下，缺协议时的浪费工作（失败算子）
    与完成表现。执行逐 tick 摊开，制造与协议版相同长度的行动窗口。
    """

    def _submit(self) -> None:
        plan = self._plan(self._observe())
        self.ops = plan["ops"]
        self.keys = plan["keys"]
        for i, op in enumerate(self.ops):
            self.sim.schedule(self.sim.now + 2 + i, lambda op=op: self._exec_op(op))

    def _exec_op(self, op: tuple) -> None:
        arb = self.sim.arbiter
        lease = Lease("free", self.id, (), 10 ** 9, 0, 0)   # 伪句柄：仅传递 id
        handler = arb.ops_registry[op[0]]
        ok, detail = handler(arb, lease, op[1])
        arb.log.append(("op_free", arb._next_seq(), "free", op[0], op[1], ok, detail))

    def on_msg(self, msg) -> None:
        pass   # 无协议：不参与消息交互
