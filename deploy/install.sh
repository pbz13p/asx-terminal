#!/usr/bin/env bash
# Neon Terminal: one-time install on a fresh Ubuntu server (e.g. Tencent Cloud Lighthouse, Hong Kong).
# Run in the server's web console:
#   curl -fsSL https://raw.githubusercontent.com/pbz13p/asx-terminal/main/deploy/install.sh | sudo bash
# It asks for the keys itself (you paste them; they stay only on this server), installs everything,
# starts the site on port 80 and keeps it updated from GitHub automatically.
set -euo pipefail
DIR=/opt/neon
echo "== 1/4 安装需要的软件"
apt-get update -qq
apt-get install -y -qq python3 python3-pip git ca-certificates >/dev/null
pip3 install -q --break-system-packages pypdf 2>/dev/null || pip3 install -q pypdf
echo "== 2/4 下载 Neon Terminal"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q --depth 1 https://github.com/pbz13p/asx-terminal.git "$DIR"; fi
id neon >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin neon
echo "== 3/4 填写密钥和登录账号（输入时屏幕不显示，粘贴后直接回车）"
ask() { local v; read -r -s -p "$1: " v < /dev/tty; echo; printf '%s' "$v"; }
if [ ! -f "$DIR/.env" ]; then
  EOD=$(ask "EODHD_API_KEY")
  KEY=$(ask "ASX_DATA_KEY（数据密钥）")
  read -r -p "你的用户名（例如 frank）: " U1 < /dev/tty
  P1=$(ask "你的密码")
  read -r -p "爸爸的用户名（例如 baba）: " U2 < /dev/tty
  P2=$(ask "爸爸的密码")
  ANT=$(ask "ANTHROPIC_API_KEY（公告 AI 翻译用，没有就直接回车）")
  umask 077
  cat > "$DIR/.env" <<ENV
EODHD_API_KEY=$EOD
ASX_DATA_KEY=$KEY
SITE_USERS=$U1:$P1,$U2:$P2
SITE_SECRET=$(head -c 32 /dev/urandom | base64 | tr -d '/+=')
ANTHROPIC_API_KEY=$ANT
ENV
fi
chown -R neon:neon "$DIR"
chmod 600 "$DIR/.env"
echo "== 4/4 设置开机自动启动"
cat > /etc/systemd/system/neon.service <<UNIT
[Unit]
Description=Neon Terminal
After=network-online.target
[Service]
User=neon
WorkingDirectory=$DIR
Environment=NO_BROWSER=1 HOST=0.0.0.0 PORT=80 PYTHONUNBUFFERED=1
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStartPre=/usr/bin/python3 $DIR/update.py
ExecStart=/usr/bin/python3 $DIR/server.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q --now neon
sleep 3
IP=$(curl -fsS -4 --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')
if systemctl is-active -q neon; then
  echo
  echo "✅ 装好了。在浏览器打开:  http://$IP"
  echo "   （如果打不开：在腾讯云控制台 → 这台服务器 → 防火墙，确认 80 端口是开放的）"
else
  echo "❌ 没有启动成功，把下面几行截图发给 Claude："; journalctl -u neon -n 20 --no-pager
fi
