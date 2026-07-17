# ch01: 基础类型层 总结

> 完成日期：2026-06-15
> 文件：mewcode/tools/base.py

## 1. 本章做了什么

定义了整个项目的**类型基石**——所有模块共享的基础类型和抽象接口：
- 2 个常量（SKIP_DIRS、MAX_OUTPUT_CHARS）
- 1 个类型别名（ToolCategory）
- 1 个结果数据类（ToolResult）
- 1 个抽象基类（Tool）
- 7 个流式事件数据类（TextDelta → StreamEnd）
- 1 个联合类型（StreamEvent）

## 2. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 事件类型用 dataclass 还是 BaseModel | dataclass | 热路径上每秒数千个事件，dataclass 创建开销 ~0.2μs vs BaseModel ~50μs |
| Tool 用 dataclass 还是普通类 | 普通类 + ABC | 有抽象方法 execute()，这是行为约束而非纯数据 |
| 事件分发用 isinstance 还是字符串 | isinstance | 类型安全，IDE 可推断，mypy 可做穷尽性检查 |
| 为什么需要 ToolCallStart/Delta/Complete 三种事件 | Anthropic SSE 协议就是三阶段 | Start=块开始(名称+ID)，Delta=增量JSON，Complete=解析完成 |

## 3. 文件结构

```
mewcode/tools/base.py
├── 导入区        # from __future__ import annotations, abc, dataclasses, pydantic
├── 常量          # SKIP_DIRS, MAX_OUTPUT_CHARS, ToolCategory
├── ToolResult    # 工具执行的统一返回值
├── Tool (ABC)    # 所有工具的抽象基类
├── 7 个流式事件   # TextDelta, ThinkingDelta/Complete, ToolCallStart/Delta/Complete, StreamEnd
└── StreamEvent   # 联合类型，供类型检查器做穷尽性检查
```

## 4. 依赖关系

```
本模块依赖：pydantic, abc, dataclasses, typing（全是标准库 + pydantic）
被依赖：client.py, agent.py, 所有具体工具实现
```

## 5. 与 learn-claude-code 的对比

learn-claude-code 没有独立的类型层——每个教学章节的类型定义散落在各自的 code.py 中。mewcode 作为完整项目，把所有共享类型集中到 base.py，好处是：
- 改一个类型定义，所有使用方自动同步
- 避免不同模块对同一概念有不同实现
- 代码审查时类型定义是单一事实来源

## 6. 下一步

ch02: 配置与缓存 — config.py + cache.py
