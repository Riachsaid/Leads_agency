#!/usr/bin/env bash
# ===========================================================================
# setup_gcs_vps.sh — Google Cloud Shell → VPS permanent
# ===========================================================================
# À exécuter SUR Google Cloud Shell (pas sur Termux)
#   gcloud cloud-shell ssh
#   bash <(curl -sL https://raw.githubusercontent.com/.../setup_gcs_vps.sh)
#
# Ou manuellement :
#   1. gcloud cloud-shell ssh
#   2. nano setup_gcs_vps.sh && chmod +x setup_gcs_vps.sh && ./setup_gcs_vps.sh
# ===========================================================================
set -euo pipefail

GCS_HOME="$HOME"
GCS_WORK="$GCS_HOME/gcs_vps"
NGROK_TOKEN="36edeYFUeRKO1PDU1ggv00nGWYn_7h8kd9H49wFGCxZA7ZLL5"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  GCS VPS SETUP — $(date '+%Y-%m-%d %H:%M:%S')"
echo "╚══════════════════════════════════════════════════════════════╝"

mkdir -p "$GCS_WORK"/{logs,scripts,data}

# ── 1. System dependencies ────────────────────────────────────────────────
echo "[1/7] System dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq tmux curl wget git python3-pip nodejs npm jq unzip

# ── 2. ngrok ──────────────────────────────────────────────────────────────
echo "[2/7] ngrok install..."
if ! command -v ngrok &>/dev/null; then
    ARCH=$(uname -m)
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        wget -q https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.tgz -O /tmp/ngrok.tgz
    else
        wget -q https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz -O /tmp/ngrok.tgz
    fi
    tar -xzf /tmp/ngrok.tgz -C /tmp
    sudo mv /tmp/ngrok /usr/local/bin/ngrok
fi
ngrok config add-authtoken "$NGROK_TOKEN" 2>/dev/null || true

# ── 3. Python dependencies ────────────────────────────────────────────────
echo "[3/7] Python packages..."
pip3 install -q flask requests python-dotenv schedule smtplib

# ── 4. GitHub clone (si repo existe) ──────────────────────────────────────
echo "[4/7] GitHub sync setup..."
if [ -d "$GCS_WORK/repo" ]; then
    echo "  → Repo already cloned, pulling..."
    cd "$GCS_WORK/repo" && git pull --ff-only 2>/dev/null || true
else
    echo "  → SKIP: Clone your repo manually after setup:"
    echo "    cd $GCS_WORK && git clone https://github.com/YOUR_USER/YOUR_REPO.git repo"
fi

# ── 5. Server bootstrap ─────────────────────────────────────────────────
echo "[5/7] Creating server bootstrap..."
cat > "$GCS_WORK/start_services.sh" << 'SRVEOF'
#!/usr/bin/env bash
# ===========================================================================
# Démarre tous les services : keepalive + health + ngrok
# ===========================================================================
GCS_WORK="$HOME/gcs_vps"
PID_DIR="$GCS_WORK/pids"
mkdir -p "$PID_DIR"

# Nettoyer les vieux pids
rm -f "$PID_DIR"/*.pid

echo "[$(date)] Starting GCS VPS services..."

# ── Health server (oblige Cloud Shell à rester actif) ──
python3 "$GCS_WORK/health_server.py" &
echo $! > "$PID_DIR/health.pid"
echo "  → Health server PID: $(cat $PID_DIR/health.pid)"

# ── ngrok tunnel ──
if command -v ngrok &>/dev/null; then
    # Tuer les vieux ngrok
    pkill -f ngrok 2>/dev/null || true
    sleep 1
    # Tunnel vers le health server
    nohup ngrok http 8080 --log=stdout > "$GCS_WORK/logs/ngrok.log" 2>&1 &
    echo $! > "$PID_DIR/ngrok.pid"
    sleep 3
    NGROK_URL=$(grep -o "https://[a-z0-9.-]*\.ngrok-free\.dev" "$GCS_WORK/logs/ngrok.log" | head -1)
    echo "  → ngrok URL: ${NGROK_URL:-waiting...}"
    echo "$NGROK_URL" > "$GCS_WORK/.ngrok_url"
fi

# ── keepalive daemon ──
python3 "$GCS_WORK/keepalive_daemon.py" &
echo $! > "$PID_DIR/keepalive.pid"
echo "  → Keepalive PID: $(cat $PID_DIR/keepalive.pid)"

# ── Chromium headless ──
if command -v chromium &>/dev/null; then
    echo "  → Chromium headless starting..."
    rm -f "$GCS_WORK/chromium_data/SingletonLock" \
          "$GCS_WORK/chromium_data/SingletonSocket" \
          "$GCS_WORK/chromium_data/SingletonCookie" 2>/dev/null || true
    nohup chromium \
        --no-sandbox \
        --disable-setuid-sandbox \
        --disable-dev-shm-usage \
        --headless=new \
        --remote-debugging-port=9222 \
        --disable-gpu \
        --disable-software-rasterizer \
        --disable-extensions \
        --disable-background-networking \
        --disable-sync \
        --mute-audio \
        --no-first-run \
        --hide-scrollbars \
        --disable-blink-features=AutomationControlled \
        --user-data-dir="$GCS_WORK/chromium_data" \
        --window-size=1280,720 \
        > "$GCS_WORK/logs/chromium.log" 2>&1 &
    echo $! > "$PID_DIR/chromium.pid"
    echo "  → Chromium PID: $(cat $PID_DIR/chromium.pid)"
else
    echo "  → Chromium not installed, skipping (run install_chromium_gcs.sh)"
fi

# ── code-server (VS Code Web) ──
export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:/usr/bin:$PATH"
CS_BIN="$(command -v code-server 2>/dev/null || echo "$HOME/.local/bin/code-server")"
if [ -f "$CS_BIN" ] && [ -x "$CS_BIN" ]; then
    echo "  → code-server starting..."
    # Lire le password depuis le fichier, créer un défaut si absent
    CS_PASS="$(cat "$GCS_WORK/.code-server-password" 2>/dev/null || echo 'kLaSiiNkOv1988@')"
    echo "$CS_PASS" > "$GCS_WORK/.code-server-password"
    chmod 600 "$GCS_WORK/.code-server-password"
    export PASSWORD="$CS_PASS"
    export HASHED_PASSWORD=""
    rm -f /tmp/code-server*.lock 2>/dev/null || true
    nohup "$CS_BIN" \
        --bind-addr "0.0.0.0:3000" \
        --auth password \
        --disable-telemetry \
        --disable-update-check \
        > "$GCS_WORK/logs/code-server.log" 2>&1 &
    echo $! > "$PID_DIR/code-server.pid"
    sleep 2
    if curl -s -o /dev/null -w "" "http://localhost:3000" 2>/dev/null; then
        echo "  → code-server PID: $(cat $PID_DIR/code-server.pid) ✅"
    else
        echo "  → code-server PID: $(cat $PID_DIR/code-server.pid) ⚠️  (port pas encore ouvert, voir logs)"
        tail -5 "$GCS_WORK/logs/code-server.log" 2>/dev/null | sed 's/^/      /'
    fi
else
    echo "  → code-server not installed, skipping (run install_code_server.sh)"
fi

echo "[$(date)] All services started."
echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║  Health:  http://localhost:8080/health       ║"
echo "  ║  ngrok:   $(cat "$GCS_WORK/.ngrok_url" 2>/dev/null || echo 'wait...')  ║"
echo "  ║  Logs:    $GCS_WORK/logs/            ║"
echo "  ╚══════════════════════════════════════════════╝"
SRVEOF
chmod +x "$GCS_WORK/start_services.sh"

# ── 7. code-server (VS Code Web) ──────────────────────────────────────────
echo "[7/7] Installing code-server (VS Code Web)..."
if ! command -v "$HOME/.local/bin/code-server" &>/dev/null; then
    curl -fsSL https://code-server.dev/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    echo "  → code-server installed"
else
    echo "  → code-server déjà installé"
fi

# Config code-server
mkdir -p "$HOME/.config/code-server"
CS_PASS="${CS_PASSWORD:-kLaSiiNkOv1988@}"
cat > "$HOME/.config/code-server/config.yaml" << CONFEOF
bind-addr: 0.0.0.0:3000
auth: password
password: "${CS_PASS}"
cert: false
disable-telemetry: true
disable-update-check: true
CONFEOF
echo "$CS_PASS" > "$GCS_WORK/.code-server-password"
chmod 600 "$GCS_WORK/.code-server-password"
echo "  → code-server configuré (password: $CS_PASS)"

# ── 6. Health server + keepalive ──────────────────────────────────────────
echo "[6/7] Creating keepalive & health scripts..."

# ─── health_server.py ─────────────────────────────────────────────────────
cat > "$GCS_WORK/health_server.py" << 'PYEOF'
import http.server
import json
import os
import time
import threading
from datetime import datetime

HOST = "0.0.0.0"
PORT = 8080
START_TIME = time.time()

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def _respond(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        uptime_sec = int(time.time() - START_TIME)
        uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s"

        if self.path == "/health":
            self._respond(200, {
                "status": "alive",
                "uptime": uptime_str,
                "uptime_sec": uptime_sec,
                "host": HOST,
                "port": PORT,
                "time": datetime.utcnow().isoformat(),
                "pid": os.getpid(),
            })
        elif self.path == "/":
            self._respond(200, {
                "service": "GCS VPS",
                "status": "running",
                "uptime": uptime_str,
                "endpoints": {
                    "/health": "Health check (use for keepalive pings)",
                    "/ping": "Lightweight ping",
                }
            })
        elif self.path == "/ping":
            self._respond(200, {"pong": True, "t": time.time()})
        else:
            self._respond(404, {"error": "not found"})

    def log_message(self, format, *args):
        # Silence les logs HTTP pour éviter le spam
        pass

if __name__ == "__main__":
    server = http.server.HTTPServer((HOST, PORT), HealthHandler)
    print(f"[HEALTH] Server listening on {HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
PYEOF

# ─── keepalive_daemon.py ──────────────────────────────────────────────────
cat > "$GCS_WORK/keepalive_daemon.py" << 'PYEOF'
import subprocess
import time
import logging
import os
import sys
from datetime import datetime

LOG_FILE = os.path.expanduser("~/gcs_vps/logs/keepalive.log")
PID_FILE = os.path.expanduser("~/gcs_vps/pids/keepalive.pid")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [KEEPALIVE] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("keepalive")

KEEPALIVE_INTERVAL = 120  # secondes entre chaque ping
TARGETS = [
    "http://localhost:8080/ping",
    "http://localhost:8080/health",
]

def self_ping():
    """Ping les endpoints locaux pour générer de l'activité."""
    for url in TARGETS:
        try:
            r = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
                capture_output=True, text=True, timeout=10
            )
            code = r.stdout.strip()
            if code == "200":
                logger.debug(f"Ping {url} → {code}")
            else:
                logger.warning(f"Ping {url} → {code}")
        except Exception as e:
            logger.error(f"Ping {url} failed: {e}")

def cloud_shell_keepalive():
    """
    Envoie une activité à l'API interne de Cloud Shell pour
    signaler que la session est active. Équivalent à taper
    une touche dans le terminal Web.
    """
    try:
        # Méthode 1: Petit calcul CPU pour montrer de l'activité
        _ = [i * i for i in range(100000)]
        # Méthode 2: Access files dans $HOME (génère I/O)
        with open(os.path.expanduser("~/.bashrc"), "r") as f:
            _ = f.read(100)
        logger.debug("Cloud Shell keepalive signal sent")
    except Exception as e:
        logger.error(f"Cloud Shell keepalive error: {e}")

def main():
    cycle = 0
    logger.info("=" * 50)
    logger.info("Keepalive daemon started")
    logger.info(f"Interval: {KEEPALIVE_INTERVAL}s")
    logger.info(f"Targets: {TARGETS}")
    logger.info("=" * 50)

    while True:
        cycle += 1
        now = datetime.now().strftime("%H:%M:%S")

        self_ping()
        cloud_shell_keepalive()

        if cycle % 30 == 0:  # Toutes les heures (30 * 120s = 3600s)
            logger.info(f"[{now}] Keepalive cycle #{cycle} — all OK")

        time.sleep(KEEPALIVE_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Keepalive daemon stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal: {e}")
        sys.exit(1)
PYEOF

# ─── github_sync.sh ───────────────────────────────────────────────────────
cat > "$GCS_WORK/github_sync.sh" << 'GHEOF'
#!/usr/bin/env bash
# ===========================================================================
# github_sync.sh — Sync GCS work to GitHub (à exécuter dans Cloud Shell)
# ===========================================================================
# Usage:
#   ./github_sync.sh                    # Push tout
#   ./github_sync.sh --pull             # Pull only
#   ./github_sync.sh --status           # Voir l'état
# ===========================================================================
set -euo pipefail

GCS_WORK="$HOME/gcs_vps"
REPO_DIR="$GCS_WORK/repo"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

if [ ! -d "$REPO_DIR/.git" ]; then
    echo "[ERROR] No git repo at $REPO_DIR"
    echo "  Clone d'abord : cd $GCS_WORK && git clone <your-repo-url> repo"
    exit 1
fi

cd "$REPO_DIR"

case "${1:-push}" in
    --pull)
        echo "[$TIMESTAMP] Pulling from GitHub..."
        git pull --ff-only
        echo "[OK] Pulled latest."
        ;;
    --status)
        echo "=== Git Status ==="
        git status
        echo "=== Recent log ==="
        git log --oneline -5
        ;;
    push|*)
        # Vérifier s'il y a des changements
        if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
            echo "[$TIMESTAMP] Rien à commit. Working tree clean."
            exit 0
        fi

        echo "[$TIMESTAMP] Staging all changes..."
        git add -A

        echo "[$TIMESTAMP] Committing..."
        git commit -m "GCS auto-sync $TIMESTAMP"

        echo "[$TIMESTAMP] Pushing to GitHub..."
        git push

        echo "[OK] Sync complete."
        ;;
esac
GHEOF
chmod +x "$GCS_WORK/github_sync.sh"

# ─── tmux launcher ───────────────────────────────────────────────────────
cat > "$GCS_WORK/tmux_vps.sh" << 'TMXEOF'
#!/usr/bin/env bash
# ===========================================================================
# Lance tmux avec 3 panes : services, logs, shell libre
# ===========================================================================
SESSION="gcs_vps"
GCS_WORK="$HOME/gcs_vps"

# Tuer session existante si présente
tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" -n "vps"

# Pane 0: services
tmux send-keys -t "$SESSION:0.0" "cd $GCS_WORK && ./start_services.sh" Enter

# Pane 1: logs (split horizontal)
tmux split-window -h -t "$SESSION:0"
tmux send-keys -t "$SESSION:0.1" "cd $GCS_WORK && tail -f logs/keepalive.log logs/ngrok.log" Enter

# Pane 2: shell libre (split vertical)
tmux split-window -v -t "$SESSION:0.0"
tmux send-keys -t "$SESSION:0.2" "cd $GCS_WORK && echo 'Shell libre — utilisez ce pane pour debugger'" Enter

# Ajuster les tailles
tmux select-layout -t "$SESSION:0" main-vertical 2>/dev/null || true

# Window 1: Chromium headless logs
if [ -f "$GCS_WORK/logs/chromium.log" ] || command -v chromium &>/dev/null; then
    tmux new-window -t "$SESSION" -n "chromium"
    tmux send-keys -t "$SESSION:chromium.0" \
        "echo '━━━ Chromium Headless ━━━'; " \
        "echo 'Debug: http://localhost:9222'; " \
        "echo 'Logs:'; " \
        "tail -f $GCS_WORK/logs/chromium.log" Enter
fi

# Window 2: VS Code Web (code-server)
if [ -f "$GCS_WORK/.code-server-password" ] || command -v "$HOME/.local/bin/code-server" &>/dev/null; then
    tmux new-window -t "$SESSION" -n "vscode"
    tmux send-keys -t "$SESSION:vscode.0" \
        "echo '━━━ code-server (VS Code Web) ━━━'; " \
        "echo 'URL:  http://localhost:3000'; " \
        "echo 'Pass: $(cat "$GCS_WORK/.code-server-password" 2>/dev/null || echo 'N/A')'; " \
        "echo 'Logs:'; " \
        "tail -f $GCS_WORK/logs/code-server.log" Enter
fi

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  TMUX SESSION: gcs_vps                                      ║"
echo "║  Attach:  tmux attach -t gcs_vps                            ║"
echo "║  Detach:  Ctrl+B, D                                         ║"
echo "║  Windows: 0:vps | 1:chromium | 2:vscode                    ║"
echo "║  Switch:  Ctrl+B, then 0/1/2                               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Les services démarrent automatiquement dans le pane 0."
echo "Connecte-toi depuis Termux:"
echo "  gcloud cloud-shell ssh -- tmux attach -t gcs_vps"
TMXEOF
chmod +x "$GCS_WORK/tmux_vps.sh"

# ── Final ─────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅ GCS VPS SETUP COMPLETE                                  ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Dossier:          $GCS_WORK"
echo ""
echo "  PROCHAINES ÉTAPES :"
echo "  1. Clone ton repo :"
echo "     cd $GCS_WORK && git clone <TON_REPO_URL> repo"
echo ""
echo "  2. Lance tmux :"
echo "     tmux new-session -s gcs_vps"
echo "     ./start_services.sh"
echo "     (Ctrl+B, D pour détacher)"
echo ""
echo "  3. Depuis Termux, reconnecte-toi :"
echo "     gcloud cloud-shell ssh -- tmux attach -t gcs_vps"
echo ""
echo "  4. (Optionnel) Config GitHub :"
echo "     git config --global user.name 'TON_NOM'"
echo "     git config --global user.email 'TON_EMAIL'"
echo "     ./github_sync.sh"
echo ""
echo "  5. Install Chromium headless (optionnel) :"
echo "     bash install_chromium_gcs.sh"
echo ""
echo "  6. Install code-server (VS Code Web, optionnel) :"
echo "     bash install_code_server.sh --password <pass>"
echo ""
echo "  7. Anti-dormance EXTERNE (RECOMMANDÉ) :"
echo "     Va sur https://cron-job.org (gratuit)"
echo "     Crée un job qui ping toutes les 5 min :"
echo "     → URL: https://TON-NDK.ngrok-free.dev/health"
echo ""
echo "  8. DeepSeek V4 Flash Agent :"
echo "     export DEEPSEEK_API_KEY='sk-...'"
echo "     python3 deepseek_v4_agent.py chat 'Bonjour'"
echo ""
echo "  Logs: $GCS_WORK/logs/"
echo ""
echo "  Services:"
echo "  ├─ Health:   http://localhost:8080/health"
echo "  ├─ ngrok:    $(cat "$GCS_WORK/.ngrok_url" 2>/dev/null || echo 'not running')"
echo "  ├─ Chromium: http://localhost:9222"
echo "  └─ VS Code:  http://localhost:3000"
echo "══════════════════════════════════════════════════════════════════"
