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
