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

## 界面
`web/index.html` 是 Neon Terminal 新版，采用 TradingView 式布局，暗色霓虹配色、干净实用；旧版是 `web/classic.html`。新功能都要遵守"傻瓜、方便"的原则：一次点击就能拿到结果，信息一目了然。
