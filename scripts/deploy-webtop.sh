#!/bin/bash
set -e

WEBTOP_DIR="/opt/webtop"
mkdir -p "$WEBTOP_DIR/config/custom-cont-init.d"
chmod -R 755 "$WEBTOP_DIR"

# Generate strong random password if not exists
PWD_FILE="$WEBTOP_DIR/.password"
if [ ! -f "$PWD_FILE" ]; then
    WEBTOP_PASS="WebTop@$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 8)!"
    echo "$WEBTOP_PASS" > "$PWD_FILE"
    chmod 600 "$PWD_FILE"
else
    WEBTOP_PASS=$(cat "$PWD_FILE")
fi

# 1. Chinese fonts and XRDP init script for the container
cat << 'INIT_EOF' > "$WEBTOP_DIR/config/custom-cont-init.d/01-chinese-fonts.sh"
#!/bin/bash
# 1. Install Chinese fonts & language packs if not already installed
if [ ! -f /config/.cjk_installed ]; then
    echo "=== Installing Chinese fonts & language packs ==="
    apt-get update
    apt-get install -y --no-install-recommends \
        fonts-wqy-zenhei \
        fonts-wqy-microhei \
        fonts-noto-cjk \
        language-pack-zh-hans \
        xrdp \
        xorgxrdp
    locale-gen zh_CN.UTF-8
    touch /config/.cjk_installed
    echo "=== Chinese fonts and XRDP installed ==="
fi

# 2. Configure and start XRDP for RDP port 3389
echo "startxfce4" > /etc/skel/.xsession
echo "startxfce4" > /config/.xsession
adduser xrdp ssl-cert 2>/dev/null || true
service xrdp restart 2>/dev/null || /etc/init.d/xrdp restart 2>/dev/null || true
INIT_EOF
chmod +x "$WEBTOP_DIR/config/custom-cont-init.d/01-chinese-fonts.sh"

# 2. Write docker-compose.yml
cat << COMPOSE_EOF > "$WEBTOP_DIR/docker-compose.yml"
version: "3.8"

services:
  webtop:
    image: lscr.io/linuxserver/webtop:ubuntu-xfce
    container_name: webtop
    restart: unless-stopped
    security_opt:
      - seccomp:unconfined
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Asia/Shanghai
      - SUBFOLDER=/
      - TITLE=Webtop Desktop
      - CUSTOM_USER=admin
      - PASSWORD=${WEBTOP_PASS}
    volumes:
      - ./config:/config
      - /var/run/docker.sock:/var/run/docker.sock
    ports:
      - "3000:3000"
      - "3001:3001"
      - "3389:3389"
    shm_size: "2gb"
    mem_limit: 4g
COMPOSE_EOF

# 3. Pull and start container
cd "$WEBTOP_DIR"
if docker compose version >/dev/null 2>&1; then
    docker compose up -d
else
    docker-compose up -d
fi

# 4. Report status
sleep 3
STATUS=$(docker ps --filter "name=webtop" --format "{{.Status}}")
echo "=== WEBTOP STATUS ==="
echo "Status: $STATUS"
echo "Username: admin"
echo "Password: $WEBTOP_PASS"
echo "Web URL (HTTP):  http://137.175.105.82:3000"
echo "Web URL (HTTPS): https://137.175.105.82:3001"
echo "RDP Address:     137.175.105.82:3389"
