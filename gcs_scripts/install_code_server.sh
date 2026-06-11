#!/usr/bin/env bash
# ===========================================================================
# install_code_server.sh — OpenVSCode Server (code-server) dans GCS VPS
# ===========================================================================
# Installe code-server (VS Code Web), le lie au port 3000,
# et l'intègre dans la session tmux gcs_vps.
#
# Usage:
#   ./install_code_server.sh                    # Install + launch + tmux
#   ./install_code_server.sh --install-only     # Install sans launch
#   ./install_code_server.sh --launch-only      # Launch sans install
#   ./install_code_server.sh --status           # Vérifier l'état
#   ./install_code_server.sh --password <pass>  # Définir un mot de passe
# ===========================================================================
set -euo pipefail

# ── Couleurs ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
header(){ echo -e "${CYAN}━━━ $1 ━━━${NC}"; }

# ── Configuration ─────────────────────────────────────────────────────────
CODE_SERVER_BIN="$HOME/.local/bin/code-server"
CODE_SERVER_PORT=3000
CODE_SERVER_LOG="$HOME/gcs_vps/logs/code-server.log"
CODE_SERVER_PID_FILE="$HOME/gcs_vps/pids/code-server.pid"
CODE_SERVER_CONFIG="$HOME/.config/code-server/config.yaml"
TMUX_SESSION="gcs_vps"
GCS_WORK="$HOME/gcs_vps"

mkdir -p "$GCS_WORK/logs" "$GCS_WORK/pids"
mkdir -p "$(dirname "$CODE_SERVER_CONFIG")"

# ── 1. Install code-server ──────────────────────────────────────────────
install_code_server() {
    header "Installation de code-server (VS Code Web)"

    if command -v "$CODE_SERVER_BIN" &>/dev/null; then
        local ver
        ver=$($CODE_SERVER_BIN --version 2>/dev/null | head -1 || echo "?")
        info "code-server déjà installé : $ver"
        return 0
    fi

    # Install script officiel de Coder
    info "Téléchargement et installation..."
    curl -fsSL https://code-server.dev/install.sh | sh

    # S'assurer que le binaire est dans le PATH
    if ! command -v "$CODE_SERVER_BIN" &>/dev/null; then
        export PATH="$HOME/.local/bin:$PATH"
    fi

    if command -v "$CODE_SERVER_BIN" &>/dev/null; then
        local ver
        ver=$($CODE_SERVER_BIN --version 2>/dev/null | head -1 || echo "?")
        info "code-server installé : $ver"
    else
        error "Échec de l'installation de code-server"
        error "Essayez : curl -fsSL https://code-server.dev/install.sh | sh"
        exit 1
    fi
}

# ── 2. Configure code-server ────────────────────────────────────────────
configure_code_server() {
    header "Configuration de code-server"

    # Arrêter une éventuelle instance en cours
    $CODE_SERVER_BIN --stop 2>/dev/null || true

    local password="${1:-kLaSiiNkOv1988@}"

    cat > "$CODE_SERVER_CONFIG" << CONFEOF
bind-addr: 0.0.0.0:3000
auth: password
password: "${password}"
cert: false
disable-telemetry: true
disable-update-check: true
disable-file-downloads: false
disable-workspace-trust: false
CONFEOF

    info "Configuration écrite dans $CODE_SERVER_CONFIG"
    info "Mot de passe: ${password}"

    # Sauvegarder le mot de passe dans un fichier lisible par start_services
    echo "$password" > "$GCS_WORK/.code-server-password"
    chmod 600 "$GCS_WORK/.code-server-password"
}

# ── 3. Launch code-server ────────────────────────────────────────────────
launch_code_server() {
    header "Lancement de code-server sur le port $CODE_SERVER_PORT"

    # S'assurer que PATH contient ~/.local/bin
    export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:/usr/bin:$PATH"

    # Tuer l'ancienne instance
    if [ -f "$CODE_SERVER_PID_FILE" ]; then
        OLD_PID=$(cat "$CODE_SERVER_PID_FILE" 2>/dev/null || echo "")
        if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
            warn "Arrêt de l'ancienne instance (PID $OLD_PID)..."
            $CODE_SERVER_BIN --stop 2>/dev/null || kill "$OLD_PID" 2>/dev/null || true
            sleep 2
        fi
    fi

    # Vérifier si le port est déjà utilisé
    if command -v lsof &>/dev/null && lsof -i :"$CODE_SERVER_PORT" &>/dev/null 2>&1; then
        local existing_pid
        existing_pid=$(lsof -ti :"$CODE_SERVER_PORT" 2>/dev/null || echo "")
        if [ -n "$existing_pid" ]; then
            info "code-server déjà actif sur le port $CODE_SERVER_PORT (PID $existing_pid)"
            echo "$existing_pid" > "$CODE_SERVER_PID_FILE"
            return 0
        fi
    fi

    # Vérifier que le binaire existe
    if ! command -v "$CODE_SERVER_BIN" &>/dev/null; then
        error "code-server introuvable dans le PATH"
        info "Installation: curl -fsSL https://code-server.dev/install.sh | sh"
        return 1
    fi

    # Vérifier que le password file existe
    if [ ! -f "$GCS_WORK/.code-server-password" ]; then
        warn "Fichier password introuvable — création avec le password par défaut"
        echo "kLaSiiNkOv1988@" > "$GCS_WORK/.code-server-password"
        chmod 600 "$GCS_WORK/.code-server-password"
    fi

    # Lire le mot de passe du fichier
    local password
    password=$(cat "$GCS_WORK/.code-server-password")

    # Vérifier que le mot de passe est aussi dans config.yaml
    if grep -q "^password:" "$CODE_SERVER_CONFIG" 2>/dev/null; then
        # S'assurer que c'est le bon
        sed -i "s/^password:.*/password: \"${password}\"/" "$CODE_SERVER_CONFIG"
    else
        echo "password: \"${password}\"" >> "$CODE_SERVER_CONFIG"
    fi

    # Définir le mot de passe via variable d'env (override config.yaml si présent)
    export PASSWORD="$password"
    export HASHED_PASSWORD=""

    # Nettoyer les vieux locks avant de lancer
    rm -f /tmp/code-server*.lock 2>/dev/null || true

    nohup "$CODE_SERVER_BIN" \
        --bind-addr "0.0.0.0:$CODE_SERVER_PORT" \
        --auth password \
        --disable-telemetry \
        --disable-update-check \
        > "$CODE_SERVER_LOG" 2>&1 &

    local pid=$!
    echo "$pid" > "$CODE_SERVER_PID_FILE"
    info "code-server démarré (PID $pid)"

    # Attendre que le port soit ouvert (max 15s)
    for i in $(seq 1 15); do
        if curl -s -o /dev/null -w "" "http://localhost:$CODE_SERVER_PORT" &>/dev/null 2>&1; then
            echo ""
            info "✅ code-server prêt sur http://localhost:$CODE_SERVER_PORT"
            info "   Mot de passe: ${password}"
            # Vérifier le binding
            local bound_addr
            bound_addr=$(ss -tlnp 2>/dev/null | grep ":$CODE_SERVER_PORT " | awk '{print $4}' || \
                         netstat -tlnp 2>/dev/null | grep ":$CODE_SERVER_PORT " | awk '{print $4}' || \
                         echo "unknown")
            if echo "$bound_addr" | grep -q "^0.0.0.0:" || echo "$bound_addr" | grep -q "^\*:"; then
                info "   Binding:  ${bound_addr} (OK — accessible depuis l'extérieur)"
            elif [ -n "$bound_addr" ] && [ "$bound_addr" != "unknown" ]; then
                warn "   Binding:  ${bound_addr} (⚠️  devrait être 0.0.0.0:3000)"
            fi
            return 0
        fi
        sleep 1
    done

    warn "⚠️  Port $CODE_SERVER_PORT pas encore ouvert après 15s. Logs:"
    echo "    tail -30 $CODE_SERVER_LOG"
    echo ""
    echo "  Dernières lignes du log :"
    tail -10 "$CODE_SERVER_LOG" 2>/dev/null | sed 's/^/    /'
}

# ── 4. Intégration tmux ──────────────────────────────────────────────────
add_to_tmux() {
    header "Intégration dans tmux session '$TMUX_SESSION'"

    if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        warn "Session tmux '$TMUX_SESSION' n'existe pas — création"
        tmux new-session -d -s "$TMUX_SESSION" -n "vps"
        tmux send-keys -t "$TMUX_SESSION:0.0" "cd $GCS_WORK" Enter
    fi

    if tmux list-windows -t "$TMUX_SESSION" 2>/dev/null | grep -q "vscode"; then
        info "Window 'vscode' existe déjà dans tmux"
        return 0
    fi

    # Créer window "vscode"
    tmux new-window -t "$TMUX_SESSION" -n "vscode"
    tmux send-keys -t "$TMUX_SESSION:vscode.0" \
        "echo '━━━ code-server (VS Code Web) ━━━'; " \
        "echo 'URL:  http://localhost:$CODE_SERVER_PORT'; " \
        "echo 'Pass: $(cat "$GCS_WORK/.code-server-password" 2>/dev/null || echo "voir config")'; " \
        "echo 'Logs:'; " \
        "tail -f $CODE_SERVER_LOG" Enter

    info "Window 'vscode' créée dans tmux session '$TMUX_SESSION'"
}

# ── 5. Status ────────────────────────────────────────────────────────────
verify_status() {
    header "Status code-server"

    if ! command -v "$CODE_SERVER_BIN" &>/dev/null; then
        error "code-server n'est pas installé"
        return 1
    fi

    local ver
    ver=$($CODE_SERVER_BIN --version 2>/dev/null | head -1 || echo "N/A")
    echo "  Binaire: $CODE_SERVER_BIN"
    echo "  Version: $ver"
    echo "  Port:    $CODE_SERVER_PORT"

    if [ -f "$CODE_SERVER_PID_FILE" ]; then
        local pid
        pid=$(cat "$CODE_SERVER_PID_FILE" 2>/dev/null || echo "")
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo -e "  PID:     ${GREEN}$pid (running)${NC}"
        else
            echo -e "  PID:     ${RED}$pid (dead)${NC}"
        fi
    else
        echo -e "  PID:     ${YELLOW}N/A${NC}"
    fi

    if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$CODE_SERVER_PORT" 2>/dev/null | grep -q 200; then
        echo -e "  Status:  ${GREEN}✅ Répond sur http://localhost:$CODE_SERVER_PORT${NC}"
    else
        echo -e "  Status:  ${RED}❌ Ne répond pas${NC}"
        echo "  Logs: tail -20 $CODE_SERVER_LOG"
    fi

    if [ -f "$GCS_WORK/.code-server-password" ]; then
        echo ""
        echo "  Mot de passe: $(cat "$GCS_WORK/.code-server-password")"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   OpenVSCode Server — GCS VPS Installer                    ║"
echo "║   $(date '+%Y-%m-%d %H:%M:%S')"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

case "${1:-all}" in
    --install-only|-i)
        install_code_server
        configure_code_server "${2:-}"
        ;;
    --launch-only|-l)
        launch_code_server
        add_to_tmux
        ;;
    --status|-s)
        verify_status
        ;;
    --password|-p)
        if [ -z "${2:-}" ]; then
            error "Usage: $0 --password <mot_de_passe>"
            exit 1
        fi
        configure_code_server "$2"
        launch_code_server
        ;;
    all|--all|-a)
        install_code_server
        configure_code_server "${2:-}"
        launch_code_server
        add_to_tmux
        verify_status
        ;;
    *)
        echo "Usage: $0 [option] [password]"
        echo ""
        echo "  (no args)     Install + Config + Launch + Tmux + Status"
        echo "  --install-only  Install code-server seulement"
        echo "  --launch-only   Launch + tmux (si déjà installé)"
        echo "  --status        Vérifier l'état"
        echo "  --password <p>  Définir le mot de passe"
        echo ""
        echo "Exemples:"
        echo "  $0                                     # Full setup"
        echo "  $0 --password monMotDePasse123         # Avec password personnalisé"
        echo "  $0 --launch-only                       # Relancer après crash"
        echo "  $0 --status                            # Vérifier l'état"
        ;;
esac

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Done.                                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
