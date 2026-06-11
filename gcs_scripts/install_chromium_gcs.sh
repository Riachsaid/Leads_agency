#!/usr/bin/env bash
# ===========================================================================
# install_chromium_gcs.sh — Chromium Headless dans Google Cloud Shell
# ===========================================================================
# Installe chromium, le lance en headless avec tous les bypass flags,
# et l'intègre dans la session tmux gcs_vps.
#
# Usage:
#   ./install_chromium_gcs.sh                    # Install + launch + tmux
#   ./install_chromium_gcs.sh --install-only     # Install sans launch
#   ./install_chromium_gcs.sh --launch-only      # Launch sans install
#   ./install_chromium_gcs.sh --status           # Vérifier si Chromium tourne
#   ./install_chromium_gcs.sh --verify-url <URL> # Tester le rendu d'une URL
# ===========================================================================
set -euo pipefail

# ── Couleurs ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
header(){ echo -e "${CYAN}━━━ $1 ━━━${NC}"; }

# ── Configuration ─────────────────────────────────────────────────────────
CHROMIUM_BIN="/usr/bin/chromium"
CHROMIUM_PORT=9222
CHROMIUM_LOG="$HOME/gcs_vps/logs/chromium.log"
CHROMIUM_PID_FILE="$HOME/gcs_vps/pids/chromium.pid"
TMUX_SESSION="gcs_vps"
GCS_WORK="$HOME/gcs_vps"

mkdir -p "$GCS_WORK/logs" "$GCS_WORK/pids"

# ── Flags Chromium (Cloud Shell / container-safe) ─────────────────────────
# Explication de chaque flag critique :
#   --no-sandbox / --disable-setuid-sandbox  : Cloud Shell n'a pas de namespace
#   --disable-dev-shm-usage                  : /dev/shm est 64Mo en container, utilise /tmp
#   --headless=new                           : Nouveau headless (plus compatible)
#   --remote-debugging-port=9222             : Debug + automation endpoint
#   --disable-gpu / --disable-software-rasterizer : Pas de GPU en headless
#   --no-first-run / --disable-default-apps  : Évite les dialogues au premier lancement
#   --disable-background-networking          : Réduit le bruit réseau
#   --mute-audio                             : Pas d'audio
CHROMIUM_FLAGS=(
    --no-sandbox
    --disable-setuid-sandbox
    --disable-dev-shm-usage
    --headless=new
    --remote-debugging-port="$CHROMIUM_PORT"
    --disable-gpu
    --disable-software-rasterizer
    --disable-extensions
    --disable-background-networking
    --disable-sync
    --disable-translate
    --disable-default-apps
    --mute-audio
    --no-first-run
    --hide-scrollbars
    --disable-popup-blocking
    --disable-blink-features=AutomationControlled
    --user-data-dir="$GCS_WORK/chromium_data"
    --window-size=1280,720
)

# ── 1. Install Chromium ──────────────────────────────────────────────────
install_chromium() {
    header "Installation de Chromium"

    if command -v "$CHROMIUM_BIN" &>/dev/null; then
        info "Chromium déjà installé : $($CHROMIUM_BIN --version 2>/dev/null || echo 'version inconnue')"
        return 0
    fi

    info "Mise à jour des paquets..."
    sudo apt-get update -qq

    info "Installation de chromium + chromium-common..."
    sudo apt-get install -y -qq chromium chromium-common

    if command -v "$CHROMIUM_BIN" &>/dev/null; then
        info "Chromium installé avec succès : $($CHROMIUM_BIN --version 2>/dev/null)"
    else
        error "Échec de l'installation de Chromium"
        exit 1
    fi
}

# ── 2. Launch Chromium Headless ──────────────────────────────────────────
launch_chromium() {
    header "Lancement de Chromium headless"

    # Tuer l'ancienne instance si présente
    if [ -f "$CHROMIUM_PID_FILE" ]; then
        OLD_PID=$(cat "$CHROMIUM_PID_FILE" 2>/dev/null || echo "")
        if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
            warn "Ancien Chromium (PID $OLD_PID) en cours d'arrêt..."
            kill "$OLD_PID" 2>/dev/null || true
            sleep 2
        fi
    fi

    # Vérifier si le port est déjà pris par un Chromium
    if lsof -i :"$CHROMIUM_PORT" &>/dev/null 2>&1; then
        warn "Port $CHROMIUM_PORT déjà utilisé — Chromium tourne peut-être déjà"
        local existing_pid
        existing_pid=$(lsof -ti :"$CHROMIUM_PORT" 2>/dev/null || echo "")
        if [ -n "$existing_pid" ]; then
            echo "$existing_pid" > "$CHROMIUM_PID_FILE"
            info "Chromium déjà actif (PID $existing_pid)"
            return 0
        fi
    fi

    # Nettoyer le cache d'ancien verrouillage
    rm -f "$GCS_WORK/chromium_data/SingletonLock" \
          "$GCS_WORK/chromium_data/SingletonSocket" \
          "$GCS_WORK/chromium_data/SingletonCookie" 2>/dev/null || true

    # Lancement en background avec nohup
    nohup "$CHROMIUM_BIN" "${CHROMIUM_FLAGS[@]}" \
        > "$CHROMIUM_LOG" 2>&1 &

    local pid=$!
    echo "$pid" > "$CHROMIUM_PID_FILE"
    info "Chromium démarré (PID $pid)"

    # Attendre que le port remote debugging soit ouvert
    info "Attente de l'ouverture du port $CHROMIUM_PORT..."
    for i in $(seq 1 15); do
        if curl -s "http://localhost:$CHROMIUM_PORT/json/version" &>/dev/null; then
            echo ""
            info "✅ Chromium prêt sur http://localhost:$CHROMIUM_PORT"
            info "   WebSocket debug: ws://localhost:$CHROMIUM_PORT/devtools/browser"
            return 0
        fi
        sleep 1
    done

    warn "⚠️  Chromium lancé mais port $CHROMIUM_PORT pas encore ouvert. Voir logs :"
    echo "    tail -20 $CHROMIUM_LOG"
}

# ── 3. Intégration dans tmux ─────────────────────────────────────────────
add_to_tmux() {
    header "Intégration dans tmux session '$TMUX_SESSION'"

    if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        warn "Session tmux '$TMUX_SESSION' n'existe pas — création"
        tmux new-session -d -s "$TMUX_SESSION" -n "vps"
        tmux send-keys -t "$TMUX_SESSION:0.0" "cd $GCS_WORK" Enter
    fi

    # Vérifier si Chromium a déjà un pane dédié
    local window_count
    window_count=$(tmux list-windows -t "$TMUX_SESSION" 2>/dev/null | wc -l)

    if tmux list-windows -t "$TMUX_SESSION" 2>/dev/null | grep -q "chromium"; then
        info "Window 'chromium' existe déjà dans tmux"
        return 0
    fi

    # Créer une nouvelle window pour Chromium dans tmux
    tmux new-window -t "$TMUX_SESSION" -n "chromium"
    tmux send-keys -t "$TMUX_SESSION:chromium.0" \
        "echo '━━━ Chromium Headless ━━━'; " \
        "echo 'Debug: http://localhost:$CHROMIUM_PORT'; " \
        "echo 'Logs: tail -f $CHROMIUM_LOG'; " \
        "echo 'PID: $(cat $CHROMIUM_PID_FILE 2>/dev/null || echo "N/A")'; " \
        "echo '━━━━━━━━━━━━━━━━━━━━━━━'; " \
        "tail -f $CHROMIUM_LOG" Enter

    info "Window 'chromium' créée dans tmux session '$TMUX_SESSION'"
    info "  Attacher : tmux attach -t $TMUX_SESSION"
    info "  Switch   : Ctrl+B, puis 1 (ou le numéro de la window)"
}

# ── 4. Verification ──────────────────────────────────────────────────────
verify_status() {
    header "Status Chromium"

    if ! command -v "$CHROMIUM_BIN" &>/dev/null; then
        error "Chromium n'est pas installé"
        return 1
    fi

    echo "  Binaire: $CHROMIUM_BIN"
    echo "  Version: $($CHROMIUM_BIN --version 2>/dev/null || echo 'N/A')"
    echo ""

    if [ -f "$CHROMIUM_PID_FILE" ]; then
        local pid
        pid=$(cat "$CHROMIUM_PID_FILE" 2>/dev/null || echo "")
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo -e "  PID:      ${GREEN}$pid (running)${NC}"
        else
            echo -e "  PID:      ${RED}$pid (dead — PID file stale)${NC}"
        fi
    else
        echo -e "  PID:      ${YELLOW}N/A (no PID file)${NC}"
    fi

    echo "  Port:     $CHROMIUM_PORT"

    if curl -s "http://localhost:$CHROMIUM_PORT/json/version" &>/dev/null; then
        echo -e "  Status:   ${GREEN}✅ Responding on remote debugging port${NC}"
        echo ""
        echo "  === Browser Info ==="
        curl -s "http://localhost:$CHROMIUM_PORT/json/version" 2>/dev/null | \
            python3 -m json.tool 2>/dev/null || \
            curl -s "http://localhost:$CHROMIUM_PORT/json/version"
    else
        echo -e "  Status:   ${RED}❌ Not responding on port $CHROMIUM_PORT${NC}"
    fi

    echo ""
    echo "  === Active pages ==="
    local pages
    pages=$(curl -s "http://localhost:$CHROMIUM_PORT/json" 2>/dev/null)
    if [ -n "$pages" ] && [ "$pages" != "[]" ]; then
        echo "$pages" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for p in data:
        print(f\"  • {p.get('title', 'N/A')[:60]}\")
        print(f\"    URL: {p.get('url', 'N/A')}\")
except: pass
" 2>/dev/null || echo "  $pages"
    else
        echo "  Aucune page active"
    fi
}

# ── 5. Fetch URL test ───────────────────────────────────────────────────
verify_url_fetch() {
    local url="${1:-https://example.com}"

    header "Test rendu URL : $url"

    if ! curl -s "http://localhost:$CHROMIUM_PORT/json/version" &>/dev/null; then
        error "Chromium ne répond pas sur le port $CHROMIUM_PORT"
        info "Lance d'abord : $0 --launch-only"
        return 1
    fi

    info "Envoi de la requête à Chromium..."

    local response
    response=$(curl -s -X PUT "http://localhost:$CHROMIUM_PORT/json/new?$url" 2>/dev/null || echo "")

    if [ -z "$response" ]; then
        # Fallback: navigation directe via WebSocket
        warn "PUT /json/new échoué, fallback via websocket pas dispo en bash"
        warn "Utilise plutôt Chrome DevTools Protocol directement:"
        echo "    curl -s http://localhost:$CHROMIUM_PORT/json"
        return 1
    fi

    echo ""
    echo "  ✅ Page chargée :"
    echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"

    local page_id
    page_id=$(echo "$response" | python3 -c "
import json, sys
try: print(json.load(sys.stdin).get('id',''))
except: pass
" 2>/dev/null)

    if [ -n "$page_id" ]; then
        info "Page ID: $page_id"
        info "Screenshot: http://localhost:$CHROMIUM_PORT/devtools/screenshot/$page_id"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Chromium Headless — GCS VPS Installer                    ║"
echo "║   $(date '+%Y-%m-%d %H:%M:%S')"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

case "${1:-all}" in
    --install-only|-i)
        install_chromium
        ;;
    --launch-only|-l)
        launch_chromium
        add_to_tmux
        ;;
    --status|-s)
        verify_status
        ;;
    --verify-url|-v)
        verify_url_fetch "${2:-https://example.com}"
        ;;
    all|--all|-a)
        install_chromium
        launch_chromium
        add_to_tmux
        verify_status
        ;;
    *)
        echo "Usage: $0 [option]"
        echo ""
        echo "  (no args)     Install + Launch + Tmux + Status"
        echo "  --install-only  Install Chromium seulement"
        echo "  --launch-only   Launch Chromium + tmux (si déjà installé)"
        echo "  --status        Vérifier si Chromium tourne"
        echo "  --verify-url    Tester le rendu d'une URL"
        echo ""
        echo "Exemples:"
        echo "  $0                              # Full setup"
        echo "  $0 --launch-only                # Relancer après crash"
        echo "  $0 --status                     # Vérifier l'état"
        echo "  $0 --verify-url https://google.com"
        ;;
esac

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Done.                                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
