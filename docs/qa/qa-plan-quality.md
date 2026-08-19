# 规划智能体的规划质量如何保证

> 日期：2026-08-06
> 类型：多Agent架构答疑

## 问题原文

"针对这个项目的多智能体环节，怎么保证规划智能体规划的任务效果"

## 核心答案

无独立的计划自动评估机制，质量靠四层"约束+把关"叠加，越往下越硬，最硬的是用户审批门：

1. **Plan agent 自身硬约束**：只读（禁 EditFile/WriteFile/Agent）、maxTurns 15、强制列出 3-5 个关键文件路径
2. **Plan mode 五阶段编排**：Explore 并行 → Plan agent 产出 → **主 agent 亲自 review 对齐意图** → 写最终 plan（Context/方案/关键文件/验证）→ ExitPlanMode（无 plan 文件不放行）
3. **用户审批门**（plan_dialog.py）：YOLO / MANUAL / FEEDBACK 三选，人不满意不执行
4. **下游验证（间接）**：独立只读 Verification agent 验收实现，反推计划问题（滞后）

## 关键代码/设计决策

- `mewcode/agents/builtins/plan.md`：只读、maxTurns 15、强制关键文件路径
- `mewcode/prompts.py:168`：Plan mode 五阶段提示语，Review 阶段主 agent 亲自读关键文件
- `mewcode/plan_dialog.py`：内联计划审批组件（PlanChoice: yolo/manual/feedback）
- `mewcode/tools/exit_plan_mode.py:42`：plan 文件不存在则拒绝退出（结构性闸）
- 设计哲学：理解权不外包——Plan agent 是输入方，主 agent 是理解者+决策者

## 面试考点

1. 规划质量保障的本质：**prompt 约束（软）vs 机制保证（硬）** 的区分
2. 把"质量判断"押在用户审批门上是省复杂度的务实取舍
3. 生产级补强方向：计划结构校验器、planner↔reviewer 对抗、结构化计划（YAML/JSON）+ 程序校验
4. 项目中 mew-spec（spec→plan→task→checklist）是结构化计划的 harness 级实践

## 涉及文件

- `mewcode/agents/builtins/plan.md`
- `mewcode/prompts.py`（_PLAN_MODE_FULL_REMINDER）
- `mewcode/plan_dialog.py`
- `mewcode/tools/exit_plan_mode.py`
- `mewcode/teams/coordinator.py`（synthesis 哲学）
