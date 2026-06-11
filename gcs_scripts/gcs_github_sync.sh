#!/usr/bin/env bash
# ===========================================================================
# gcs_github_sync.sh — Sync GCS VPS ↔ GitHub
# ===========================================================================
# Synchronise les fichiers critiques avec GitHub pour :
#   - Backup automatique du code et des données
#   - Récupération rapide après un arrêt Cloud Shell
#   - Partage entre plusieurs sessions Cloud Shell
#
# Usage:
#   ./gcs_github_sync.sh                    # Push (commit + push)
#   ./gcs_github_sync.sh --pull             # Pull (récupère les dernières modifs)
#   ./gcs_github_sync.sh --status           # Voir l'état du repo
#   ./gcs_github_sync.sh --auto             # Auto: pull → merge → push
#
# Configuration:
#   1. Crée un repo GitHub privé
#   2. git config --global user.name "TON_NOM"
#   3. git config --global user.email "TON_EMAIL"
#   4. Ajoute un token GitHub : gh auth login (ou ssh-key)
# ===========================================================================
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────
GCS_WORK="$HOME/gcs_vps"
REPO_DIR="$GCS_WORK/repo"

# Fichiers/dossiers à inclure (chemins relatifs au repo)
INCLUDE_PATTERNS=(
    "*.py"
    "*.sh"
    "*.json"
    "*.csv"
    "*.md"
    "*.txt"
    "*.env"
    "data/"
    "logs/*.log"
)

# Fichiers à exclure
EXCLUDE_PATTERNS=(
    "__pycache__"
    "*.pyc"
    ".git"
    "node_modules"
    "venv"
    ".cache"
    "pids/"
)

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# ── Couleurs ──────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Vérifications ─────────────────────────────────────────────────────────
if [ ! -d "$REPO_DIR/.git" ]; then
    error "Aucun repo Git trouvé dans $REPO_DIR"
    echo ""
    echo "  Pour initialiser :"
    echo "    cd $GCS_WORK"
    echo "    git clone https://github.com/TON_USER/TON_REPO.git repo"
    echo ""
    echo "  Ou créer un nouveau repo :"
    echo "    mkdir -p $GCS_WORK/repo && cd $GCS_WORK/repo"
    echo "    git init"
    echo "    git remote add origin https://github.com/TON_USER/TON_REPO.git"
    echo "    git add -A && git commit -m 'Initial commit' && git push -u origin main"
    exit 1
fi

# ── Sync Functions ────────────────────────────────────────────────────────

do_pull() {
    info "Pulling from GitHub..."
    cd "$REPO_DIR"
    git pull --ff-only origin main 2>&1 | sed 's/^/  /' || {
        warn "Fast-forward failed. Trying merge..."
        git pull origin main 2>&1 | sed 's/^/  /'
    }
    info "Pull complete."
}

do_push() {
    cd "$REPO_DIR"

    # Copier les fichiers depuis GCS_WORK vers le repo
    if [ -d "$GCS_WORK/data" ]; then
        cp -r "$GCS_WORK/data" "$REPO_DIR/" 2>/dev/null || true
    fi
    if [ -f "$GCS_WORK/.ngrok_url" ]; then
        cp "$GCS_WORK/.ngrok_url" "$REPO_DIR/" 2>/dev/null || true
    fi

    # Vérifier s'il y a des changements
    if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
        info "Rien à commit. Working tree clean."
        return 0
    fi

    # Afficher les changements
    echo "  Changements détectés :"
    git status --short 2>&1 | sed 's/^/    /'

    # Stage, commit, push
    git add -A

    # Définir .gitignore s'il n'existe pas
    if [ ! -f ".gitignore" ]; then
        cat > ".gitignore" << 'GIEOF'
__pycache__/
*.pyc
.git/
node_modules/
venv/
.cache/
pids/
*.log
.env
GIEOF
        git add .gitignore
    fi

    git commit -m "GCS auto-sync $TIMESTAMP"
    info "Committing: GCS auto-sync $TIMESTAMP"

    git push origin main 2>&1 | sed 's/^/  /'
    info "Push complete."
}

do_auto() {
    info "=== Auto Sync ==="
    do_pull
    do_push
    info "=== Auto Sync Complete ==="
}

do_status() {
    cd "$REPO_DIR"
    echo "=== Git Status ==="
    git status
    echo ""
    echo "=== Recent Commits ==="
    git log --oneline -10
    echo ""
    echo "=== Remote ==="
    git remote -v
}

# ── Main ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  GCS → GitHub Sync — $TIMESTAMP"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

case "${1:-push}" in
    --pull|-p)
        do_pull
        ;;
    --status|-s)
        do_status
        ;;
    --auto|-a)
        do_auto
        ;;
    push|--push|*)
        do_push
        ;;
esac

echo ""
info "Sync finished — $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
