"""Fetch the Effiplan work schedule and write it out as an .ics file.

First run: opens a real (visible) browser. If the corporate SSO doesn't log
you in silently, a login page will appear - log in yourself in that window,
the script is just waiting and watching network traffic, it never sees your
password. The authenticated session is then saved locally to
auth_state.json so future runs (e.g. from a scheduled task) can reuse it
without any login step, as long as the session/SSO is still valid.
"""

import base64
import json
import random
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

from extract_events import parse_events_response
from build_ics import build_ics

BASE_URL = "https://decathlon-effiplan-fr.cloud-horoquartz.fr/"
EVENTS_URL_PART = "/api/v3/activities/events"

PROJECT_DIR = Path(__file__).parent
STATE_FILE = PROJECT_DIR / "auth_state.json"
DOCS_DIR = PROJECT_DIR / "docs"
OUTPUT_FILE = DOCS_DIR / "planning.ics"

WEEKS_BACK = 1
WEEKS_FORWARD = 8

HEADLESS = "--headless" in sys.argv


def _wait_for_events_request(page, trigger, timeout_ms=180_000):
    """Call trigger(), then wait for the next POST to the events API and
    return its captured url/body/headers/status."""
    captured = {}

    def on_request(request):
        if EVENTS_URL_PART in request.url and request.method == "POST" and "url" not in captured:
            captured["url"] = request.url
            captured["headers"] = request.headers
            try:
                captured["body"] = json.loads(request.post_data or "{}")
            except json.JSONDecodeError:
                captured["body"] = {}

    def on_response(response):
        if EVENTS_URL_PART in response.url and "status" not in captured:
            captured["status"] = response.status

    page.on("request", on_request)
    page.on("response", on_response)
    try:
        trigger()
        deadline = datetime.now() + timedelta(milliseconds=timeout_ms)
        while "url" not in captured and datetime.now() < deadline:
            page.wait_for_timeout(500)
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)

    if "url" not in captured:
        raise RuntimeError(
            "N'a pas vu de requête vers l'API events après "
            f"{timeout_ms/1000:.0f}s. Es-tu bien connecté au réseau Decathlon ? "
            "Si une page de login est affichée, connecte-toi puis relance."
        )
    return captured


def capture_template(page, timeout_ms=180_000):
    """Load the home page and capture the (lightweight) dashboard events request."""
    return _wait_for_events_request(
        page, lambda: page.goto(BASE_URL, wait_until="load", timeout=timeout_ms), timeout_ms
    )


def capture_full_planning_template(page, timeout_ms=30_000):
    """Click through to the full weekly planning view and capture that events request
    (it asks for more keywords, including 'aff' which is what actually carries shifts)."""
    def trigger():
        page.get_by_text("Tout voir").first.click()

    return _wait_for_events_request(page, trigger, timeout_ms)


def shift_week(date_str, weeks):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d + timedelta(weeks=weeks)).strftime("%Y-%m-%d")


FETCH_JS = """
async ({url, body, headers}) => {
  const resp = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json;charset=UTF-8', ...headers},
    credentials: 'include',
    body: JSON.stringify(body),
  });
  const buf = await resp.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return {status: resp.status, ok: resp.ok, base64: btoa(binary)};
}
"""


def fetch_events(page, url, body, extra_headers):
    headers = dict(extra_headers)
    headers["x-hq-idtransaction"] = str(random.randint(100_000_000, 999_999_999))
    return page.evaluate(FETCH_JS, {"url": url, "body": body, "headers": headers})


def publish_to_git():
    """Commit and push docs/planning.ics if it changed. No-op if nothing changed."""
    def run(*args):
        return subprocess.run(
            ["git", *args], cwd=PROJECT_DIR, capture_output=True, text=True
        )

    status = run("status", "--porcelain", "docs/planning.ics")
    if not status.stdout.strip():
        print("Pas de changement dans le planning, rien à publier.")
        return

    run("add", "docs/planning.ics")
    commit = run("commit", "-m", f"Mise à jour planning {datetime.now().isoformat(timespec='seconds')}")
    if commit.returncode != 0:
        print("git commit a échoué:", commit.stdout, commit.stderr)
        return

    push = run("push", "origin", "HEAD")
    if push.returncode != 0:
        print("git push a échoué:", push.stdout, push.stderr)
    else:
        print("Planning publié sur GitHub.")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context_kwargs = {}
        if STATE_FILE.exists():
            context_kwargs["storage_state"] = str(STATE_FILE)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        print("Chargement de la page et attente de la première requête planning...")
        home_captured = capture_template(page)
        print(f"Requête dashboard capturée (pour le token de session): {home_captured['url']}")

        planning_captured = capture_full_planning_template(page)
        events_url, template_body = planning_captured["url"], planning_captured["body"]
        print(f"Requête planning complet capturée: {events_url}")
        print(f"Corps template: {json.dumps(template_body, ensure_ascii=False)}")
        print(f"Statut: {planning_captured.get('status')}")

        captured = planning_captured

        context.storage_state(path=str(STATE_FILE))
        print(f"Session sauvegardée dans {STATE_FILE}")

        base_start = template_body.get("start")
        base_end = template_body.get("end")
        if not base_start or not base_end:
            raise RuntimeError("Le corps de la requête capturée n'a pas de champs start/end.")

        orig_headers = captured.get("headers", {})
        hq_headers = {k: v for k, v in orig_headers.items() if k.lower().startswith("x-hq")}
        # idtransaction gets regenerated per-request in fetch_events
        hq_headers.pop("x-hq-idtransaction", None)

        all_events = {}
        for week_offset in range(-WEEKS_BACK, WEEKS_FORWARD + 1):
            body = dict(template_body)
            body["start"] = shift_week(base_start, week_offset)
            body["end"] = shift_week(base_end, week_offset)

            result = fetch_events(page, events_url, body, hq_headers)
            if not result["ok"]:
                print(f"  semaine {body['start']}: HTTP {result['status']}, ignorée")
                continue

            raw = base64.b64decode(result["base64"])
            try:
                events = parse_events_response(raw, include_rest_days=False)
            except Exception as e:
                print(f"  semaine {body['start']}: échec du décodage protobuf ({e}), ignorée")
                continue

            for ev in events:
                all_events[ev["id"]] = ev
            print(f"  semaine {body['start']}: {len(events)} créneaux")

        browser.close()

    events_list = sorted(all_events.values(), key=lambda e: e["start"])
    ics_text = build_ics(events_list)
    DOCS_DIR.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(ics_text, encoding="utf-8")
    print(f"\n{len(events_list)} événements écrits dans {OUTPUT_FILE}")

    publish_to_git()


if __name__ == "__main__":
    main()
