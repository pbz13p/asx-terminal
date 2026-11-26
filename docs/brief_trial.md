# 状态快照试验（2026-11-26 开始，2026-12-04 对比）

Frank 2026-11-26 同意（"你看着办吧"）：5 个定时任务的读文件步骤改成先读 docs/brief.md。
一周后对比每个任务的「用量：」行（改前一周 vs 改后一周）和产出质量。省不到 40% 或质量变差 → 把下面的原句改回去。

## 改动前的原句（恢复用，其余提示词未动）

- 每日三条厉害指令（trig_016RSuCEA1XQF2RHpWqhMShp）【质量第一】第 3 条原为：「先按下面的方式只读需要的部分（用 grep、head、sed 截取，能合并成一次的命令就合并）；读完如果判断不清……」。第 1 步原为：「先读这些：howzu ROADMAP.md 的「排序表」和「待 Frank 处理」各条标题和状态；asx-terminal ROADMAP.md 的二级标题和每节开头几行、「待 Frank 处理」、docs/progress.md 最后约 40 行、research/quant_watch.md 最近一周那一节；study*.py 编号最大的 5 个文件的文件头（第几轮研究了什么、结论是什么）。当天打算给哪个项目写指令，就把那个项目相关的章节、代码文件头读全。」
- 总参谋每日汇报邮件（trig_01C6Vf3cA6ET5EtvtSSGaPX5）第 2 步原为：「读今天（墨尔本时间）的记录：howzu 的 .auto-improve/LOG.md 和 METRICS.md；melb-property 的 docs/progress.md 和 docs/blockers.md（Microburbs 花费）；asx-terminal 的 docs/progress.md。」4b 来源原为：「howzu 的 .auto-improve/LOG.md 和 .auto-improve/DEMAND.md、melb-property 的 docs/progress.md、asx-terminal 的 docs/progress.md 和 research/quant_watch.md、earwise 的 METRICS.md……」
- 股票研究员（trig_01X347JzTHHirL5W3xzrYRvW）第 1 步原为：「读 ROADMAP.md 和 docs/progress.md；没有就先建：……」。1.5c 原为：「先对照 ROADMAP 各轮、research/quant_watch.md、research/creators/……」
- 好租产品增长组（trig_01T5cRh4kFW9NNwPQFjGHSza）第 1 步原为：「读 ROADMAP.md、METRICS.md、.github/auto-improve/goals.md（差距清单）、.auto-improve/DEMAND.md（租客需求周报）。ROADMAP.md 或 METRICS.md 不存在就先建：……」
- 量化方法每周追踪（trig_01Lsqk31Fpf6mmp77afMoc3K）第 1 步原为：「读 CLAUDE.md（规则必须遵守）、research/quant_watch.md（跟踪名单和以往记录，不要重复收录）、ROADMAP.md、factors/catalog.json（因子库，已有的不再加）。」

## 改前一周的用量基线（来自各仓库「用量：」行）

- 好租产品增长组：11/19 6,510,000；11/22 未能获取；11/25 3,854,172
- 股票研究员 / 股票事业部：11/20、11/22 未能获取；11/25 2,304,672
- 量化方法每周追踪：11/22 未能获取
- 每日三条：11/24 约 729 万（任务提示词里记的）
- 总参谋：日报邮件里有，待 12/04 从 Gmail 取
基线里「未能获取」多，对比时只用两边都有真实数字的任务；不够就直说"样本不够，再看一周"。

## LOG 归档
.auto-improve/LOG.md 最早一条是 2026-11-07，还没有 30 天前的条目，暂不归档；12 月初再看。
