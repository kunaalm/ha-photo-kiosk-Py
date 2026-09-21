#!/bin/bash
# HA Photo Kiosk — installer.
#
# Turns a Debian-based box into a two-state kiosk in one command:
#   * creates a local 'kiosk' user
#   * puts EVERY kiosk thing under that user's home (~kiosk)
#   * installs a systemd unit that boots Chromium at the kiosk page
#
# Pipeable straight from GitHub — no clone needed:
#
#     curl -fsSL https://raw.githubusercontent.com/kunaalm/ha-photo-kiosk-Py/main/scripts/install.sh | sudo bash
#
# (Inspect it first if you like: curl that URL, read the file, then run it.)
#
# Default install: pulls the published engine container from GHCR (no local
# build, no clone). The engine (smarts) runs in the container; the supervisor
# (browser + display) runs on the host.
#
# Alternative: `--from-source` clones the repo and installs the engine as a
# Python venv — for hacking on the code or boxes without Docker.
#
# Either way, after install you are directed to the WEB CONFIG SERVICE to
# complete setup — set your HA URL, photo source, and timings there, not via
# this script.
set -eu

# Never let apt/debconf block on an interactive prompt (keyboard-config,
# tzdata, etc.) — this is a one-command non-interactive install.
export DEBIAN_FRONTEND=noninteractive

# --- Config (env-overridable) -------------------------------------------
KIOSK_USER="${KIOSK_USER:-kiosk}"
KIOSK_HOME="$(getent passwd "$KIOSK_USER" | cut -d: -f6)"
[ -n "$KIOSK_HOME" ] || KIOSK_HOME="/home/$KIOSK_USER"
ENGINE_PORT="${ENGINE_PORT:-8080}"
PHOTO_HOST_DIR="${PHOTO_HOST_DIR:-$KIOSK_HOME/photos}"
CONFIG_HOST_DIR="${CONFIG_HOST_DIR:-$KIOSK_HOME/config}"
ENGINE_SRC_DIR="${ENGINE_SRC_DIR:-$KIOSK_HOME/engine}"
INSTALL_DIR="${INSTALL_DIR:-$KIOSK_HOME/install-files}"
SUPERVISOR_BIN="${SUPERVISOR_BIN:-$KIOSK_HOME/bin/kiosk-supervisor.sh}"
GP_SYNC_BIN="${GP_SYNC_BIN:-$KIOSK_HOME/bin/kiosk-gphotos-sync.sh}"
INSTALL_RCLONE="${INSTALL_RCLONE:-1}"   # 1 = install rclone for Google Photos sync
IMAGE="${IMAGE:-ghcr.io/kunaalm/ha-photo-kiosk-py:latest}"
# Where the installer pulls companion files from (a tag, not main, for pinning).
REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/kunaalm/ha-photo-kiosk-Py/main}"
REPO_GIT="https://github.com/kunaalm/ha-photo-kiosk-Py.git"
MODE="container"   # container | source

log() { echo "[kiosk-install] $*"; }
die() { echo "[kiosk-install] ERROR: $*" >&2; exit 1; }

# --- Progress bar --------------------------------------------------------
# Renders an overall install progress line before each phase. Only draws when
# stdout is a TTY (a curl|bash pipe has no terminal, so it stays quiet and
# just logs). Usage: progress <current> <total> <label>
PROGRESS_TOTAL=11
progress() {
    [ -t 1 ] || return 0
    local cur="$1" total="$2" label="$3"
    local pct=$((cur * 100 / total))
    local filled=$((pct / 5))          # 20 chars wide
    local empty=$((20 - filled))
    local bar
    bar="$(printf '%*s' "$filled" '' | tr ' ' '=')$(printf '%*s' "$empty" '' | tr ' ' '-')"
    printf "${CYAN}[%s]${NC} ${BOLD}%3d%%${NC} %s\n" "$bar" "$pct" "$label"
}

## UI COLORS (ASCII-safe, no unicode box-drawing — keeps terminal
## compatibility across serial consoles / minimal TTYs). Disabled
## automatically when stdout isn't a terminal (e.g. piped/logged output).
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; NC=''
fi

# --- CLI ----------------------------------------------------------------
parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --from-source) MODE="source" ;;
            --help|-h) echo "Usage: install.sh [--from-source]"; exit 0 ;;
            *) die "unknown option: $1 (try --help)" ;;
        esac
        shift
    done
}

# --- Helpers ------------------------------------------------------------
have_curl() { command -v curl >/dev/null 2>&1; }
have_wget() { command -v wget >/dev/null 2>&1; }

# Fetch a companion file from the repo into the install dir.
fetch() {
    # fetch <repo-relative-path> -> $INSTALL_DIR/<basename>
    local rel="$1"
    mkdir -p "$INSTALL_DIR"
    if have_curl; then
        curl -fsSL "$REPO_RAW/$rel" -o "$INSTALL_DIR/$(basename "$rel")" || die "could not download $rel."
    elif have_wget; then
        wget -q "$REPO_RAW/$rel" -O "$INSTALL_DIR/$(basename "$rel")" || die "could not download $rel."
    else
        die "need curl or wget to download companion files."
    fi
}

# --- 1. Prerequisites ----------------------------------------------------
# Detect the Debian Buster/CD codename for picking the right Docker apt repo.
debian_codename() {
    local id
    if [ -r /etc/os-release ]; then
        id="$(. /etc/os-release && echo "$VERSION_CODENAME")"
        [ -n "$id" ] && echo "$id" && return 0
    fi
    echo "bookworm"   # sane default; fallback below via lsb_release
}

# Install Docker Engine (official apt repo) if not already present.
install_docker() {
    if command -v docker >/dev/null 2>&1; then
        if docker info >/dev/null 2>&1; then
            return 0
        fi
        # Binary present but daemon not running — try to start it first.
        log "Docker found but daemon not running — starting it..."
        systemctl enable --now docker >/dev/null 2>&1 || true
        sleep 2
        docker info >/dev/null 2>&1 && return 0
    fi
    log "Docker not found — installing Docker Engine (official repo)..."
    command -v apt-get >/dev/null 2>&1 || die "neither Docker nor apt-get found; install Docker manually, or use: bash install.sh --from-source"
    # We must be root to configure apt (we already checked at install start).
    local arch code
    arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
    code="$(debian_codename)"
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq ca-certificates curl gnupg >/dev/null 2>&1 \
        || die "could not install apt prerequisites (ca-certificates/curl/gnupg)."
    # Add Docker's official GPG key + apt repo.
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/debian/gpg" \
        -o /etc/apt/keyrings/docker.asc || die "could not fetch Docker GPG key."
    echo "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $code stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq >/dev/null 2>&1 || die "apt update failed after adding Docker repo."
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
        >/dev/null 2>&1 || die "could not install Docker packages (check that '${code}' is a valid Debian codename)."
    systemctl enable --now docker >/dev/null 2>&1 || true
    sleep 2
    command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 \
        || die "Docker installed but not usable; check 'docker info'."
    log "Docker Engine installed + running."
}

check_prereqs() {
    [ "$(id -u)" -eq 0 ] || die "run as root (sudo)."
    command -v systemctl >/dev/null 2>&1 || die "systemd not found (systemctl missing)."
    if [ "$MODE" = "container" ]; then
        install_docker
        command -v docker compose >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 \
            || die "docker compose plugin not found."
        have_curl || have_wget || die "need curl or wget to fetch companion files."
    else
        command -v git >/dev/null 2>&1 || die "git not found for --from-source mode."
        command -v python3 >/dev/null 2>&1 || die "python3 not found for --from-source mode."
    fi
    log "prerequisites OK (root, systemd, mode=$MODE)."
}

# --- 2. Kiosk user --------------------------------------------------------
install_user() {
    if id "$KIOSK_USER" >/dev/null 2>&1; then
        log "kiosk user '$KIOSK_USER' already exists (home $KIOSK_HOME)."
    else
        # Service account: no login shell (systemd runs the supervisor as it).
        useradd -m -s /usr/sbin/nologin "$KIOSK_USER" || die "could not create user '$KIOSK_USER'."
        # Re-derive home in case the user existed with a non-default HOME.
        KIOSK_HOME="$(getent passwd "$KIOSK_USER" | cut -d: -f6)"
        log "created kiosk user '$KIOSK_USER' (home $KIOSK_HOME, no login shell)."
    fi
    # Everything kiosk lives under the user's home; the user owns it all.
    mkdir -p "$KIOSK_HOME/bin" "$PHOTO_HOST_DIR" "$CONFIG_HOST_DIR" "$INSTALL_DIR"
    chown -R "$KIOSK_USER":"$KIOSK_USER" "$KIOSK_HOME"
}

# --- 3. Engine: container (default) or venv (--from-source) -------------
install_engine() {
    if [ "$MODE" = "container" ]; then
        log "pulling engine image ($IMAGE)..."
        docker pull "$IMAGE" >/dev/null 2>&1 || die "could not pull $IMAGE."
        # Pull the compose file from the repo at install time (single source of
        # truth) and write an override pinning runtime config.
        fetch "docker-compose.yml"
        cat > "$INSTALL_DIR/docker-compose.override.yml" <<EOF
services:
  kiosk-engine:
    image: ${IMAGE}
    ports:
      - "8080:8080"              # reachable from LAN for config/upload from a laptop
    environment:
      HA_URL: "http://localhost:8123"   # seed; edit via web config
      PHOTO_DIR: "/photos"
      IDLE_TIMEOUT_SECONDS: "120"
      SLIDE_INTERVAL_SECONDS: "10"
      CONFIG_FILE: "/config/kiosk.json"
    volumes:
      - "${CONFIG_HOST_DIR}:/config"
      - "${PHOTO_HOST_DIR}:/photos"
EOF
        mkdir -p "$PHOTO_HOST_DIR" "$CONFIG_HOST_DIR"
        docker compose -f "$INSTALL_DIR/docker-compose.yml" -f "$INSTALL_DIR/docker-compose.override.yml" up -d \
            || die "engine container failed to start."
        log "engine container up."
    else
        log "installing engine as Python venv at $ENGINE_SRC_DIR (cloning repo)..."
        git clone --depth 1 "$REPO_GIT" "$ENGINE_SRC_DIR" \
            || die "could not clone repository."
        python3 -m venv "$ENGINE_SRC_DIR/.venv" || die "could not create venv."
        "$ENGINE_SRC_DIR/.venv/bin/pip" install --quiet -r "$ENGINE_SRC_DIR/requirements.txt" \
            || die "could not install Python deps."
        log "engine venv ready."
    fi
}

# --- 4. Engine service (source mode runs app.py under systemd) ----------
install_engine_service() {
    [ "$MODE" = "source" ] || return 0
    cat > /etc/systemd/system/kiosk-engine.service <<EOF
[Unit]
Description=HA Photo Kiosk engine (Python venv)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$KIOSK_USER
Group=$KIOSK_USER
ExecStart=$ENGINE_SRC_DIR/.venv/bin/python $ENGINE_SRC_DIR/apps/app.py
Environment=PORT=$ENGINE_PORT
Environment=HA_URL=http://localhost:8123
Environment=PHOTO_DIR=/photos
Environment=CONFIG_FILE=$CONFIG_HOST_DIR/kiosk.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable kiosk-engine.service >/dev/null 2>&1
    log "engine systemd service installed + enabled."
}

# --- 5b. GUI stack (X + Chromium + autologin) — the kiosk's display ------
# A kiosk is a physical display device: it must boot straight into the UI.
# Ported from the original HA-Chromium-Kiosk approach — no display manager,
# xinit + openbox-session on vt7, getty@tty1 autologin for the kiosk user.
install_gui() {
    # A kiosk is a physical display device — the GUI stack is mandatory.
    log "installing graphical stack (X server, Chromium, Openbox)..."
    # X pulls in keyboard-configuration, which prompts for a layout and hangs
    # a non-interactive install. Pre-seed the debconf answers (DEBIAN_FRONTEND
    # is already noninteractive globally) so apt never blocks on a prompt.
    debconf-set-selections <<'EOF'
keyboard-configuration  keyboard-configuration/layoutcode  string  us
keyboard-configuration  keyboard-configuration/xkb-keymap select  us
keyboard-configuration  keyboard-configuration/variant  select  English (US)
console-setup  console-setup/layoutcode  string  us
EOF
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq xorg xserver-xorg xinit openbox chromium unclutter curl netcat-openbsd \
        >/dev/null 2>&1 || die "could not install graphical packages (xorg/chromium/openbox)."

    # Auto-login the kiosk user on tty1 (no display manager, no password).
    mkdir -p /etc/systemd/system/getty@tty1.service.d
    cat > /etc/systemd/system/getty@tty1.service.d/override.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $KIOSK_USER --noclear %I \$TERM
Type=idle
EOF
    systemctl daemon-reload

    # Openbox autostart launches the supervisor (which opens Chromium).
    sudo -u "$KIOSK_USER" mkdir -p "$KIOSK_HOME/.config/openbox"
    cat > "$KIOSK_HOME/.config/openbox/autostart" <<EOF
# HA Photo Kiosk — start the supervisor (Chromium kiosk at the engine page).
xset s off
xset -dpms
xset s noblank
unclutter -idle 0 &
$SUPERVISOR_BIN &
EOF
    chown -R "$KIOSK_USER":"$KIOSK_USER" "$KIOSK_HOME/.config"

    # The kiosk user needs the tty group to grab the vt7 session.
    usermod -aG tty "$KIOSK_USER"

    log "graphical stack installed: boots to the kiosk on the physical display."
}

# --- 5. Supervisor + systemd --------------------------------------------
install_supervisor() {
    log "installing supervisor script + systemd unit..."
    fetch "scripts/supervisor/kiosk-supervisor.sh"
    fetch "scripts/supervisor/ha-photo-kiosk.service"
    install -m 0755 "$INSTALL_DIR/kiosk-supervisor.sh" "$SUPERVISOR_BIN"
    # The kiosk user owns its whole home, including the supervisor it runs.
    chown "$KIOSK_USER":"$KIOSK_USER" "$SUPERVISOR_BIN" "$KIOSK_HOME/bin"
    # The systemd unit MUST live in /etc/systemd/system, but it runs the
    # supervisor from the kiosk user's home as that user.
    # The unit's ExecStart (xinit + openbox-session on vt7) is preserved as-is;
    # only the kiosk user/group are substituted. Openbox's autostart launches
    # the supervisor, which opens Chromium.
    sed -e "s|^User=.*|User=$KIOSK_USER|" \
        -e "s|^Group=.*|Group=$KIOSK_USER|" \
        "$INSTALL_DIR/ha-photo-kiosk.service" > /etc/systemd/system/ha-photo-kiosk.service
    systemctl daemon-reload
    systemctl enable ha-photo-kiosk.service >/dev/null 2>&1
    log "supervisor installed + enabled."
}

# --- 6. Photos dir -------------------------------------------------------
setup_photos() {
    chown -R "$KIOSK_USER":"$KIOSK_USER" "$PHOTO_HOST_DIR" "$CONFIG_HOST_DIR" 2>/dev/null || true
    log "photos dir ready at $PHOTO_HOST_DIR (or just upload via the web config)."
}

# --- 7. Firewall (restrict to the ports the kiosk needs) -----------------
install_firewall() {
    command -v ufw >/dev/null 2>&1 || { log "ufw not found; skipping firewall setup."; return 0; }
    log "configuring ufw (allow SSH + engine port $ENGINE_PORT)..."
    ufw allow 22/tcp >/dev/null 2>&1 || true
    ufw allow "$ENGINE_PORT"/tcp >/dev/null 2>&1 || true
    ufw --force enable >/dev/null 2>&1 || log "could not enable ufw (enable manually)."
    log "firewall enabled: SSH (22) + engine ($ENGINE_PORT) allowed."
}

# --- 8. Config auth (basic auth for the web config service) --------------
install_auth() {
    AUTH_FILE="$CONFIG_HOST_DIR/auth.json"
    # Reset to a known temp password on every run. If the user has already set
    # their own password (hashed, must_change=false), leave it alone. Otherwise
    # regenerate + print a fresh password so the operator is never locked out by
    # a leftover auth file from an earlier attempt.
    if [ -f "$AUTH_FILE" ] && grep -q '"password_hash"' "$AUTH_FILE" 2>/dev/null             && ! grep -q '"must_change": *true' "$AUTH_FILE" 2>/dev/null; then
        log "config auth already set by the operator (keeping it)."
        return 0
    fi
    # Generate a random temporary password; the user changes it on first login.
    AUTH_PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 || true)"
    [ -n "$AUTH_PASSWORD" ] || AUTH_PASSWORD="kiosk-$(date +%s)"
    cat > "$AUTH_FILE" <<EOF
{
  "username": "kiosk",
  "password": "$AUTH_PASSWORD",
  "must_change": true
}
EOF
    # The engine hashes the temp password on first login and clears the
    # plaintext. Until then it's kiosk-owned and mode 600.
    chown "$KIOSK_USER":"$KIOSK_USER" "$AUTH_FILE"
    chmod 600 "$AUTH_FILE"
    log "config auth set up. Temporary password: $AUTH_PASSWORD"
    log "  (You will be asked to change it on first login.)"
}

# --- 9. Google Photos sync (rclone + systemd) ----------------------------
install_gphotos_sync() {
    log "installing Google Photos sync (rclone)..."
    if [ "${INSTALL_RCLONE:-1}" = "1" ]; then
        command -v rclone >/dev/null 2>&1 || {
            apt-get install -y rclone >/dev/null 2>&1 || log "could not install rclone (install manually)."
        }
    else
        command -v rclone >/dev/null 2>&1 || log "rclone not installed (skipping install per INSTALL_RCLONE=0)."
    fi
    # Sync script + systemd units live in the kiosk home / systemd dir.
    fetch "scripts/supervisor/kiosk-gphotos-sync.sh"
    fetch "scripts/supervisor/kiosk-gphotos-sync.service"
    fetch "scripts/supervisor/kiosk-gphotos-sync.timer"
    fetch "scripts/supervisor/kiosk-gphotos-sync.path"
    install -m 0755 "$INSTALL_DIR/kiosk-gphotos-sync.sh" "$GP_SYNC_BIN"
    chown "$KIOSK_USER":"$KIOSK_USER" "$GP_SYNC_BIN" "$KIOSK_HOME/bin"
    # Substitute the kiosk user/home into the units, write to systemd.
    for u in service timer path; do
        sed -e "s|User=kiosk|User=$KIOSK_USER|" \
            -e "s|Group=kiosk|Group=$KIOSK_USER|" \
            -e "s|/home/kiosk/|$KIOSK_HOME/|g" \
            "$INSTALL_DIR/kiosk-gphotos-sync.$u" > /etc/systemd/system/kiosk-gphotos-sync.$u
    done
    systemctl daemon-reload
    systemctl enable --now kiosk-gphotos-sync.timer >/dev/null 2>&1
    systemctl enable --now kiosk-gphotos-sync.path >/dev/null 2>&1
    log "Google Photos sync installed: rclone + systemd timer (enable in web config)."
}

# --- Banner ---------------------------------------------------------------
print_banner() {
    echo -e "${CYAN}****************************************************************************************************${NC}"
    echo "    __  _____       ________                         _                    __ __ _            __   "
    echo "   / / / /   |     / ____/ /_  _________  ____ ___  (_)_  ______ ___     / //_/(_)___  _____/ /__ "
    echo "  / /_/ / /| |    / /   / __ \\/ ___/ __ \\/ __ \`__ \\/ / / / / __ \`__ \\   / ,<  / / __ \\/ ___/ //_/ "
    echo " / __  / ___ |   / /___/ / / / /  / /_/ / / / / / / / /_/ / / / / / /  / /| |/ / /_/ (__  ) ,<    "
    echo "/_/ /_/_/  |_|   \\____/_/ /_/_/   \\____/_/ /_/ /_/_/\\__,_/_/ /_/ /_/  /_/ |_/_/\\____/____/_/|_|   "
    echo "                                                                                                  "
    echo -e "${BOLD}                        Setup and Install Script for HA Photo Kiosk${NC}              "
    echo -e "${CYAN}****************************************************************************************************${NC}"
    echo -e "${RED}${BOLD}***                               WARNING: USE AT YOUR OWN RISK                                  ***${NC}"
    echo -e "${CYAN}****************************************************************************************************${NC}"
    echo ""
    echo -e "${BOLD}This script will:${NC}"
    echo -e " ${GREEN}*${NC} Create a dedicated kiosk user"
    echo -e " ${GREEN}*${NC} Install the graphical stack (X server, Chromium, Openbox)"
    echo -e " ${GREEN}*${NC} Pull the engine container (or build from source)"
    echo -e " ${GREEN}*${NC} Install the display supervisor + systemd service"
    echo -e " ${GREEN}*${NC} Set up the web config service (with basic auth)"
    echo -e " ${GREEN}*${NC} Configure a firewall (SSH + engine port)"
    echo ""
    echo -e "* Please read the script before running it to understand what it does."
    echo -e "* Use at your own risk. The author is not responsible for any damage or data loss."
    # Only pause for confirmation when running interactively; a curl|bash pipe
    # has no terminal to read from, so proceed straight through.
    if [ -t 0 ]; then
        echo -e "${BOLD}Press ${GREEN}[Enter]${NC}${BOLD} to continue or ${RED}[Ctrl+C]${NC}${BOLD} to exit${NC}"
        read -n 1 -s
    fi
}

# --- Main ----------------------------------------------------------------
main() {
    parse_args "$@"
    print_banner
    log "HA Photo Kiosk installer starting (mode=$MODE)."
    progress 1 "$PROGRESS_TOTAL" "Checking prerequisites"
    check_prereqs
    progress 2 "$PROGRESS_TOTAL" "Creating kiosk user"
    install_user
    progress 3 "$PROGRESS_TOTAL" "Setting up config auth"
    install_auth
    progress 4 "$PROGRESS_TOTAL" "Installing engine"
    install_engine
    progress 5 "$PROGRESS_TOTAL" "Installing engine service"
    install_engine_service
    progress 6 "$PROGRESS_TOTAL" "Installing supervisor"
    install_supervisor
    progress 7 "$PROGRESS_TOTAL" "Installing graphical stack (X, Chromium, Openbox)"
    install_gui
    progress 8 "$PROGRESS_TOTAL" "Setting up photos dir"
    setup_photos
    progress 9 "$PROGRESS_TOTAL" "Configuring firewall"
    install_firewall
    progress 10 "$PROGRESS_TOTAL" "Installing Google Photos sync"
    install_gphotos_sync

    # Start the engine now (container starts via compose; source via systemd).
    if [ "$MODE" = "source" ]; then
        systemctl start kiosk-engine.service && log "engine started."
    fi

    # Start the kiosk now — the box is a working kiosk immediately, not just
    # configured (R19b). The supervisor brings up X + Chromium on the display.
    systemctl start ha-photo-kiosk.service && log "kiosk started (X + Chromium on the display)."
    progress 11 "$PROGRESS_TOTAL" "Install complete"

    log ""
    log "=========================== INSTALL COMPLETE ========================"
    log ""
    log "  NEXT STEP — configure it from a LAPTOP, not this screen:"
    log ""
    # Best-effort: print the kiosk's LAN IP so the user can reach it from a PC.
    KIOSK_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [ -n "$KIOSK_IP" ]; then
        log "  On your laptop/phone, open:"
        log ""
        log "      http://${KIOSK_IP}:$ENGINE_PORT/config/"
        log ""
        log "  (If that IP isn't right, find this box's IP with: ip addr)"
        log ""
    else
        log "  On your laptop/phone, open this kiosk's config page:"
        log "      http://<kiosk-ip>:$ENGINE_PORT/config/"
        log "  (find the kiosk's IP with: ip addr)"
        log ""
    fi
    log "  Log in with username 'kiosk' and the temporary password printed above."
    log "  You will be asked to change it on first login."
    log ""
    log "  There you set:"
    log "    - Home Assistant URL (http://<your-ha-ip>:8123)"
    log "    - Idle timeout / slide interval"
    log "    - Upload your photos"
    log "  Then restart the engine for changes to take effect:"
    log "      sudo docker restart kiosk-engine       # container install"
    log ""
    log "  The kiosk is already running on the display (X + Chromium)."
    log "  It also starts automatically on boot."
    log ""
}

main "$@"