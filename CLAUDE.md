# asx-terminal：给 Claude 的长期规则

主人：pbz（Frank）。回复用中文，简洁、大白话；不加"非投资建议"之类的免责声明；每次报告 API 花费和 token 用量。

## 每做完一轮研究（必须做，不用问）
1. **更新研究中心**：编辑 `research/findings.json`，加或改一张卡片：
   - `verdict`：有效 / 一般 / 无效 / 工具
   - `rule`：一句话说清规则
   - `use`：一句话说怎么用
   - `numbers`：2–4 个关键数字，要扣过成本、带 t 值
   - `round`、`market`（ASX / A股）
   - `today`：如果有每日命中名单，填数据来源的键（如 rockets、vol_alerts、watch_alerts）；没有就填 null
   - 同时把文件顶部的 `updated` 改成当天日期
   - 在 `news` 列表**最前面**加一条大白话更新（日期、市场、一句话说新发现了什么）。爸爸在中国看网页时，研究中心按钮会亮红点，打开就能看到最新更新，不用另外截图解释
2. **加密后提交**：
   `python -c "import securedata;open('research/findings.json.enc','wb').write(securedata.encrypt(open('research/findings.json','rb').read()))"`
   （需要环境变量 ASX_DATA_KEY）。只提交 `.enc` 文件，`findings.json` 已在 .gitignore 里。
3. 如果新结论需要每天自动出一份名单，在 `study22.py`（每日扫描）里加输出，再在 `server.py` 的 `research()` 里把它接到卡片的 `today` 上。
4. 结论被新数据推翻时，把旧卡片改成"无效"并写明原因，不要删掉。测过无效的结论也要留着，这样以后不会重复测。

## 不能违反的规则
- 仓库是公开的：所有结果、数据、公告都要加密（securedata.py）。不要提交明文的 `report.html`、`findings.json`、`.env`、密钥。
- 所有网络下载都在 GitHub Actions 上跑（Claude 的工作环境连不上 asx.com.au 和政府网站）。
- AI 读公告的总预算是 40 美元，花到 35 美元就停。公告翻译按篇缓存，同一篇不收第二次钱。
- 回测必须包含退市股，并扣真实成本：佣金 + 一格价差；A 股另外处理涨跌停买不进、卖不出。
- 提交时作者名用 pbz13p 和主人的邮箱，提交信息里不要写 User-Agent 字符串。

## 数据永久保存（主人要求，不能丢）
- 每天自动扫一遍所有数据缓存（backup.yml），每周日加密备份到 GitHub Release「data-backup」。每次开工先看最近一次备份成功没有（Release 里的 LAST_BACKUP.txt）；失败了先修好再做别的。
- 新增任何数据源，都要同时加进 backup.yml。
- 实盘记录（out/track_*.json）只增不删。

## 界面
`web/index.html` 是 Neon Terminal 新版，采用 TradingView 式布局，暗色霓虹配色、干净实用；旧版是 `web/classic.html`。新功能都要遵守"傻瓜、方便"的原则：一次点击就能拿到结果，信息一目了然。

## 对标主流交易软件（主人的长期要求）
目标：一站式。技术面 + 基本面 + 公告 + 我们自己的研究，全放在一个终端里。
- 每次做终端功能前，先对照 TradingView、盈透 TWS、CommSec / IRESS，列出它们有、我们还没有的功能，按"对主人最有用、最省钱"的顺序排队，写进 ROADMAP.md 的"终端功能对标"一节，再一项项做。
- 数据只用免费或已付费、而且完整准确的来源：EODHD（已付费，不含基本面）、ASX 官网与 markitdigital 公司接口、巨潮资讯、公告里提取的数字、以后接上的盈透 API。要另外花钱的数据，先问主人。
- 基础功能做齐只是起点。重点是在这些数据上做我们自己的研究和信号，并通过研究中心展示出来。
