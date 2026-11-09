# ASX Neon Terminal

个人用的 ASX 看盘软件。Python 3.10+，不用安装其他东西。

1. 复制 `.env.example` 为 `.env`，填入 `EODHD_API_KEY`。
2. 双击 `start.bat`。浏览器会打开 http://127.0.0.1:8765 。

数据来源在 `providers/`。网页只认 `providers/base.py` 规定的格式；
接盈透时实现 `providers/ibkr.py`，再把 `.env` 里的 `DATA_PROVIDER` 改成 `ibkr`。

盈透数据只限本人使用，不要把服务开放给别人。`.env` 不要上传。
