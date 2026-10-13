#!/usr/bin/env bash
# Livemap 服务器一键部署脚本（Ubuntu 24.04，轻量应用服务器 2C4G）
# 前提：代码已解压到 /opt/livemap，data/livemap.db 与 data/uploads 已就位，
#       /opt/livemap/.env 已就位（含 BAIDU_SERVER_AK / NEXT_PUBLIC_BAIDU_JS_AK）
set -euo pipefail
cd /opt/livemap

echo "== [1/6] 系统依赖 =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx nodejs npm

echo "== [2/6] Python 依赖（清华源） =="
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv/bin/pip install -r apps/api/requirements.txt -e packages/geo \
  -i https://pypi.tuna.tsinghua.edu.cn/simple

echo "== [3/6] 前端依赖（npmmirror） =="
cd apps/web
npm config set registry https://registry.npmmirror.com
npm install --no-audit --no-fund

echo "== [4/6] 前端构建（注入公网 API 地址与地图 AK） =="
# shellcheck disable=SC1091
set -a; source /opt/livemap/.env; set +a
export NEXT_PUBLIC_API_BASE="${NEXT_PUBLIC_API_BASE}"
export NEXT_PUBLIC_BAIDU_JS_AK="${NEXT_PUBLIC_BAIDU_JS_AK}"
export NODE_OPTIONS=--max-old-space-size=3072
npm run build
cd /opt/livemap

echo "== [5/6] systemd 服务 =="
cp deploy/livemap-api.service deploy/livemap-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now livemap-api
systemctl enable --now livemap-web

echo "== [6/6] nginx 反代（80 -> web 3000 / api 8000 同源） =="
cp deploy/nginx-livemap.conf /etc/nginx/sites-available/livemap
ln -sf /etc/nginx/sites-available/livemap /etc/nginx/sites-enabled/livemap
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "== 冒烟验证 =="
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS http://127.0.0.1:8000/health && echo
curl -fsS -o /dev/null -w "web http_code=%{http_code}\n" http://127.0.0.1:3000/
curl -fsS -o /dev/null -w "nginx / http_code=%{http_code}\n" http://127.0.0.1/
echo "部署完成。浏览器访问 http://123.57.41.32/"
