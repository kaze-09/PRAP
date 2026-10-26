# PRAP: Preemption & Resource Arbitration Protocol for Multi-LLM-Agent Systems

多 LLM 智能体共享互斥资源时的协调协议：**不打架（互斥）、不卡死（TTL 租约）、能发现自己想错了（信念校验）、坏信念不产生作用（信念隔离）、观察按需计价（订阅经济学）**。

核心洞察：把游戏状态同步领域 30 年的成熟技术（权威服务器、客户端预测+和解、兴趣管理）迁移到多 agent 协作，并补上游戏栈里不存在、LLM 时代必需的部件——信念校验（游戏客户端不会幻觉，LLM agent 会）。

## 当前状态（2026-10-26）

- 协议规范 v0.2（消息定义、状态机、正确性论证提纲、信念隔离）
- 仲裁器原型 + 网格测试床（纯 Python 标准库，虚拟时钟，确定性可重放）
- 真实 LLM（Qwen / DeepSeek）批次实验通过：互斥零泄漏、TTL 恢复 10/10、L4 检测+隔离 36/36
- 详见 [实验记录](PRAP_experiment_log.md)

## 仓库结构

```
├── PRAP_protocol_spec.md               协议规范 v0.2
├── PRAP_experiment_log.md              实验记录（台账）
├── PRAP_feasibility_novelty_analysis.md 可行性与创新性调研（文献截至 2026-10）
├── PRAP_paper_reading_notes.md         CoAgent / DPBench 精读笔记
├── PRAP_advisor_briefing.md            面向导师的项目说明
└── prototype/                          可运行原型
    ├── prap_core.py                    仲裁器（L0 权威状态 / L1 租约 / L2 仲裁 / L3 和解 / L4 校验+隔离 / L5 订阅）
    ├── testbed.py                      5x5 网格测试床 + 脚本 agent + 离散事件仿真
    ├── llm_agent.py                    真实 LLM agent 适配器（OpenAI 兼容，零依赖）
    ├── run_e1.py                       协议回归（24 断言，无 API）
    ├── run_e1_full.py / merge_e1.py    批次实验与合并汇总
    ├── run_e2.py                       信念校验专项实验
    ├── analyze_planning.py             规划可靠性分析（分模型 + Fisher 检验）
    ├── run_llm_smoke.py                LLM 冒烟实验
    └── *.json                          原始实验数据
```

## 快速开始

```powershell
cd prototype
python run_e1.py                # 协议回归（无网络，秒级）
python run_llm_smoke.py --stuck # 真实 LLM 冒烟（需先配置 api_keys.json）
python run_e1_full.py --trials 10   # 批次实验
python run_e2.py                # 信念校验专项
python analyze_planning.py      # 规划可靠性分析（离线）
```

LLM 实验需在 `prototype/api_keys.json` 填入 Qwen（DashScope）与 DeepSeek 的 key，或设环境变量 `QWEN_API_KEY` / `DEEPSEEK_API_KEY`。**该文件已被 .gitignore 排除，切勿提交。**

## 文档导航

- 想快速理解 idea → 读 [`PRAP_advisor_briefing.md`](PRAP_advisor_briefing.md)（含游戏同步迁移叙事与真实实验数据）
- 想实现或扩展协议 → 读 [`PRAP_protocol_spec.md`](PRAP_protocol_spec.md)
- 想了解实验证据 → 读 [`PRAP_experiment_log.md`](PRAP_experiment_log.md)
- 想了解文献定位 → 读 [`PRAP_feasibility_novelty_analysis.md`](PRAP_feasibility_novelty_analysis.md)

## 投稿规划

- 短文占坑：NeurIPS/ICLR workshop（2026-12 前后）
- 正稿：AAMAS / ICLR 2028
- 与 Collab-Overcooked 主线（ECAI 2027）技术解耦、互引留作可选策略
