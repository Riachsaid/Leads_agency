#!/usr/bin/env python3
"""
DeepSeek-V4-Flash Agent — Ultra-Fast LLM + Chromium Browser Automation
======================================================================
Intégration officielle de DeepSeek-V4-Flash (api.deepseek.com) avec
notre Chromium headless via Chrome DevTools Protocol (CDP).

Usage:
  export DEEPSEEK_API_KEY="sk-..."

  # Chat basique
  python3 deepseek_v4_agent.py chat "Explique le protocole CDP"

  # Chat avec contexte système
  python3 deepseek_v4_agent.py chat "Résume cette page" \
    --system "Tu es un assistant technique"

  # Contrôle navigateur
  python3 deepseek_v4_agent.py browser navigate https://example.com
  python3 deepseek_v4_agent.py browser extract "Titre et paragraphes"
  python3 deepseek_v4_agent.py browser screenshot /tmp/page.png

  # Analyse intelligente d'une page via LLM
  python3 deepseek_v4_agent.py analyze https://example.com \
    "Quels sont les services proposés ?"

  # Session interactive
  python3 deepseek_v4_agent.py interactive

  # Mode API stateless
  python3 deepseek_v4_agent.py api --port 5051
"""

import argparse
import base64
import json
import logging
import os
import sys
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DEEPSEEK] %(levelname)s %(message)s",
)
logger = logging.getLogger("deepseek_v4")

# ── Config ────────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-v4-flash"
CHROMIUM_CDP_URL = "http://localhost:9222"

# ── DeepSeek V4 Flash Client ─────────────────────────────────────────────

class DeepSeekV4Flash:
    """
    Client OpenAI-compatible pour DeepSeek-V4-Flash.
    Utilise l'endpoint officiel api.deepseek.com/v1.
    Optimisé pour la génération ultra-rapide de V4-Flash.
    """

    def __init__(self, api_key: str = None, model: str = DEFAULT_MODEL):
        self.api_key = api_key or DEEPSEEK_API_KEY
        self.model = model
        self.base_url = DEEPSEEK_BASE_URL

        if not self.api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY non définie. "
                "Exportez-la : export DEEPSEEK_API_KEY='sk-...'"
            )

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages, **kwargs):
        return {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
            "top_p": kwargs.get("top_p", 0.95),
            "stream": kwargs.get("stream", False),
        }

    def chat(self, messages: list, **kwargs) -> dict:
        """
        Chat completion synchrone.
        'messages' = [{"role": "user"|"system"|"assistant", "content": "..."}]
        """
        import requests
        payload = self._payload(messages, **kwargs)
        logger.debug(f"Chat: model={self.model}, messages={len(messages)}")

        r = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
            timeout=kwargs.get("timeout", 60),
        )
        r.raise_for_status()
        return r.json()

    def chat_stream(self, messages: list, **kwargs):
        """
        Chat completion en streaming (génération token par token).
        Yield : (token_text, is_final_bool)
        """
        import requests
        payload = self._payload(messages, stream=True, **kwargs)

        r = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
            stream=True,
            timeout=kwargs.get("timeout", 120),
        )
        r.raise_for_status()

        for line in r.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8")
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    yield ("", True)
                    return
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    yield (content, False)
                except json.JSONDecodeError:
                    continue

    def generate(self, prompt: str, system: str = None, **kwargs) -> str:
        """Génération rapide depuis un prompt texte simple."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        result = self.chat(messages, **kwargs)
        return result["choices"][0]["message"]["content"]

    def generate_stream(self, prompt: str, system: str = None, **kwargs):
        """Génération en streaming depuis un prompt texte."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        yield from self.chat_stream(messages, **kwargs)


# ── Chrome DevTools Protocol (CDP) Client ───────────────────────────────

class ChromiumCDP:
    """
    Connexion à Chromium headless via CDP (port 9222).
    Permet de contrôler le navigateur depuis DeepSeek V4.
    """

    def __init__(self, cdp_url: str = CHROMIUM_CDP_URL):
        self.cdp_url = cdp_url
        self._ws_url = None
        self._target_id = None
        self._conn = None

    def _get_ws_url(self) -> str:
        """Récupère l'URL WebSocket du premier page target."""
        import requests
        r = requests.get(f"{self.cdp_url}/json", timeout=5)
        r.raise_for_status()
        targets = r.json()
        if not targets:
            raise RuntimeError("Aucun target Chromium disponible. Lancez d'abord chromium.")
        # Prendre le premier target de type "page"
        for t in targets:
            if t.get("type") == "page":
                self._target_id = t["id"]
                return t["webSocketDebuggerUrl"]
        # Fallback
        self._target_id = targets[0]["id"]
        return targets[0]["webSocketDebuggerUrl"]

    def _connect(self):
        """Établit la connexion WebSocket CDP."""
        import websocket
        if self._conn is None:
            ws_url = self._get_ws_url()
            self._conn = websocket.create_connection(ws_url, timeout=10)

    def _disconnect(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _send(self, method: str, params: dict = None) -> dict:
        """Envoie une commande CDP et retourne le résultat."""
        import uuid
        self._connect()
        msg_id = str(uuid.uuid4())[:8]
        msg = {
            "id": msg_id,
            "method": method,
            "params": params or {},
        }
        self._conn.send(json.dumps(msg))

        while True:
            response = json.loads(self._conn.recv())
            if response.get("id") == msg_id:
                return response.get("result", {})
            if "error" in response:
                raise RuntimeError(f"CDP error: {response['error']}")

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, *args):
        self._disconnect()

    # ── Navigation ──
    def navigate(self, url: str) -> dict:
        """Navigue vers une URL."""
        logger.info(f"Navigation: {url}")
        return self._send("Page.navigate", {"url": url})

    def get_title(self) -> str:
        """Retourne le titre de la page."""
        result = self._send("Runtime.evaluate", {
            "expression": "document.title"
        })
        return result.get("result", {}).get("value", "")

    def get_text(self) -> str:
        """Extrait tout le texte visible de la page."""
        result = self._send("Runtime.evaluate", {
            "expression": "document.body.innerText"
        })
        return result.get("result", {}).get("value", "")

    def get_html(self) -> str:
        """Extrait le HTML complet."""
        result = self._send("Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML"
        })
        return result.get("result", {}).get("value", "")

    def click(self, selector: str):
        """Clique sur un élément via sélecteur CSS."""
        self._send("Runtime.evaluate", {
            "expression": f"""
                (() => {{
                    const el = document.querySelector('{selector}');
                    if (el) el.click();
                    return {{ clicked: !!el }};
                }})()
            """
        })

    def screenshot(self, path: str = None, format: str = "png") -> Optional[str]:
        """
        Capture d'écran.
        Si path est fourni, sauvegarde le fichier.
        Retourne le base64 si path est None.
        """
        result = self._send("Page.captureScreenshot", {"format": format})
        data = result.get("data", "")
        if path:
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
            logger.info(f"Screenshot sauvegardé: {path}")
            return None
        return data

    def extract_links(self) -> list:
        """Extrait tous les liens de la page."""
        result = self._send("Runtime.evaluate", {
            "expression": """
                Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    text: a.innerText.trim(),
                    href: a.href
                }))
            """
        })
        return result.get("result", {}).get("value", [])

    def extract_structured(self, prompt: str) -> dict:
        """
        Extrait des données structurées via JS.
        Exemple: "return {title: document.title, links: document.querySelectorAll('a').length}"
        """
        result = self._send("Runtime.evaluate", {
            "expression": f"(() => {{ {prompt} }})()"
        })
        return result.get("result", {}).get("value", {})


# ── Browser Agent (LLM + CDP) ────────────────────────────────────────────

class BrowserAgent:
    """
    Agent intelligent combinant DeepSeek-V4-Flash + Chromium CDP.
    Permet au LLM de naviguer, analyser et interagir avec des pages web.
    """

    def __init__(self, api_key: str = None, model: str = DEFAULT_MODEL):
        self.llm = DeepSeekV4Flash(api_key, model)
        self.cdp = ChromiumCDP()

    def analyze_page(self, url: str, question: str = None) -> str:
        """
        Navigue vers une URL, extrait le contenu,
        puis utilise DeepSeek V4 pour l'analyser.
        """
        with self.cdp:
            self.cdp.navigate(url)
            time.sleep(2)
            title = self.cdp.get_title()
            text = self.cdp.get_text()[:15000]

        prompt = f"""Analyse la page web suivante.

Titre: {title}
URL: {url}

Contenu:
{text[:12000]}

{ "Question: " + question if question else "Fais un résumé concis de cette page." }
"""
        result = self.llm.generate(
            prompt,
            system="Tu es un analyste web expert. Réponds de façon claire et structurée.",
            temperature=0.3,
        )
        return result

    def smart_search(self, query: str) -> str:
        """
        Recherche intelligente : navigue sur Google/DuckDuckGo,
        extrait les résultats, utilise DeepSeek V4 pour synthétiser.
        """
        search_url = f"https://lite.duckduckgo.com/lite/?q={query.replace(' ', '+')}"
        with self.cdp:
            self.cdp.navigate(search_url)
            time.sleep(2)
            text = self.cdp.get_text()[:10000]

        prompt = f"""Recherche web pour: {query}

Résultats:
{text[:8000]}

Synthétise les informations les plus pertinentes pour répondre à la recherche."""
        return self.llm.generate(
            prompt,
            system="Tu es un moteur de recherche intelligent. Synthétise les résultats.",
            temperature=0.2,
        )

    def suggest_action(self, page_text: str, goal: str) -> str:
        """
        Analyse le texte d'une page et suggère la prochaine action
        (navigation, click, extraction).
        """
        prompt = f"""Page actuelle:
{page_text[:8000]}

Objectif: {goal}

Quelle est la prochaine action recommandée ?
Réponds en une phrase précise."""
        return self.llm.generate(
            prompt,
            system="Tu es un agent de navigation web. Propose des actions concrètes.",
            temperature=0.3,
        )


# ── API Server (stateless) ────────────────────────────────────────────────

class DeepSeekAPIHandler(BaseHTTPRequestHandler):
    """Serveur API minimal pour exposer DeepSeek V4 via HTTP."""

    agent: BrowserAgent = None

    def _json(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/v1/chat":
            messages = body.get("messages", [])
            try:
                result = self.agent.llm.chat(messages)
                self._json(200, result)
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif self.path == "/v1/generate":
            prompt = body.get("prompt", "")
            system = body.get("system")
            try:
                text = self.agent.llm.generate(prompt, system)
                self._json(200, {"response": text})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif self.path == "/v1/analyze":
            url = body.get("url", "")
            question = body.get("question")
            try:
                result = self.agent.analyze_page(url, question)
                self._json(200, {"analysis": result})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif self.path == "/v1/screenshot":
            url = body.get("url")
            try:
                agent = BrowserAgent()
                with agent.cdp:
                    if url:
                        agent.cdp.navigate(url)
                        time.sleep(2)
                    data = agent.cdp.screenshot()
                self._json(200, {"screenshot": data})
            except Exception as e:
                self._json(500, {"error": str(e)})

        else:
            self._json(404, {"error": "endpoint not found"})

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "alive", "model": DEFAULT_MODEL})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


def serve_api(port: int = 5051):
    """Lance le serveur API."""
    agent = BrowserAgent()
    DeepSeekAPIHandler.agent = agent
    server = HTTPServer(("0.0.0.0", port), DeepSeekAPIHandler)
    logger.info(f"DeepSeek V4 API server: http://localhost:{port}")
    logger.info(f"  POST /v1/chat        — Chat completion")
    logger.info(f"  POST /v1/generate    — Génération simple")
    logger.info(f"  POST /v1/analyze     — Analyse de page web")
    logger.info(f"  POST /v1/screenshot  — Capture d'écran")
    logger.info(f"  GET  /health         — Health check")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DeepSeek-V4-Flash Agent — LLM + Browser Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  %(prog)s chat "Explique le protocole CDP"
  %(prog)s chat "Résume" --system "Assistant technique"
  %(prog)s browser navigate https://example.com
  %(prog)s browser extract "Titre et paragraphes"
  %(prog)s browser screenshot /tmp/page.png
  %(prog)s analyze https://example.com "Quels services ?"
  %(prog)s interactive
  %(prog)s api --port 5051
        """,
    )
    parser.add_argument("--api-key", help="Clé API DeepSeek (ou variable DEEPSEEK_API_KEY)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Modèle (défaut: {DEFAULT_MODEL})")

    sub = parser.add_subparsers(dest="mode", required=True)

    # chat
    chat_p = sub.add_parser("chat", help="Chat completion")
    chat_p.add_argument("prompt", nargs="?", help="Message utilisateur")
    chat_p.add_argument("--system", help="Contexte système")
    chat_p.add_argument("--stream", action="store_true", help="Mode streaming")
    chat_p.add_argument("--temperature", type=float, default=0.7)

    # browser
    browser_p = sub.add_parser("browser", help="Contrôle Chromium via CDP")
    browser_p.add_argument("action", choices=["navigate", "extract", "screenshot", "title", "links"])
    browser_p.add_argument("value", nargs="?", help="URL (navigate) ou chemin fichier (screenshot)")

    # analyze
    analyze_p = sub.add_parser("analyze", help="Analyser une page web via LLM")
    analyze_p.add_argument("url", help="URL à analyser")
    analyze_p.add_argument("question", nargs="?", help="Question spécifique")

    # interactive
    sub.add_parser("interactive", help="Session interactive")

    # api
    api_p = sub.add_parser("api", help="Lancer le serveur API")
    api_p.add_argument("--port", type=int, default=5051)

    args = parser.parse_args()

    # Initialiser le client
    try:
        client = DeepSeekV4Flash(api_key=args.api_key, model=args.model)
    except ValueError as e:
        logger.error(e)
        sys.exit(1)

    if args.mode == "chat":
        if not args.prompt and sys.stdin.isatty():
            logger.error("Fournissez un prompt ou pipez du texte")
            sys.exit(1)

        prompt = args.prompt
        if not prompt:
            prompt = sys.stdin.read().strip()

        messages = []
        if args.system:
            messages.append({"role": "system", "content": args.system})
        messages.append({"role": "user", "content": prompt})

        if args.stream:
            logger.info("Streaming DeepSeek V4 Flash...")
            for token, is_final in client.chat_stream(messages, temperature=args.temperature):
                print(token, end="", flush=True)
                if is_final:
                    print()
        else:
            result = client.chat(messages, temperature=args.temperature)
            print(result["choices"][0]["message"]["content"])

    elif args.mode == "browser":
        cdp = ChromiumCDP()
        with cdp:
            if args.action == "navigate":
                if not args.value:
                    logger.error("URL requise")
                    sys.exit(1)
                cdp.navigate(args.value)
                time.sleep(1)
                print(f"Titre: {cdp.get_title()}")

            elif args.action == "extract":
                print(cdp.get_text())

            elif args.action == "title":
                print(cdp.get_title())

            elif args.action == "links":
                links = cdp.extract_links()
                for l in links[:20]:
                    print(f"  • {l['text'][:60]:60s} → {l['href']}")

            elif args.action == "screenshot":
                path = args.value or f"screenshot_{int(time.time())}.png"
                cdp.screenshot(path)
                print(f"Screenshot: {path}")

    elif args.mode == "analyze":
        agent = BrowserAgent(api_key=args.api_key, model=args.model)
        result = agent.analyze_page(args.url, args.question)
        print(result)

    elif args.mode == "interactive":
        logger.info("Session interactive DeepSeek V4 Flash. Ctrl+C pour quitter.")
        print()
        history = []
        while True:
            try:
                prompt = input("🧠 Vous: ")
                if not prompt:
                    continue
                history.append({"role": "user", "content": prompt})

                print("🤖 DeepSeek: ", end="", flush=True)
                response = ""
                for token, is_final in client.chat_stream(history):
                    print(token, end="", flush=True)
                    response += token
                    if is_final:
                        print()
                history.append({"role": "assistant", "content": response})

            except KeyboardInterrupt:
                print("\nAu revoir !")
                break

    elif args.mode == "api":
        serve_api(args.port)


if __name__ == "__main__":
    main()
