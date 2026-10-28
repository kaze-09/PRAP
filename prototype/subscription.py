"""L5 观察经济学：Beta-Bernoulli 后验订阅决策（E3 校准曲线的决策层）。

判据（规范 §3.6）：订阅当且仅当
    p_hat * L_miss > C_sub + p_hat * C_recheck
  即 p_hat > p* = C_sub / (L_miss - C_recheck)（盈亏平衡阈值）。

两种决策模式：
  mean        后验均值 > p*          —— 激进：证据少时易误订阅
  lower_bound 后验均值 - z*标准差 > p* —— 保守：证据不足时自动要求更多观察
              （Beta 后验的高斯近似；这正是"观察到多少次才敢"的来源：
               证据越少、下界越低，决策越保守）

确定性：给定 (n, k, 参数) 结果唯一；随机性仅来自外部观测流（由调用方固定种子）。
"""

from __future__ import annotations

import math

DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 1.0


def posterior(n: int, k: int, alpha: float = DEFAULT_ALPHA,
              beta: float = DEFAULT_BETA) -> tuple[float, float]:
    """Beta-Bernoulli 后验的均值与标准差（高斯近似）。"""
    total = n + alpha + beta
    mean = (k + alpha) / total
    var = (k + alpha) * (beta + n - k) / (total * total * (total + 1))
    return mean, var ** 0.5


def breakeven(L_miss: float, C_sub: float, C_recheck: float) -> float | None:
    """盈亏平衡阈值 p*。L_miss <= C_recheck 时不等式恒成立 -> 无条件订阅（返回 None）。"""
    denom = L_miss - C_recheck
    if denom <= 0:
        return None
    return C_sub / denom


def decide(n: int, k: int, L_miss: float, C_sub: float, C_recheck: float,
           mode: str = "lower_bound", z: float = 1.0,
           alpha: float = DEFAULT_ALPHA, beta: float = DEFAULT_BETA) -> tuple[bool, dict]:
    """订阅决策。返回 (decision, diagnostics)。"""
    mean, sd = posterior(n, k, alpha, beta)
    p_star = breakeven(L_miss, C_sub, C_recheck)
    if p_star is None:
        return True, {"mean": mean, "sd": sd, "p_star": None, "mode": mode}
    score = mean if mode == "mean" else mean - z * sd
    return score > p_star, {"mean": mean, "sd": sd, "p_star": p_star,
                            "score": score, "mode": mode}


def oracle(p_true: float, L_miss: float, C_sub: float, C_recheck: float) -> bool:
    """全知决策（已知真实变化率 p_true 时的最优选择）。"""
    denom = L_miss - C_recheck
    if denom <= 0:
        return True
    return p_true * denom > C_sub


def regret(p_true: float, L_miss: float, C_sub: float, C_recheck: float,
           decision: bool) -> float:
    """单次决策的后悔值：与全知决策不一致时的额外代价。"""
    gain = p_true * (L_miss - C_recheck) - C_sub   # 订阅的净收益
    if decision != (gain > 0):
        return abs(gain)
    return 0.0


# ---------- E3b（v0.2.3）：刷新经济学（P1-3 消融发现的代价模型修正） ----------

def plan_fail(len_ops: float, len0: float = 9.0, s: float = 1.5) -> float:
    """计划级失败概率：长度依赖的 logistic 模型（参数由 L5 消融实测粗标定）。

    注意 p_plan 不是逐算子独立复合——qwen 的退化计划（斜向压缩、ops=2）是
    计划级的潜变量事件，长度只是其最强相关因子（越长越易含缺陷步）。
    """
    return 1.0 / (1.0 + math.exp(-(len_ops - len0) / s))


def refresh_economics(len_full: float, len_red: float, L_fail: float = 10.0,
                      len0: float = 9.0, s: float = 1.5) -> dict:
    """E3b：检测到计划过期后，"主动刷新" vs "被动修复"的期望代价比较（闭合形式）。

      E[refresh]  = C_replan + P_fail(len_full) × L_fail   （从远处重采样，计划更长）
      E[reactive] = C_waste  + P_fail(len_red)  × L_fail   （确定性浪费 C_waste，
                                                            但浪费移动使位置更近、
                                                            恢复计划更短）
    两条路径各重采样一次 LLM，C_replan 相消——刷新的净收益**只**取决于：
      更长的恢复计划带来的额外失败风险 是否超过 浪费代价 C_waste。

    刷新划算 ⟺ C_waste > L_fail × (P_fail(len_full) − P_fail(len_red))

    两条边界推论（均可检验）：
      - 规划器极可靠（len0 很大）→ P_full ≈ P_red ≈ 0 → 刷新不划算（浪费不值一提）
      - 规划器极不可靠（len0 很小）→ 两个计划都几乎必败 → 刷新同样不划算
      即：**刷新只在中间可靠性区间 + 高浪费代价时划算**（相变结构）。
    """
    pf_full = plan_fail(len_full, len0, s)
    pf_red = plan_fail(len_red, len0, s)
    threshold = L_fail * (pf_full - pf_red)
    return {"p_fail_full": pf_full, "p_fail_red": pf_red,
            "c_waste_breakeven": threshold}
