"""
Sicily property listing watcher.

Fetches a set of saved search pages (currently Immobiliare.it), figures out
which listings are new since the last run, scores them against your budget
and keywords, and emails you a prioritized summary.

State (which listings have already been seen) is stored in seen_listings.json,
which the GitHub Actions workflow commits back to the repo after each run so
the history persists between runs.
"""

import json
import os
import re
import smtplib
import subprocess
from datetime import datetime
from email.mime.text import MIMEText

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration — edit this section to change what gets watched and how
# listings get prioritized. No need to touch anything below it.
# ---------------------------------------------------------------------------

SEARCHES = [
    {
        "name": "Avola - sea view",
        "url": "https://www.immobiliare.it/en/vendita-case/avola/con-vista-mare/",
    },
    {
        "name": "Avola - cheap houses",
        "url": "https://www.immobiliare.it/en/vendita-case/avola/con-prezzo-basso/",
    },
    {
        "name": "Santa Maria del Focallo - sea view",
        "url": "https://www.immobiliare.it/en/vendita-case/ispica/santa-maria-del-focallo/con-vista-mare/",
    },
    # Add more saved-search URLs here following the same pattern.
    # Idealista.it has a different page structure, so it isn't wired up yet —
    # see the README for notes on adding it.
]

MAX_BUDGET = 70_000  # purchase-price ceiling used for priority scoring
GOOD_KEYWORDS = [
    "sea view", "vista mare", "beach", "spiaggia", "fronte mare",
    "metri dal mare", "passi dal mare", "passi dalla spiaggia",
]

STATE_FILE = "seen_listings.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ---------------------------------------------------------------------------
# State handling
# ---------------------------------------------------------------------------


def load_seen() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_seen(seen: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Fetching and parsing
# ---------------------------------------------------------------------------


def parse_price(raw: str) -> int | None:
    """Turn '480.000' or '70,000' into an int."""
    cleaned = re.sub(r"[^\d]", "", raw)
    return int(cleaned) if cleaned else None


def parse_immobiliare(html: str) -> list[dict]:
    """
    Parse an Immobiliare.it search-results page.

    Rather than relying on exact CSS class names (which Immobiliare changes
    periodically), this looks for links matching the canonical listing URL
    pattern '/annunci/<id>/' and pulls the nearest price text as context.
    That pattern is far more stable than any particular div/class name.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    seen_ids = set()

    for a_tag in soup.find_all("a", href=re.compile(r"/annunci/\d+/?")):
        href = a_tag.get("href", "")
        match = re.search(r"/annunci/(\d+)/?", href)
        if not match:
            continue
        listing_id = match.group(1)
        if listing_id in seen_ids:
            continue
        seen_ids.add(listing_id)

        title = (a_tag.get("title") or a_tag.get_text(strip=True) or "").strip()
        if not title:
            continue

        # Look in increasingly large containers for a nearby price.
        price = None
        node = a_tag
        for _ in range(4):
            node = node.parent
            if node is None:
                break
            price_match = re.search(r"€\s*([\d.,]+)", node.get_text(" ", strip=True))
            if price_match:
                price = parse_price(price_match.group(1))
                break

        full_url = href if href.startswith("http") else f"https://www.immobiliare.it{href}"

        listings.append(
            {
                "id": f"immobiliare_{listing_id}",
                "title": title,
                "price": price,
                "link": full_url,
            }
        )

    return listings


def fetch_url(url: str) -> tuple[int, str]:
    """
    Fetch a URL via curl rather than requests.

    Some servers (Immobiliare.it included) send an HTTP 103 Early Hints
    response before the real one, as a performance optimization — the
    genuine 200 response with the actual page follows right after. The
    requests/urllib3 library has a known issue where it stops at that 103
    and never reads the real response. curl has always handled this
    correctly, so we shell out to it instead of using requests.
    """
    result = subprocess.run(
        [
            "curl", "-s", "-L",
            "-A", HEADERS["User-Agent"],
            "-H", f"Accept-Language: {HEADERS['Accept-Language']}",
            "-w", "\n__HTTP_STATUS__:%{http_code}",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    output = result.stdout
    marker = "\n__HTTP_STATUS__:"
    idx = output.rfind(marker)
    if idx == -1:
        return 0, output
    html = output[:idx]
    status_code = int(output[idx + len(marker):].strip() or 0)
    return status_code, html


def fetch_search(search: dict) -> list[dict]:
    status_code, html = fetch_url(search["url"])

    # --- Diagnostics: figure out what we actually received ---
    lowered = html.lower()
    print(f"  -> HTTP {status_code}, {len(html)} characters received")
    if "captcha" in lowered or "are you a human" in lowered or "access denied" in lowered or "cloudflare" in lowered:
        print("  -> Looks like a bot-check / block page, not the real listing page.")
    if "/annunci/" not in lowered:
        print("  -> No '/annunci/' links appear anywhere in the page at all.")
    print(f"  -> First 300 characters of response:\n{html[:300]!r}")
    # --- End diagnostics ---

    if status_code >= 400 or status_code == 0:
        raise RuntimeError(f"Bad response (HTTP {status_code}) for {search['url']}")

    listings = parse_immobiliare(html)
    for listing in listings:
        listing["search"] = search["name"]
    return listings


# ---------------------------------------------------------------------------
# Prioritization
# ---------------------------------------------------------------------------


def score_listing(listing: dict) -> int:
    score = 0
    if listing["price"] is not None and listing["price"] <= MAX_BUDGET:
        score += 10
    title_lower = listing["title"].lower()
    for keyword in GOOD_KEYWORDS:
        if keyword in title_lower:
            score += 5
    return score


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def send_email(new_listings: list[dict]) -> None:
    if not new_listings:
        print("No new listings — skipping email.")
        return

    ranked = sorted(new_listings, key=score_listing, reverse=True)

    lines = [f"{len(ranked)} new listing(s) since the last check:\n"]
    for listing in ranked:
        price_text = f"€{listing['price']:,}" if listing["price"] else "price not shown"
        lines.append(f"[{listing['search']}] {price_text}")
        lines.append(listing["title"])
        lines.append(listing["link"])
        lines.append("")

    body = "\n".join(lines)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"Sicily property alert: {len(ranked)} new listing(s)"
    msg["From"] = os.environ["EMAIL_FROM"]
    msg["To"] = os.environ["EMAIL_TO"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["EMAIL_FROM"], os.environ["EMAIL_PASSWORD"])
        server.send_message(msg)

    print(f"Sent email with {len(ranked)} new listing(s).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    seen = load_seen()
    new_listings = []

    for search in SEARCHES:
        try:
            listings = fetch_search(search)
        except Exception as exc:  # noqa: BLE001 - we want to keep going on per-site failures
            print(f"Failed to fetch '{search['name']}': {exc}")
            continue

        print(f"{search['name']}: parsed {len(listings)} listing(s) from the page.")

        for listing in listings:
            if listing["id"] not in seen:
                new_listings.append(listing)
                seen[listing["id"]] = {
                    "title": listing["title"],
                    "price": listing["price"],
                    "first_seen": datetime.utcnow().isoformat(),
                }

    save_seen(seen)
    send_email(new_listings)
    print(f"Done. {len(new_listings)} new listing(s) found this run.")


if __name__ == "__main__":
    main()
