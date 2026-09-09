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
        librsvg2-common \
        fcitx \
        fcitx-googlepinyin \
        fcitx-pinyin \
        fcitx-frontend-gtk3 \
        fcitx-frontend-gtk2 \
        fcitx-ui-classic
    locale-gen zh_CN.UTF-8
    /usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/gdk-pixbuf-query-loaders --update-cache 2>/dev/null || true
    echo "run_im fcitx" > /etc/X11/xinit/xinputrc 2>/dev/null || true
    cat << 'ENV_EOF' > /etc/profile.d/fcitx.sh
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
ENV_EOF
    mkdir -p /etc/xdg/autostart
    cp -f /usr/share/fcitx/xdg/autostart/fcitx-autostart.desktop /etc/xdg/autostart/ 2>/dev/null || true
    touch /config/.cjk_installed
    echo "=== Chinese fonts, XRDP, and Fcitx IME installed ==="
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

# Pre-populate desktop theme, settings & Fcitx IME config
for DIR in "/home/${USER_NAME}" "/config" "/etc/skel"; do
    mkdir -p "$DIR/.config/fcitx" "$DIR/.config/autostart"
    cat << 'FCITX_EOF' > "$DIR/.config/fcitx/profile"
[Profile]
DefaultIMName=fcitx-keyboard-us
ShareInputState=No
UsePreedit=True
ShowInputWindowAfterSwitching=True
ShowInputWindowWhenFocusIn=False
ShowInputWindowCompact=False

[Profile/Order]
0=fcitx-keyboard-us
1=googlepinyin

[Profile/TriggerKey]
0=CTRL_SPACE
1=CTRL_SHIFT
FCITX_EOF

    cat << 'HOTKEY_EOF' > "$DIR/.config/fcitx/config"
[Hotkey]
TriggerKey=CTRL_SPACE
SwitchKey=L_SHIFT
HOTKEY_EOF

    cat << 'XP_EOF' > "$DIR/.xprofile"
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
fcitx -d &
XP_EOF
    cp -f /usr/share/fcitx/xdg/autostart/fcitx-autostart.desktop "$DIR/.config/autostart/" 2>/dev/null || true
    echo "run_im fcitx" > "$DIR/.xinputrc" 2>/dev/null || true
done

if [ -d /config/.config ] && [ ! -d "/home/${USER_NAME}/.config/xfce4" ]; then
    cp -a /config/.config/* "/home/${USER_NAME}/.config/" 2>/dev/null || true
fi
chown -R "${USER_NAME}":abc "/home/${USER_NAME}" 2>/dev/null || true
chown -R abc:abc /config/.config /config/.xprofile /config/.xinputrc 2>/dev/null || true

# 4. Configure and start XRDP for RDP port 3389
echo "startxfce4" > /etc/skel/.xsession
echo "startxfce4" > /config/.xsession

# Fix socket permissions & session persistence (prevent sesman socket teardown on disconnect)
sed -i 's/^#SessionSockdirGroup=.*/SessionSockdirGroup=xrdp/' /etc/xrdp/sesman.ini 2>/dev/null || true
sed -i 's/^KillDisconnected=.*/KillDisconnected=false/' /etc/xrdp/sesman.ini 2>/dev/null || true
sed -i 's/^DisconnectedTimeLimit=.*/DisconnectedTimeLimit=0/' /etc/xrdp/sesman.ini 2>/dev/null || true
usermod -a -G root,ssl-cert xrdp 2>/dev/null || true

# Direct startwm.sh to ensure XFCE and IME start reliably
cat << 'STARTWM_EOF' > /etc/xrdp/startwm.sh
#!/bin/sh
if test -r /etc/profile; then
	. /etc/profile
fi
if test -r ~/.profile; then
	. ~/.profile
fi
if test -r ~/.xprofile; then
	. ~/.xprofile
fi
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP=XFCE
export XDG_CONFIG_DIRS=/etc/xdg/xdg-xubuntu:/etc/xdg
export DESKTOP_SESSION=xubuntu
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx

if ! pgrep -u $(id -u) fcitx >/dev/null 2>&1; then
    fcitx -d &
fi

exec startxfce4
STARTWM_EOF
chmod 755 /etc/xrdp/startwm.sh

# Auto-login to Xorg using default or client-supplied credentials (completely eliminates secondary login screen)
sed -i 's/^autorun=.*/autorun=Xorg/' /etc/xrdp/xrdp.ini 2>/dev/null || true
sed -i "/\[Xorg\]/,/\[Xvnc\]/ s/^username=.*/username=${USER_NAME}/" /etc/xrdp/xrdp.ini 2>/dev/null || true
sed -i "/\[Xorg\]/,/\[Xvnc\]/ s/^password=.*/password=${PASS_WORD}/" /etc/xrdp/xrdp.ini 2>/dev/null || true

# Install & start XRDP auto-healing watchdog
cat << 'WATCHDOG_EOF' > /usr/local/bin/xrdp-watchdog.sh
#!/bin/bash
while true; do
    sleep 3
    if ! pgrep -x xrdp-sesman >/dev/null 2>&1 || [ ! -S /run/xrdp/sockdir/sesman.socket ]; then
        echo "[xrdp-watchdog] $(date): sesman is not healthy, reviving..." >> /var/log/xrdp-watchdog.log
        killall -9 xrdp xrdp-sesman 2>/dev/null || true
        rm -rf /run/xrdp /var/run/xrdp
        /etc/init.d/xrdp start 2>/dev/null || true
        sleep 2
        continue
    fi
    if ! pgrep -x xrdp >/dev/null 2>&1 || ! ss -tlpn | grep -q ':3389 '; then
        echo "[xrdp-watchdog] $(date): xrdp is not listening on 3389, restarting..." >> /var/log/xrdp-watchdog.log
        killall -9 xrdp 2>/dev/null || true
        service xrdp start 2>/dev/null || /etc/init.d/xrdp start 2>/dev/null || true
        sleep 2
        continue
    fi
done
WATCHDOG_EOF
chmod +x /usr/local/bin/xrdp-watchdog.sh

killall -9 xrdp xrdp-sesman 2>/dev/null || true
pkill -f xrdp-watchdog.sh 2>/dev/null || true
rm -f /var/run/xrdp/*.pid /run/xrdp/*.pid
service xrdp start 2>/dev/null || /etc/init.d/xrdp start 2>/dev/null || true
nohup /usr/local/bin/xrdp-watchdog.sh >/dev/null 2>&1 &
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
