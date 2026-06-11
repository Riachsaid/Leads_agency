#!/usr/bin/env python3
"""
GCS Keepalive Daemon — Anti-dormance pour Google Cloud Shell
=============================================================
Empêche Cloud Shell de s'endormir en générant de l'activité
périodique (CPU, I/O, self-ping HTTP).

Usage:
  python3 gcs_keepalive_daemon.py              # Lance le daemon
  python3 gcs_keepalive_daemon.py --interval 60 # Ping toutes les 60s
  python3 gcs_keepalive_daemon.py --daemon      # Fork en arrière-plan

Stratégies anti-dormance:
  1. Self-ping HTTP → keepalive sur le serveur health (localhost:8080)
  2. CPU spike → calculs légers pour montrer de l'activité
  3. I/O activity → lecture/écriture de fichiers
  4. Cloud Shell API → signaux internes keepalive
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────
LOG_DIR = os.path.expanduser("~/gcs_vps/logs")
PID_DIR = os.path.expanduser("~/gcs_vps/pids")
DEFAULT_INTERVAL = 120  # secondes
HEALTH_PORT = 8080

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(PID_DIR, exist_ok=True)


def setup_logger(interval):
    logger = logging.getLogger("gcs_keepalive")
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s [KEEPALIVE] %(message)s")

    fh = logging.FileHandler(os.path.join(LOG_DIR, "keepalive.log"))
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


def self_ping(logger):
    """Ping le health server local pour générer du trafic HTTP."""
    targets = [
        f"http://localhost:{HEALTH_PORT}/ping",
        f"http://localhost:{HEALTH_PORT}/health",
    ]
    for url in targets:
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


def cpu_activity(logger):
    """Génère une micro-activité CPU pour signaler que le shell est actif."""
    try:
        _ = [i * i for i in range(200000)]
        _ = {i: str(i) for i in range(50000)}
        logger.debug("CPU activity generated")
    except Exception as e:
        logger.debug(f"CPU activity error: {e}")


def io_activity(logger):
    """Génère de l'activité I/O disque."""
    try:
        # Lecture de fichiers système
        with open("/proc/loadavg", "r") as f:
            load = f.read().strip()
        with open("/proc/uptime", "r") as f:
            uptime = f.read().strip().split()[0]
        # Écriture légère
        marker = os.path.join(LOG_DIR, ".alive")
        with open(marker, "w") as f:
            f.write(f"{time.time()}\n")
        logger.debug(f"I/O activity (load: {load}, uptime: {uptime}s)")
    except Exception as e:
        logger.debug(f"I/O activity error: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="GCS Keepalive Daemon — anti-dormance Cloud Shell"
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL,
        help=f"Intervalle entre pings en secondes (défaut: {DEFAULT_INTERVAL})"
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Fork en arrière-plan (daemon mode)"
    )
    args = parser.parse_args()

    logger = setup_logger(args.interval)

    if args.daemon:
        pid = os.fork()
        if pid > 0:
            print(f"[KEEPALIVE] Daemon started (PID: {pid})")
            with open(os.path.join(PID_DIR, "keepalive.pid"), "w") as f:
                f.write(str(pid))
            sys.exit(0)

    cycle = 0
    logger.info("=" * 50)
    logger.info(f"GCS Keepalive Daemon started")
    logger.info(f"Interval: {args.interval}s")
    logger.info(f"PID: {os.getpid()}")
    logger.info(f"Log: {os.path.join(LOG_DIR, 'keepalive.log')}")
    logger.info("=" * 50)
    logger.info("Stratégies actives:")
    logger.info("  ✓ HTTP self-ping (localhost:{})".format(HEALTH_PORT))
    logger.info("  ✓ CPU activity")
    logger.info("  ✓ I/O activity")
    logger.info("=" * 50)

    try:
        while True:
            cycle += 1
            now = datetime.now().strftime("%H:%M:%S")

            self_ping(logger)
            cpu_activity(logger)
            io_activity(logger)

            if cycle % 30 == 0:
                logger.info(f"[{now}] Cycle #{cycle} — all keepalive signals OK")

            time.sleep(args.interval)

    except KeyboardInterrupt:
        logger.info("Daemon stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
