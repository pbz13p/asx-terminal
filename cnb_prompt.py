"""Prompt and tidy-up for the Chinese one-line announcement summaries, shared by cn_brief.py (nightly, GitHub Actions)
and annsvc.brief() (on demand on Frank's PC)."""
import re

PROMPT = """下面是一家澳洲上市小公司的价格敏感公告（标题加正文开头）。用简体中文写一到两句大白话，只写公告里的事实：
发生了什么、关键数字（金额、价格、折让、股数、品位、期限、现金）、有没有附带条件或还没落实的部分。
规则：
- 只写事实，不评价好坏，不用“利好”“利空”“值得关注”“大涨”“前景”这类词，不预测股价，不给任何买卖建议。
- 币种照原文（A$ / $ 不写明时按澳元；US$ 才是美元），数字照原文，并换算成中文单位再写：1 million（m、M）＝100 万，1 billion（bn、B）＝10 亿，thousand（k、'000）＝千。
  例：US$17.8 million＝1780 万美元；A$50m＝5000 万澳元；US$840m＝8.4 亿美元；116.4Mt＝1.164 亿吨；0.47 cents＝0.47 分（不是 0.47 澳元）。不要留 million、m、bn 这类英文单位。
- 原文没有的数字不要补；分清"分"（cent）和"澳元"。
- franked 写成"全额抵税（franked）"；化学式、品位照写（如 Li2O、Au），不要用 LaTeX 或别的语言的文字。
- 如果公告只是会议演示文稿、免责声明、文件封面，正文开头没有新的事实数字，就只写一句：某某公司发布了某某文件，没有新的数字。
- 公司名、项目名保留英文原名，不要翻译。
- 不超过 80 个汉字，超了就删掉次要数字。直接输出这一两句话，不解释你怎么写的，不加引号、标题或说明。

标题：{headline}
正文开头：
{text}"""


def clean(zh):
    """Tidy one Chinese line: one line, no quotes, no LaTeX, no stray scripts (Arabic etc. seen once), no bare 'million'."""
    zh = re.sub(r"\s+", " ", zh or "").strip().strip("“”\"")
    zh = re.sub(r"\$\\text\{([^}]*)\}_(\d)\\text\{([^}]*)\}\$", r"\1\2\3", zh)          # $\text{Li}_2\text{O}$ -> Li2O
    zh = re.sub(r"[$\\]", "", zh) if "\\" in zh else zh
    zh = re.sub(r"[\u0590-\u08ff]+", "", zh)                                                 # Hebrew/Arabic letters
    m = re.search(r"(按规则|只写一句|规则要求)[^：:]{0,20}[：:]\s*", zh)            # model explaining itself first
    if m:
        zh = zh[m.end():]
    return zh.strip()
