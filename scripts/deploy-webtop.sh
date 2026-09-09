#!/bin/bash
set -e

WEBTOP_DIR="/opt/webtop"
mkdir -p "$WEBTOP_DIR/config/custom-cont-init.d"
chmod -R 755 "$WEBTOP_DIR"

WEBTOP_USER="admin"
WEBTOP_PASS="admin321"
echo "$WEBTOP_PASS" > "$WEBTOP_DIR/.password"
chmod 600 "$WEBTOP_DIR/.password"

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
        xorgxrdp \
        libgdk-pixbuf2.0-bin \
        librsvg2-common
    locale-gen zh_CN.UTF-8
    /usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/gdk-pixbuf-query-loaders --update-cache 2>/dev/null || true
    touch /config/.cjk_installed
    echo "=== Chinese fonts and XRDP installed ==="
fi

# 2. Patch bubblewrap (bwrap) for unprivileged Docker container support (fixes GTK glycin icon loading)
if [ -f /usr/bin/bwrap ] && [ ! -f /usr/bin/bwrap.orig ]; then
    mv /usr/bin/bwrap /usr/bin/bwrap.orig
    cat << 'BWRAP_EOF' > /usr/bin/bwrap
#!/bin/bash
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
    cmd="${args[i]}"
    if [[ "$cmd" == /* ]] && [[ -f "$cmd" ]] && [[ -x "$cmd" ]] && [[ "$cmd" != *bwrap* ]]; then
        exec "${args[@]:i}"
    fi
done
exit 127
BWRAP_EOF
    chmod 755 /usr/bin/bwrap
fi

# 3. Sync system user admin and passwords for RDP
USER_NAME="${CUSTOM_USER:-admin}"
PASS_WORD="${PASSWORD:-admin321}"
if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd -m -s /bin/bash -g abc -G sudo,docker "$USER_NAME" 2>/dev/null || true
fi
echo "${USER_NAME}:${PASS_WORD}" | chpasswd
echo "abc:${PASS_WORD}" | chpasswd
echo "startxfce4" > "/home/${USER_NAME}/.xsession" 2>/dev/null || true

# Pre-populate desktop theme & settings if needed
if [ -d /config/.config ] && [ ! -d "/home/${USER_NAME}/.config/xfce4" ]; then
    mkdir -p "/home/${USER_NAME}/.config"
    cp -a /config/.config/* "/home/${USER_NAME}/.config/" 2>/dev/null || true
fi
chown -R "${USER_NAME}":abc "/home/${USER_NAME}" 2>/dev/null || true

# 4. Configure and start XRDP for RDP port 3389
echo "startxfce4" > /etc/skel/.xsession
echo "startxfce4" > /config/.xsession

# Fix socket permissions & session cleanup to prevent black screen
sed -i 's/^#SessionSockdirGroup=.*/SessionSockdirGroup=xrdp/' /etc/xrdp/sesman.ini 2>/dev/null || true
sed -i 's/^KillDisconnected=.*/KillDisconnected=true/' /etc/xrdp/sesman.ini 2>/dev/null || true
sed -i 's/^DisconnectedTimeLimit=.*/DisconnectedTimeLimit=5/' /etc/xrdp/sesman.ini 2>/dev/null || true
usermod -a -G root,ssl-cert xrdp 2>/dev/null || true

# Direct startwm.sh to ensure XFCE starts reliably without hanging
cat << 'STARTWM_EOF' > /etc/xrdp/startwm.sh
#!/bin/sh
if test -r /etc/profile; then
	. /etc/profile
fi
if test -r ~/.profile; then
	. ~/.profile
fi
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP=XFCE
export XDG_CONFIG_DIRS=/etc/xdg/xdg-xubuntu:/etc/xdg
export DESKTOP_SESSION=xubuntu
exec startxfce4
STARTWM_EOF
chmod 755 /etc/xrdp/startwm.sh

# Auto-login to Xorg using default or client-supplied credentials (completely eliminates secondary login screen)
sed -i 's/^autorun=.*/autorun=Xorg/' /etc/xrdp/xrdp.ini 2>/dev/null || true
sed -i "/\[Xorg\]/,/\[Xvnc\]/ s/^username=.*/username=${USER_NAME}/" /etc/xrdp/xrdp.ini 2>/dev/null || true
sed -i "/\[Xorg\]/,/\[Xvnc\]/ s/^password=.*/password=${PASS_WORD}/" /etc/xrdp/xrdp.ini 2>/dev/null || true

killall -9 xrdp xrdp-sesman 2>/dev/null || true
rm -f /var/run/xrdp/*.pid /run/xrdp/*.pid
service xrdp start 2>/dev/null || /etc/init.d/xrdp start 2>/dev/null || true
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
