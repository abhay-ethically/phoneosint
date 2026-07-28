#!/usr/bin/env python3
"""
PhoneOsint - Master phone number OSINT aggregator
Scans publicly available metadata and generates dork URLs for accounts/services
linked to a phone number.
"""

import argparse
import csv
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import phonenumbers
from phonenumbers import carrier, geocoder, number_type
import phonenumbers.timezone
import requests
from colorama import Fore, Style, init
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.text import Text
from rich import box

try:
    import truecallerpy
    HAS_TRUECALLERPY = True
except ImportError:
    truecallerpy = None
    HAS_TRUECALLERPY = False

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
    from telethon.tl.types import InputPhoneContact
    from telethon.errors import SessionPasswordNeededError
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    openpyxl = None
    HAS_OPENPYXL = False

init(autoreset=True)
console = Console()
# Status/progress/error messages (batch progress, save confirmations, etc.)
# must never land on stdout, since stdout may be a piped/redirected --json
# or --output stream. Use this console for anything that isn't the actual
# report content.
status_console = Console(stderr=True)

TOOL_NAME = "PhoneOsint"
AUTHOR = "abhay-ethically"
GITHUB_URL = "https://github.com/abhay-ethically"

DEFAULT_COUNTRY = "IN"
CONFIG_DIR = Path.home() / ".phoneosint"
CONFIG_FILE = CONFIG_DIR / "config.json"
EXTERNAL_TOOLS_DIR = Path.home() / ".phoneosint-tools"
HISTORY_DIR = CONFIG_DIR / "history"
HISTORY_KEEP = 10

ALL_SECTIONS = [
    "basic_info", "dorks", "search_engines", "direct_links",
    "carrier_gateways", "dark_web", "breach_lookup", "local_breach_search",
    "email_lookup", "gravatar", "business_dorks", "aadhaar", "paypal",
    "extra_apis", "ignorant", "truecaller", "telegram", "external_tools",
    "paid_apis",
]

_LINE_TYPE_NAMES = {
    0: "FIXED_LINE",
    1: "MOBILE",
    2: "FIXED_LINE_OR_MOBILE",
    3: "TOLL_FREE",
    4: "PREMIUM_RATE",
    5: "SHARED_COST",
    6: "VOIP",
    7: "PERSONAL_NUMBER",
    8: "PAGER",
    9: "UAN",
    10: "UNKNOWN",
    27: "VOICEMAIL",
}


BANNER_ART = r"""
[bold cyan] ____  _                    ___     _       _
|  _ \| |__   ___  _ __   ___/ _ \___(_)_ __ | |_
| |_) | '_ \ / _ \| '_ \ / _ \ | | / __| | '_ \| __|
|  __/| | | | (_) | | | |  __/ |_| \__ \ | | | | |_
|_|   |_| |_|\___/|_| |_|\___|\___/|___/_|_| |_|\__|[/bold cyan]
"""


def print_banner():
    """Show a pretty startup banner with author/github credit. Always
    written to stderr so it never corrupts --json/--output stdout, no
    matter where it's called from (top-level or inside interactive())."""
    status_console.print(BANNER_ART)
    subtitle = Text()
    subtitle.append("Phone Number OSINT Aggregator", style="bold white")
    subtitle.append("  •  ", style="dim")
    subtitle.append("by ", style="dim")
    subtitle.append(f"{AUTHOR}", style="bold magenta")
    subtitle.append("  •  ", style="dim")
    subtitle.append(GITHUB_URL, style="underline blue")
    status_console.print(Panel(subtitle, box=box.ROUNDED, border_style="cyan", padding=(0, 2)))


def load_config():
    """Load saved API keys / Truecaller session from ~/.phoneosint/config.json."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(config: dict):
    """Persist API keys / Truecaller session to ~/.phoneosint/config.json."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        CONFIG_FILE.chmod(0o600)
        return True
    except Exception as exc:
        print(Fore.RED + f"[!] Could not save config: {exc}" + Style.RESET_ALL, file=sys.stderr)
        return False


def normalize(number: str, region: str = DEFAULT_COUNTRY):
    """Parse and validate a phone number, returning a phonenumbers object.

    Tolerates two common user-input mistakes instead of failing outright:
    1. Entering the numeric calling code (e.g. "91") when asked for the
       ISO region code (e.g. "IN") -- resolved via region_code_for_country_code.
    2. Typing a number that already includes the country calling code but
       without a leading '+' (e.g. "919021148834") -- retried as
       international ("+919021148834") if the region-based parse fails.
    """
    number = number.strip()
    region = (region or DEFAULT_COUNTRY).strip().upper()

    if region.isdigit():
        resolved = phonenumbers.region_code_for_country_code(int(region))
        if resolved and resolved != "ZZ":
            region = resolved

    if number.startswith("+"):
        candidates = [(number, None)]
    else:
        candidates = [(number, region)]
        calling_code = phonenumbers.country_code_for_region(region)
        if calling_code and number.startswith(str(calling_code)):
            candidates.append(("+" + number, None))

    for num, reg in candidates:
        try:
            parsed = phonenumbers.parse(num, reg)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            return parsed
    return None


def basic_info(parsed):
    """Extract country, carrier, line type, timezone, and formatted numbers."""
    return {
        "e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        "national": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL),
        "international": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
        "country_code": parsed.country_code,
        "national_number": parsed.national_number,
        "region": geocoder.description_for_number(parsed, "en"),
        "timezone": list(phonenumbers.timezone.time_zones_for_number(parsed)),
        "carrier": carrier.name_for_number(parsed, "en"),
        "line_type": _LINE_TYPE_NAMES.get(number_type(parsed), str(number_type(parsed))),
        "possible": phonenumbers.is_possible_number(parsed),
        "valid": phonenumbers.is_valid_number(parsed),
    }


def generate_dorks(number: str):
    """Generate Google dork URLs for finding accounts/services."""
    queries = {
        "General": number,
        "WhatsApp": f'{number} "WhatsApp"',
        "Telegram": f'{number} "Telegram"',
        "Facebook": f'{number} "Facebook"',
        "Instagram": f'{number} "Instagram"',
        "Twitter/X": f'{number} "Twitter"',
        "LinkedIn": f'{number} "LinkedIn"',
        "Truecaller": f'{number} "Truecaller"',
        "GitHub": f'{number} "GitHub"',
        "Pastebin/Leaks": f'{number} "pastebin"',
    }
    return {
        name: f"https://www.google.com/search?q={quote_plus(query)}"
        for name, query in queries.items()
    }


def direct_links(number: str):
    """Generate direct deep links to messaging platforms."""
    digits = number.lstrip("+")
    return {
        "WhatsApp": f"https://wa.me/{digits}",
        "Telegram": f"https://t.me/{digits}",
        "Viber": f"viber://chat?number={digits}",
    }


_US_CARRIER_GATEWAYS = {
    "AT&T": "txt.att.net",
    "Verizon": "vtext.com",
    "T-Mobile": "tmomail.net",
    "Sprint": "messaging.sprintpcs.com",
    "Boost Mobile": "sms.myboostmobile.com",
    "Cricket": "sms.cricketwireless.net",
    "US Cellular": "email.uscc.net",
    "MetroPCS": "mymetropcs.com",
    "Google Fi": "msg.fi.google.com",
    "Virgin Mobile": "vmobl.com",
}


def carrier_gateways(digits: str, region: str):
    """Generate SMS/email gateway addresses (mostly US-specific, best-effort)."""
    result = {}
    region = (region or "").strip().upper()
    is_us_like = region in ("US", "CA") or (len(digits) == 11 and digits.startswith("1"))
    if is_us_like:
        national = digits[-10:]
        for name, domain in _US_CARRIER_GATEWAYS.items():
            result[name] = f"{national}@{domain}"
    result["note"] = (
        "These are unofficial guesses since the real carrier isn't confirmed without a paid API. "
        "SMS/email gateways are only reliably known for US carriers; other countries generally "
        "do not expose public carrier email gateways."
    )
    return result


def search_engine_dorks(number: str):
    """Generate search URLs for multiple search engines."""
    engines = {
        "Google": "https://www.google.com/search?q=",
        "Bing": "https://www.bing.com/search?q=",
        "DuckDuckGo": "https://duckduckgo.com/?q=",
        "Yahoo": "https://search.yahoo.com/search?p=",
        "Yandex": "https://yandex.com/search/?text=",
        "Brave": "https://search.brave.com/search?q=",
    }
    queries = {
        "General": number,
        "Social Accounts": f'{number} ("WhatsApp" OR "Telegram" OR "Facebook" OR "Instagram" OR "Twitter" OR "LinkedIn")',
        "Leaks/Pastes": f'{number} ("pastebin" OR "breach" OR "leak")',
        "Truecaller": f'{number} "Truecaller"',
    }
    return {
        engine: {name: f"{base}{quote_plus(query)}" for name, query in queries.items()}
        for engine, base in engines.items()
    }


def shodan_check(number: str, api_key: str | None):
    """Optional Shodan Internet DB banner search (requires API key)."""
    if not api_key:
        return {"note": "No Shodan API key provided; skipping remote lookup"}
    try:
        resp = _request_with_retry(
            "get", "https://api.shodan.io/shodan/host/search",
            params={"key": api_key, "query": number, "limit": 10},
            timeout=20,
        )
        data = resp.json()
        return {
            "total": data.get("total", 0),
            "matches": data.get("matches", [])[:5],
            "warning": "Results are Shodan banner content; not phone-specific records.",
        }
    except Exception as exc:
        return {"error": str(exc)}


def _tor_is_running(host="127.0.0.1", port=9050, timeout=1.5):
    """Check if a local Tor SOCKS proxy is reachable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def dark_web_search(number: str, use_tor: bool = False):
    """Generate dark web search engine URLs (Tor/onion indexes). Optionally query live via Tor."""
    q = quote_plus(number)
    result = {
        "Ahmia_Tor_Index": f"https://ahmia.fi/search/?q={q}",
        "DarkSearch": f"https://darksearch.io/?q={q}",
        "Haystak_Onion": f"https://haystak5njsmn2hqkewecpaxdfeht334xm522ktgfjql7eoabjdzjlyd.onion/search?q={q}",
        "Torch_Onion": f"http://xmh57jrknzkhv6y3ls3ubitzfqnpswu5jgmdo2nxn4334iyn5i3kpyqd.onion/search?query={q}",
    }

    if not use_tor:
        result["note"] = "Dark web search links only. Pass --tor to route live queries through a local Tor proxy."
        return result

    if not _tor_is_running():
        result["note"] = (
            "Tor not detected on 127.0.0.1:9050. Showing links only. "
            "Start Tor (e.g. `brew install tor && tor`) to enable live onion search."
        )
        return result

    proxies = {
        "http": "socks5h://127.0.0.1:9050",
        "https": "socks5h://127.0.0.1:9050",
    }
    try:
        resp = requests.get(
            f"https://ahmia.fi/search/?q={q}",
            proxies=proxies,
            timeout=30,
        )
        result["ahmia_live_status"] = resp.status_code
        result["ahmia_live_snippet"] = resp.text[:2000]
        result["note"] = "Live Ahmia search routed through local Tor proxy."
    except Exception as exc:
        result["tor_error"] = str(exc)
        result["note"] = "Tor proxy detected but the live request failed; showing links only."
    return result


def _request_with_retry(method: str, url: str, retries: int = 2, backoff: float = 1.5, **kwargs):
    """requests.get/post wrapper with exponential backoff on 429/5xx/connection
    errors. Returns the final Response, or raises the last exception after
    exhausting retries (callers should still wrap calls in try/except)."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < retries:
                    time.sleep(backoff * (attempt + 1))
                    continue
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise
    if last_exc:
        raise last_exc


def numverify_check(number: str, api_key: str | None):
    """Optional Numverify lookup (requires API key)."""
    if not api_key:
        return {"note": "No Numverify API key provided; skipping remote lookup"}
    try:
        resp = _request_with_retry(
            "get", "http://apilayer.net/api/validate",
            params={"access_key": api_key, "number": number},
            timeout=15,
        )
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


def external_tool_commands(number: str):
    """Reference commands for popular OSINT tools (install separately)."""
    digits = number.lstrip("+")
    return {
        "Maigret": {
            "description": "Username/email/phone account discovery across social networks",
            "command": f"maigret {digits} --phone",
            "note": "Requires maigret installed; primarily username-based.",
        },
        "Sherlock": {
            "description": "Username-based social account discovery",
            "command": f"sherlock {digits}",
            "note": "Use a username derived from the number, not the number itself.",
        },
        "Holehe": {
            "description": "Check if email/phone is registered on services",
            "command": f"holehe {digits}",
            "note": "Requires email format; phone support is limited.",
        },
        "Twint": {
            "description": "Twitter intelligence (search mentions of number)",
            "command": f"twint -s '{number}' -o twint_{digits}.csv --csv",
            "note": "Deprecated/fragile; searches tweets containing the number.",
        },
        "GHunt": {
            "description": "Google account OSINT",
            "command": f"ghunt email {digits}@gmail.com",
            "note": "Requires an email, not a phone number.",
        },
        "Mr.Holmes": {
            "description": "All-in-one OSINT with phone modules",
            "command": "python3 MrHolmes.py",
            "note": "Mr.Holmes is a fully interactive menu-driven tool (no CLI flags) -- run it, then choose 'Phone Number OSINT' from the menu and paste the number in when prompted. Cannot be auto-scripted safely.",
        },
        "PhoneInfoga": {
            "description": "Advanced phone number OSINT scanner",
            "command": f"phoneinfoga scan -n {number}",
            "note": "Install phoneinfoga separately.",
        },
        "Infoga": {
            "description": "Email/phone information gathering",
            "command": f"python3 infoga.py --info {number}",
            "note": "Install Infoga and adjust script path.",
        },
        "SpiderFoot": {
            "description": "Full OSINT automation with phone number modules",
            "command": f"sf.py -m sfp_phonenumber -s {number} -q",
            "note": "Requires SpiderFoot server and API key.",
        },
        "theHarvester": {
            "description": "Email/domain enumeration",
            "command": "theHarvester -d example.com -b all",
            "note": "Not phone-specific; use with a domain linked to the target.",
        },
    }


def _run_subprocess(cmd, cwd=None, timeout=60):
    """Run a subprocess safely, capturing FULL output (no truncation) and
    never raising."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return {
            "command": " ".join(cmd),
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"command": " ".join(cmd), "error": f"Timed out after {timeout}s"}
    except FileNotFoundError as exc:
        return {"command": " ".join(cmd), "error": f"Executable not found: {exc}"}
    except Exception as exc:
        return {"command": " ".join(cmd), "error": str(exc)}


def run_external_tools(number: str, digits: str):
    """Best-effort auto-run of installed external OSINT tools; merges output into report."""
    results = {}

    phoneinfoga_bin = shutil.which("phoneinfoga")
    if phoneinfoga_bin:
        results["PhoneInfoga"] = _run_subprocess(
            [phoneinfoga_bin, "scan", "-n", number], timeout=90
        )
    else:
        results["PhoneInfoga"] = {
            "note": "phoneinfoga binary not found on PATH. Run install.sh to download the official prebuilt "
                    "binary (no Go toolchain required), or install manually: "
                    "bash <(curl -sSL https://raw.githubusercontent.com/sundowndev/phoneinfoga/master/support/scripts/install)"
        }

    maigret_bin = shutil.which("maigret")
    if maigret_bin:
        results["Maigret"] = _run_subprocess(
            [maigret_bin, digits, "--phone", "--no-color"], timeout=90
        )
    else:
        results["Maigret"] = {"note": "maigret not installed. Run: pip install maigret"}

    derived_usernames = sorted(set([digits, digits[-10:] if len(digits) > 10 else digits]))

    sherlock_bin = shutil.which("sherlock")
    if sherlock_bin:
        results["Sherlock"] = {
            "basis": "derived username guess, not a confirmed identity",
            "runs": [
                _run_subprocess([sherlock_bin, uname, "--print-found", "--timeout", "10"], timeout=90)
                for uname in derived_usernames
            ],
        }
    else:
        results["Sherlock"] = {"note": "sherlock not installed. See install.sh."}

    holehe_bin = shutil.which("holehe")
    if holehe_bin:
        results["Holehe"] = {
            "note": "Holehe expects an email address; phone-derived usernames are unlikely to match.",
            "skipped": True,
        }
    else:
        results["Holehe"] = {"note": "holehe not installed. Run: pip install holehe"}

    results["theHarvester"] = {
        "note": "Skipped auto-run: theHarvester needs a domain, not a phone number.",
    }

    return results


def paid_enrichment(number: str, ipqs_key: str | None, opencnam_key: str | None):
    """Optional paid API enrichment for location and caller-ID name."""
    result = {"note": "No paid API keys provided"}
    if ipqs_key:
        try:
            resp = _request_with_retry(
                "get", "https://ipqualityscore.com/api/json/phone",
                params={"phone": number, "strictness": 1, "country": [], "key": ipqs_key},
                timeout=15,
            )
            result["ipqs"] = resp.json()
        except Exception as exc:
            result["ipqs_error"] = str(exc)
    if opencnam_key:
        try:
            resp = _request_with_retry(
                "get", f"https://api.opencnam.com/v3/phone/{number}",
                params={"format": "json", "account_sid": opencnam_key, "auth_token": ""},
                timeout=15,
            )
            result["opencnam"] = resp.json()
        except Exception as exc:
            result["opencnam_error"] = str(exc)
    return result


def breach_lookup(number: str):
    """Search public paste/breach dumps for the phone number."""
    result = {}
    q = quote_plus(number)
    digits = number.lstrip("+")
    try:
        resp = _request_with_retry(
            "get", f"https://psbdmp.ws/api/v3/search/{digits}",
            timeout=20,
        )
        result["psbdmp"] = resp.json()
    except Exception as exc:
        result["psbdmp_error"] = str(exc)

    result["dehashed_search"] = f"https://dehashed.com/search?query={q}"
    result["google_pastebin_dork"] = (
        f"https://www.google.com/search?q={q}+site:pastebin.com"
    )
    result["note"] = (
        "Public breach APIs for phone numbers are rare. "
        "Reliable breach checks require paid/authorized access (DeHashed, Leak-Lookup, etc.)."
    )
    return result


def _cell_digits(value) -> str:
    """Extract a plain digit string from a spreadsheet/CSV cell, handling
    the common case where openpyxl reads a phone number as a float
    (e.g. 7410410123.0) so it doesn't leak a bogus trailing '0'."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\D", "", str(value))


def _load_rows_from_file(path: Path):
    """Load rows from a CSV or XLSX/XLS file as a list of dicts keyed by
    the header row. Raises on unreadable/unsupported files -- callers
    should catch and report per-file errors."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    if suffix in (".xlsx", ".xlsm", ".xls"):
        if not HAS_OPENPYXL:
            raise RuntimeError("openpyxl not installed. Run: pip install openpyxl")
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        rows = []
        for sheet in wb.worksheets:
            it = sheet.iter_rows(values_only=True)
            try:
                header = [str(h) if h is not None else f"col{i}" for i, h in enumerate(next(it))]
            except StopIteration:
                continue
            for row in it:
                rows.append(dict(zip(header, row)))
        return rows
    raise ValueError(f"Unsupported file type: {suffix or '(none)'} (use .csv or .xlsx)")


def local_file_search(number: str, national_number: int, file_paths: list):
    """Search phone number/name/address matches against local file(s) you
    explicitly provide via --breach-file (or the interactive prompt). This
    is a generic lookup mechanism only -- PhoneOsint does not ship, bundle,
    or hardcode any dataset. You are solely responsible for the legality
    and authorization of any file you point this at."""
    if not file_paths:
        return {
            "note": (
                "No local breach/database file configured. Pass --breach-file <path.csv|.xlsx> "
                "(repeatable) to search your own authorized dataset(s) for this number. "
                "PhoneOsint does not bundle or hardcode any such data."
            )
        }

    target_digits = {re.sub(r"\D", "", number), str(national_number)}
    files_searched = []
    matches = []
    MAX_MATCHES_PER_FILE = 25

    for raw_path in file_paths:
        path = Path(raw_path).expanduser()
        entry = {"file": str(path)}
        if not path.exists():
            entry["error"] = "File not found."
            files_searched.append(entry)
            continue
        try:
            rows = _load_rows_from_file(path)
        except Exception as exc:
            entry["error"] = str(exc)
            files_searched.append(entry)
            continue

        found_in_file = 0
        for row in rows:
            row_matches = any(
                _cell_digits(value) and _cell_digits(value) in target_digits
                for value in row.values()
            )
            if row_matches:
                found_in_file += 1
                if len(matches) < MAX_MATCHES_PER_FILE * len(file_paths):
                    matches.append({"source_file": str(path), "row": row})

        entry["rows_scanned"] = len(rows)
        entry["matches_found"] = found_in_file
        files_searched.append(entry)

    return {
        "files_searched": files_searched,
        "total_matches": len(matches),
        "matches": matches,
        "note": (
            "Results are only as good as the file(s) you provided via --breach-file; "
            "PhoneOsint performs no verification of this data's accuracy, legality, or provenance. "
            "You must have proper authorization to possess and query any dataset used here."
        ),
    }


def email_lookup_from_phone(number: str):
    """Generate dorks and links that may reveal an email linked to the phone number."""
    q = quote_plus(number)
    at_q = quote_plus("@")
    return {
        "google_general": f"https://www.google.com/search?q={q}",
        "google_email_keyword": f"https://www.google.com/search?q={q}+email",
        "google_mailto": f"https://www.google.com/search?q={q}+mailto",
        "google_with_at": f"https://www.google.com/search?q=%22{q}%22+%22{at_q}%22",
        "google_pastebin_email": f"https://www.google.com/search?q={q}+email+site:pastebin.com",
        "dehashed_phone_search": f"https://dehashed.com/search?query={q}",
        "breach_directory": "https://breachdirectory.org",
        "note": (
            "No reliable free API maps a phone number to an email. "
            "Use paid/authorized breach services (DeHashed, Snusbase, Intelligence X) "
            "or Google dorks that pair the number with 'email', 'mailto', or '@'."
        ),
    }


def gravatar_lookup(email: str | None):
    """Check Gravatar for a profile linked to an email (free, no key). Only
    useful if an email was already discovered elsewhere (e.g. breach_lookup
    or email_lookup dorks) -- there is no direct phone-to-email API."""
    if not email:
        return {"note": "No email address available to check against Gravatar. "
                         "Gravatar cannot be queried by phone number directly."}
    email_hash = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
    profile_url = f"https://www.gravatar.com/{email_hash}.json"
    avatar_url = f"https://www.gravatar.com/avatar/{email_hash}?d=404"
    result = {
        "email_checked": email,
        "md5_hash": email_hash,
        "avatar_url": f"https://www.gravatar.com/avatar/{email_hash}",
    }
    try:
        resp = _request_with_retry("get", avatar_url, timeout=10)
        result["has_avatar"] = resp.status_code == 200
    except Exception as exc:
        result["avatar_check_error"] = str(exc)
    try:
        resp = _request_with_retry("get", profile_url, timeout=10)
        if resp.status_code == 200:
            result["profile"] = resp.json()
        else:
            result["profile_note"] = "No public Gravatar profile found for this email."
    except Exception as exc:
        result["profile_error"] = str(exc)
    return result


def business_directory_dorks(number: str):
    """Direct search links on business/local directories that commonly index
    phone numbers for registered businesses (free, no key, links only)."""
    q = quote_plus(number)
    return {
        "Google_Maps": f"https://www.google.com/maps/search/{q}",
        "Google_My_Business_dork": f"https://www.google.com/search?q={q}+site:business.google.com",
        "JustDial": f"https://www.justdial.com/{q}",
        "Sulekha_dork": f"https://www.google.com/search?q={q}+site:sulekha.com",
        "IndiaMart_dork": f"https://www.google.com/search?q={q}+site:indiamart.com",
        "Yelp_dork": f"https://www.google.com/search?q={q}+site:yelp.com",
        "note": "These are search/directory links only; a match confirms a publicly listed business number, not personal ownership.",
    }


def aadhaar_linkage_note(number: str):
    """Informational only -- there is NO free, public, or legal API that maps
    a phone number to an Aadhaar number/name. Aadhaar data is protected under
    India's Aadhaar Act 2016 and UIDAI regulations; only authorized entities
    (banks, telecom operators, and government agencies with UIDAI-approved
    AUA/KUA access) can perform consent-based e-KYC verification. This
    function performs NO network request and returns NO real data."""
    return {
        "lookup_possible": False,
        "reason": "No free/public API maps a phone number to Aadhaar data. This is restricted by the Aadhaar Act, 2016 and UIDAI regulations.",
        "authorized_channel": (
            "Only UIDAI-licensed AUA/KUA entities (banks, telecom operators, certain govt "
            "agencies) can perform Aadhaar e-KYC, and only with the number holder's explicit "
            "consent (OTP-based). Law enforcement must go through official UIDAI/legal channels "
            "(e.g. court order or MHA-approved request), not a public API."
        ),
        "warning": "Any third-party tool/site claiming to reveal Aadhaar details from a phone number is either a scam or operating illegally. Do not use such services.",
    }


def paypal_name_leak_note(number: str):
    """Informational only -- PayPal's 'send money' flow can sometimes reveal a
    partially-masked account holder name when you enter a phone/email tied to
    an account. This is NOT automated here: it requires a real logged-in
    PayPal session and doing it programmatically/repeatedly violates PayPal's
    Terms of Service (risk of account suspension). This function performs NO
    network request and NO login."""
    return {
        "lookup_possible": False,
        "technique": (
            "Manually entering a phone number or email as a 'Send Money' recipient in the "
            "PayPal app/website sometimes shows a partially-masked name (e.g. 'J*** D**') if "
            "that contact has a PayPal account linked to it."
        ),
        "why_not_automated": (
            "Automating this requires a real authenticated PayPal session and repeated lookups, "
            "which violates PayPal's Terms of Service and risks account suspension/legal action. "
            "PhoneOsint does not perform any login or automated requests against PayPal."
        ),
        "manual_use_only": "If you choose to check this yourself, do it manually, sparingly, and only with proper authorization.",
    }


def extra_phone_apis(number: str, numlookupapi_key: str | None, abstractapi_key: str | None, veriphone_key: str | None):
    """Optional free-tier keyed phone validation APIs (numlookupapi.com,
    Abstract API, Veriphone.io). Each offers a small free monthly quota and
    gives live carrier/line-type data that can be more current than the
    offline phonenumbers database (useful after number porting)."""
    result = {}
    if not any([numlookupapi_key, abstractapi_key, veriphone_key]):
        result["note"] = (
            "No numlookupapi.com / Abstract API / Veriphone.io key provided; skipping. "
            "All three offer a small free-tier monthly quota if you want live carrier data."
        )
        return result

    if numlookupapi_key:
        try:
            resp = _request_with_retry(
                "get", f"https://api.numlookupapi.com/v1/validate/{quote_plus(number)}",
                params={"apikey": numlookupapi_key},
                timeout=15,
            )
            result["numlookupapi"] = resp.json()
        except Exception as exc:
            result["numlookupapi_error"] = str(exc)

    if abstractapi_key:
        try:
            resp = _request_with_retry(
                "get", "https://phonevalidation.abstractapi.com/v1/",
                params={"api_key": abstractapi_key, "phone": number},
                timeout=15,
            )
            result["abstractapi"] = resp.json()
        except Exception as exc:
            result["abstractapi_error"] = str(exc)

    if veriphone_key:
        try:
            resp = _request_with_retry(
                "get", "https://api.veriphone.io/v2/verify",
                params={"key": veriphone_key, "phone": number},
                timeout=15,
            )
            result["veriphone"] = resp.json()
        except Exception as exc:
            result["veriphone_error"] = str(exc)

    return result


def exposure_score(report: dict):
    """Aggregate signals from other sections into a 0-100 'exposure score'
    with a short explanation. Purely derived from already-fetched data --
    no new network calls."""
    score = 0
    reasons = []

    telegram = report.get("telegram")
    if isinstance(telegram, dict) and telegram.get("registered"):
        score += 20
        reasons.append("Telegram: registered account found (+20)")

    truecaller = report.get("truecaller")
    if isinstance(truecaller, dict) and (truecaller.get("name") or truecaller.get("data")):
        score += 20
        reasons.append("Truecaller: name resolved (+20)")

    breach = report.get("breach_lookup")
    if isinstance(breach, dict):
        psbdmp = breach.get("psbdmp")
        if isinstance(psbdmp, dict) and psbdmp.get("count"):
            score += 25
            reasons.append("Breach/paste dumps: hits found (+25)")
        elif isinstance(psbdmp, list) and psbdmp:
            score += 25
            reasons.append("Breach/paste dumps: hits found (+25)")

    extra = report.get("extra_apis")
    if isinstance(extra, dict) and any(
        isinstance(v, dict) and v.get("valid") for k, v in extra.items() if k != "note"
    ):
        score += 10
        reasons.append("Extra phone API confirms an active line (+10)")

    ignorant = report.get("ignorant")
    if isinstance(ignorant, dict) and ignorant.get("registered_on"):
        score += 15
        sites = ", ".join(ignorant["registered_on"])
        reasons.append(f"Account found on: {sites} (+15)")

    dark_web = report.get("dark_web")
    if isinstance(dark_web, dict) and dark_web.get("ahmia_live_status") == 200:
        score += 10
        reasons.append("Dark web: live Tor search returned a result (+10)")

    score = min(score, 100)
    if not reasons:
        reasons.append("No strong exposure signals found in the enabled sections.")

    return {
        "score": score,
        "level": "High" if score >= 60 else "Medium" if score >= 30 else "Low",
        "reasons": reasons,
        "note": "Derived heuristically from the other sections in this report; not an official risk rating.",
    }


def _prompt_stderr(prompt: str) -> str:
    """Like input(), but writes the prompt to stderr so it never pollutes
    stdout when --json output is being piped/redirected. If stdin is
    unavailable (e.g. redirected from /dev/null in a headless/automated
    run), returns an empty string instead of letting EOFError propagate."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        return input()
    except EOFError:
        return ""


def truecaller_login_flow(login_number: str, config: dict):
    """One-time interactive Truecaller login/OTP flow. Saves session to config."""
    import asyncio
    from truecallerpy import login, verify_otp

    print(Fore.CYAN + f"\n[Truecaller] Logging in as {login_number} ..." + Style.RESET_ALL, file=sys.stderr)
    try:
        login_resp = asyncio.run(login(login_number))
    except Exception as exc:
        return None, {"error": f"Login request failed: {exc}"}

    if login_resp.get("status_code") != 200:
        return None, {"error": f"Truecaller login failed: {login_resp.get('message')}"}

    login_data = login_resp.get("data", login_resp)
    otp = _prompt_stderr("Enter the OTP you received: ").strip()
    try:
        verify_resp = asyncio.run(verify_otp(login_number, login_data, otp))
    except Exception as exc:
        return None, {"error": f"OTP verification failed: {exc}"}

    v_data = verify_resp.get("data", verify_resp)
    if v_data.get("status") != 2 or v_data.get("suspended"):
        return None, {"error": f"OTP verification failed: {verify_resp.get('message', v_data)}"}

    installation_id = v_data.get("installationId")
    phones = v_data.get("phones", [])
    country_code = phones[0].get("countryCode") if phones else DEFAULT_COUNTRY
    config["truecaller_installation_id"] = installation_id
    config["truecaller_country_code"] = country_code
    save_config(config)
    print(Fore.GREEN + "[+] Truecaller login successful; session saved." + Style.RESET_ALL, file=sys.stderr)
    return installation_id, None


def truecaller_lookup(number: str, region: str, config: dict, allow_prompt: bool = True):
    """Look up a name via Truecaller (free, unofficial). Requires one-time login."""
    if not HAS_TRUECALLERPY:
        return {"note": "truecallerpy not installed. Run: pip install truecallerpy"}

    import asyncio
    from truecallerpy import search_phonenumber

    installation_id = config.get("truecaller_installation_id")
    country_code = config.get("truecaller_country_code", region)

    if not installation_id:
        if not allow_prompt:
            return {"note": "Truecaller lookup skipped: no saved session (batch mode doesn't prompt for login)."}
        print(Fore.YELLOW + "\n[Truecaller] No saved session found." + Style.RESET_ALL, file=sys.stderr)
        answer = _prompt_stderr("Log in to Truecaller now for name lookup? [y/N] ").strip().lower()
        if answer != "y":
            return {"note": "Truecaller lookup skipped (no session)."}
        login_number = _prompt_stderr("Your phone number for Truecaller login (e.g. +1234567890): ").strip()
        installation_id, err = truecaller_login_flow(login_number, config)
        if err:
            return err

    try:
        resp = asyncio.run(search_phonenumber(number, country_code, installation_id))
        return resp
    except Exception as exc:
        return {"error": str(exc)}


def telegram_login_flow(login_number: str, config: dict):
    """One-time interactive Telegram login/OTP flow. Saves session string to config.
    Requires a free api_id/api_hash from https://my.telegram.org."""
    import asyncio

    api_id = config.get("telegram_api_id")
    api_hash = config.get("telegram_api_hash")
    if not api_id or not api_hash:
        print(Fore.YELLOW + "\n[Telegram] Get a free api_id/api_hash from https://my.telegram.org (API Development Tools)." + Style.RESET_ALL, file=sys.stderr)
        api_id = _prompt_stderr("  Telegram api_id: ").strip()
        api_hash = _prompt_stderr("  Telegram api_hash: ").strip()
        if not api_id or not api_hash:
            return None, {"error": "api_id/api_hash required for Telegram login."}

    async def _login():
        client = TelegramClient(StringSession(), int(api_id), api_hash)
        await client.connect()
        try:
            await client.send_code_request(login_number)
            code = _prompt_stderr("Enter the Telegram code you received: ").strip()
            try:
                await client.sign_in(login_number, code)
            except SessionPasswordNeededError:
                password = _prompt_stderr("Two-factor password: ").strip()
                await client.sign_in(password=password)
            return client.session.save()
        finally:
            await client.disconnect()

    try:
        session_str = asyncio.run(_login())
    except Exception as exc:
        return None, {"error": f"Telegram login failed: {exc}"}

    config["telegram_api_id"] = api_id
    config["telegram_api_hash"] = api_hash
    config["telegram_session"] = session_str
    save_config(config)
    print(Fore.GREEN + "[+] Telegram login successful; session saved." + Style.RESET_ALL, file=sys.stderr)
    return session_str, None


def telegram_lookup(number: str, config: dict, allow_prompt: bool = True):
    """Check if a number is registered on Telegram (free, requires one-time login
    with your own account). Returns name/username if the contact's privacy
    settings allow it."""
    if not HAS_TELETHON:
        return {"note": "telethon not installed. Run: pip install telethon"}

    import asyncio

    session_str = config.get("telegram_session")
    api_id = config.get("telegram_api_id")
    api_hash = config.get("telegram_api_hash")

    if not session_str:
        if not allow_prompt:
            return {"note": "Telegram lookup skipped: no saved session (batch mode doesn't prompt for login)."}
        print(Fore.YELLOW + "\n[Telegram] No saved session found." + Style.RESET_ALL, file=sys.stderr)
        answer = _prompt_stderr("Log in to Telegram now for this lookup? [y/N] ").strip().lower()
        if answer != "y":
            return {"note": "Telegram lookup skipped (no session)."}
        login_number = _prompt_stderr("Your phone number for Telegram login (e.g. +1234567890): ").strip()
        session_str, err = telegram_login_flow(login_number, config)
        if err:
            return err
        api_id = config.get("telegram_api_id")
        api_hash = config.get("telegram_api_hash")

    async def _lookup():
        client = TelegramClient(StringSession(session_str), int(api_id), api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return {"note": "Telegram session expired or unauthorized. Re-run with a fresh login."}
            contact = InputPhoneContact(client_id=0, phone=number, first_name="", last_name="")
            result = await client(ImportContactsRequest([contact]))
            if not result.users:
                return {"registered": False, "note": "No Telegram account found for this number (or their privacy settings hide it)."}
            user = result.users[0]
            data = {
                "registered": True,
                "user_id": user.id,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "username": user.username,
                "is_premium": getattr(user, "premium", None),
                "is_verified": getattr(user, "verified", None),
            }
            try:
                await client(DeleteContactsRequest([user.id]))
            except Exception:
                pass
            return data
        finally:
            await client.disconnect()

    try:
        return asyncio.run(_lookup())
    except Exception as exc:
        return {"error": str(exc)}


def _summarize_ignorant_results(results: list) -> dict:
    """Pure helper (unit-testable without network) that turns ignorant's raw
    per-site result list into a summarized dict."""
    registered = [r["domain"] for r in results if r.get("exists")]
    rate_limited = [r["domain"] for r in results if r.get("rateLimit")]
    return {
        "checked_sites": len(results),
        "registered_on": registered,
        "rate_limited_sites": rate_limited,
        "results": results,
    }


def ignorant_lookup(country_code: int, national_number: int):
    """Check if a phone number is registered on Instagram, Snapchat, and
    Amazon using the free 'ignorant' library (free, no key, no login --
    calls its internal async modules directly instead of shelling out to
    its interactive CLI)."""
    try:
        from ignorant.core import import_submodules, get_functions, launch_module
        import httpx
        import trio
    except ImportError:
        return {"note": "ignorant not installed. Run: pip install ignorant"}

    async def _run():
        modules = import_submodules("ignorant.modules")
        websites = get_functions(modules)
        out = []
        async with httpx.AsyncClient(timeout=10) as client:
            async with trio.open_nursery() as nursery:
                for website in websites:
                    nursery.start_soon(
                        launch_module, website, str(national_number), f"+{country_code}", client, out
                    )
        return out

    try:
        results = trio.run(_run)
    except Exception as exc:
        return {"error": str(exc)}

    summary = _summarize_ignorant_results(results)
    summary["note"] = "Checks Instagram, Snapchat, and Amazon account existence via the free 'ignorant' library (no login required)."
    return summary


def twilio_lookup(number: str, account_sid: str, auth_token: str):
    """HLR-style live line lookup via Twilio's Lookup v2 API. Basic validation is
    free, but line_type_intelligence/sim_swap/line_status/caller_name are paid
    add-ons requiring a Twilio account (no free public HLR API exists)."""
    if not account_sid or not auth_token:
        return {"note": "No Twilio Account SID/Auth Token provided; skipping HLR/line-type lookup (this is a paid Twilio feature, no free alternative exists)."}

    url = f"https://lookups.twilio.com/v2/PhoneNumbers/{quote_plus(number)}"
    params = {"Fields": "line_type_intelligence,caller_name,sim_swap,line_status,call_forwarding"}
    try:
        resp = _request_with_retry("get", url, params=params, auth=(account_sid, auth_token), timeout=10)
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


def upi_lookup(number: str, client_id: str, client_secret: str):
    """Resolve the bank account holder's name registered against a phone number
    on UPI, via Cashfree's Validate Payout API. Requires a KYC-approved Cashfree
    Payouts merchant account (Client ID/Secret from the Merchant Dashboard) --
    there is no free, unauthenticated public API that maps a phone number to a
    UPI/bank account name; this is intentionally restricted by NPCI/banks."""
    if not client_id or not client_secret:
        return {"note": "No Cashfree Client ID/Secret provided; skipping UPI name lookup (requires your own KYC-approved Cashfree Payouts merchant account -- no free public API exists for this)."}

    url = "https://api.cashfree.com/payout/validatePayout"
    headers = {
        "Content-Type": "application/json",
        "x-api-version": "2024-01-01",
        "x-client-id": client_id,
        "x-client-secret": client_secret,
    }
    payload = {
        "transfer_id": f"phoneosint_{int(time.time())}",
        "phone": number.lstrip("+"),
    }
    try:
        resp = _request_with_retry("post", url, json=payload, headers=headers, timeout=10)
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


SECTION_MENU = [
    ("basic_info", "Basic Information (always included)"),
    ("dorks", "Google Dorks"),
    ("search_engines", "Multi-Search-Engine Dorks"),
    ("direct_links", "Direct Messaging Links"),
    ("carrier_gateways", "Carrier SMS/Email Gateways"),
    ("dark_web", "Dark Web Search (+Tor if available)"),
    ("breach_lookup", "Breach/Paste Lookup"),
    ("local_breach_search", "Search from Breach / Local File (needs --breach-file, your own authorized data)"),
    ("email_lookup", "Email Lookup Dorks"),
    ("gravatar", "Gravatar Lookup (free, needs an email from other sections)"),
    ("business_dorks", "Business Directory Dorks (JustDial/Maps/Yelp/etc, free)"),
    ("aadhaar", "Aadhaar Linkage (informational only, no real lookup)"),
    ("paypal", "PayPal Name-Leak Technique (informational only, no real lookup)"),
    ("extra_apis", "Extra Free-Tier Phone APIs (numlookupapi/AbstractAPI/Veriphone, needs free keys)"),
    ("ignorant", "Account Existence Check: Instagram/Snapchat/Amazon (free, no login, via 'ignorant')"),
    ("truecaller", "Truecaller Name Lookup (free, needs login)"),
    ("telegram", "Telegram Registration/Name Check (free, needs login)"),
    ("external_tools", "Auto-run Installed External Tools"),
    ("paid_apis", "Paid API Enrichment (Numverify/Shodan/IPQS/OpenCNAM/Twilio HLR/Cashfree UPI, needs keys)"),
]


def select_sections():
    """Show a pretty checklist menu and return the set of selected section keys."""
    menu_table = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True)
    menu_table.add_column("#", style="bold yellow", justify="right", no_wrap=True)
    menu_table.add_column("Section", style="bold white")
    for i, (_, label) in enumerate(SECTION_MENU, start=1):
        menu_table.add_row(str(i), label)
    menu_table.add_row("a", "[bold green]All (default)[/bold green]")
    status_console.print(Panel(menu_table, title="[bold cyan]Select Sections to Run[/bold cyan]", border_style="cyan"))

    choice = Prompt.ask(
        "[bold]Enter numbers separated by commas, or 'a' for all[/bold]", default="a", console=status_console
    ).strip().lower()
    if not choice or choice == "a":
        return set(ALL_SECTIONS)
    selected = {"basic_info"}
    for part in choice.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(SECTION_MENU):
                selected.add(SECTION_MENU[idx][0])
    return selected


def interactive(show_banner: bool = True):
    """Collect inputs from the user interactively using a pretty rich-driven flow."""
    config = load_config()
    if show_banner:
        print_banner()
    status_console.print(Panel("[bold]Interactive Mode[/bold] — let's scope your scan", border_style="green"))

    number = Prompt.ask("[bold]Phone number to investigate[/bold]", console=status_console).strip()
    country = Prompt.ask(
        "[bold]Default region code[/bold] (ISO code, e.g. IN, US, GB -- not the numeric calling code)",
        default=DEFAULT_COUNTRY, console=status_console,
    ).strip() or DEFAULT_COUNTRY

    sections = select_sections()

    numverify_key = config.get("numverify_key")
    shodan_key = config.get("shodan_key")
    ipqs_key = config.get("ipqs_key")
    opencnam_key = config.get("opencnam_key")
    twilio_sid = config.get("twilio_sid")
    twilio_token = config.get("twilio_token")
    cashfree_client_id = config.get("cashfree_client_id")
    cashfree_client_secret = config.get("cashfree_client_secret")
    numlookupapi_key = config.get("numlookupapi_key")
    abstractapi_key = config.get("abstractapi_key")
    veriphone_key = config.get("veriphone_key")
    breach_files = []

    if "local_breach_search" in sections:
        status_console.print(Panel(
            "[bold yellow]Search from Breach / Local File[/bold yellow]\n"
            "Enter path(s) to CSV/XLSX file(s) YOU are authorized to search (comma-separated), "
            "or leave blank to skip. PhoneOsint does not bundle or hardcode any dataset.",
            border_style="yellow",
        ))
        raw_paths = Prompt.ask("  File path(s)", default="", show_default=False, console=status_console).strip()
        if raw_paths:
            breach_files = [p.strip() for p in raw_paths.split(",") if p.strip()]

    if "extra_apis" in sections:
        status_console.print(Panel("[bold yellow]Optional free-tier phone API keys[/bold yellow] (press Enter to skip/keep saved)", border_style="yellow"))
        numlookupapi_key = Prompt.ask("  numlookupapi.com key", default=numlookupapi_key or "", show_default=bool(numlookupapi_key), console=status_console).strip() or numlookupapi_key
        abstractapi_key = Prompt.ask("  Abstract API key", default=abstractapi_key or "", show_default=bool(abstractapi_key), console=status_console).strip() or abstractapi_key
        veriphone_key = Prompt.ask("  Veriphone.io key", default=veriphone_key or "", show_default=bool(veriphone_key), console=status_console).strip() or veriphone_key
        if any([numlookupapi_key, abstractapi_key, veriphone_key]):
            if Confirm.ask("Save these free-tier keys locally for next time?", default=False, console=status_console):
                config["numlookupapi_key"] = numlookupapi_key
                config["abstractapi_key"] = abstractapi_key
                config["veriphone_key"] = veriphone_key
                save_config(config)
                status_console.print("[green]Saved to ~/.phoneosint/config.json[/green]")

    if "paid_apis" in sections:
        status_console.print(Panel("[bold yellow]Optional API keys[/bold yellow] (press Enter to skip/keep saved)", border_style="yellow"))
        numverify_key = Prompt.ask("  Numverify key", default=numverify_key or "", show_default=bool(numverify_key), console=status_console).strip() or numverify_key
        shodan_key = Prompt.ask("  Shodan key", default=shodan_key or "", show_default=bool(shodan_key), console=status_console).strip() or shodan_key
        ipqs_key = Prompt.ask("  IPQS key", default=ipqs_key or "", show_default=bool(ipqs_key), console=status_console).strip() or ipqs_key
        opencnam_key = Prompt.ask("  OpenCNAM key", default=opencnam_key or "", show_default=bool(opencnam_key), console=status_console).strip() or opencnam_key
        status_console.print("[dim]  Twilio Lookup (HLR/line-type) and Cashfree (UPI name) both require a real business/KYC'd account -- no free public alternative exists.[/dim]")
        twilio_sid = Prompt.ask("  Twilio Account SID", default=twilio_sid or "", show_default=bool(twilio_sid), console=status_console).strip() or twilio_sid
        twilio_token = Prompt.ask("  Twilio Auth Token", default=twilio_token or "", show_default=bool(twilio_token), console=status_console).strip() or twilio_token
        cashfree_client_id = Prompt.ask("  Cashfree Client ID", default=cashfree_client_id or "", show_default=bool(cashfree_client_id), console=status_console).strip() or cashfree_client_id
        cashfree_client_secret = Prompt.ask("  Cashfree Client Secret", default=cashfree_client_secret or "", show_default=bool(cashfree_client_secret), console=status_console).strip() or cashfree_client_secret

        if any([numverify_key, shodan_key, ipqs_key, opencnam_key, twilio_sid, twilio_token, cashfree_client_id, cashfree_client_secret]):
            if Confirm.ask("Save these API keys locally for next time?", default=False, console=status_console):
                config["numverify_key"] = numverify_key
                config["shodan_key"] = shodan_key
                config["ipqs_key"] = ipqs_key
                config["opencnam_key"] = opencnam_key
                config["twilio_sid"] = twilio_sid
                config["twilio_token"] = twilio_token
                config["cashfree_client_id"] = cashfree_client_id
                config["cashfree_client_secret"] = cashfree_client_secret
                save_config(config)
                status_console.print("[green]Saved to ~/.phoneosint/config.json[/green]")

    use_tor = False
    if "dark_web" in sections:
        use_tor = Confirm.ask("Route dark web search through local Tor proxy?", default=False, console=status_console)

    status_console.print(Panel(f"[bold green]Starting scan for {number} ...[/bold green]", border_style="green"))

    return {
        "number": number,
        "country": country,
        "sections": sections,
        "numverify_key": numverify_key,
        "shodan_key": shodan_key,
        "ipqs_key": ipqs_key,
        "opencnam_key": opencnam_key,
        "twilio_sid": twilio_sid,
        "twilio_token": twilio_token,
        "cashfree_client_id": cashfree_client_id,
        "cashfree_client_secret": cashfree_client_secret,
        "numlookupapi_key": numlookupapi_key,
        "abstractapi_key": abstractapi_key,
        "veriphone_key": veriphone_key,
        "breach_files": breach_files,
        "use_tor": use_tor,
    }


def _format_value(value):
    """Render a value for display, fully expanding nested dicts/lists as
    pretty JSON so no data is ever silently dropped."""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, indent=2, ensure_ascii=False, default=str)
        except Exception:
            return str(value)
    return str(value)


def _kv_table(data: dict, key_style="cyan"):
    """Build a two-column rich Table from a dict of key/value pairs, showing
    every item in full (nested values are pretty-printed, never truncated)."""
    table = Table(box=box.SIMPLE_HEAVY, show_header=False, pad_edge=False, expand=True)
    table.add_column("Key", style=f"bold {key_style}", no_wrap=True, ratio=1)
    table.add_column("Value", style="white", overflow="fold", ratio=3)
    for key, value in data.items():
        table.add_row(str(key), _format_value(value))
    return table


def print_report(report):
    """Pretty-print the OSINT report to the terminal using rich panels/tables."""
    console.print()
    console.print(Panel(
        Text(f"Target: {report['target']}", style="bold green", justify="center"),
        title=f"[bold red]{TOOL_NAME} Report[/bold red]",
        subtitle=f"[dim]by {AUTHOR} · {GITHUB_URL}[/dim]",
        box=box.DOUBLE,
        border_style="yellow",
    ))

    if "exposure_score" in report:
        es = report["exposure_score"]
        level_color = {"High": "red", "Medium": "yellow", "Low": "green"}.get(es.get("level"), "white")
        es_text = Text()
        es_text.append(f"{es.get('score', 0)}/100  ", style=f"bold {level_color}")
        es_text.append(f"({es.get('level', 'Unknown')} exposure)\n", style=f"bold {level_color}")
        for reason in es.get("reasons", []):
            es_text.append(f"  • {reason}\n", style="white")
        console.print(Panel(es_text, title="[bold magenta]Exposure Score[/bold magenta]", border_style="magenta"))

    console.print(Panel(_kv_table(report["basic_info"]), title="[bold cyan]Basic Information[/bold cyan]", border_style="cyan"))

    if "direct_links" in report:
        console.print(Panel(_kv_table(report["direct_links"]), title="[bold cyan]Direct Links[/bold cyan]", border_style="cyan"))

    if "dorks" in report:
        console.print(Panel(_kv_table(report["dorks"]), title="[bold cyan]Google Dorks[/bold cyan]", border_style="cyan"))

    if "search_engines" in report:
        se_table = Table(box=box.SIMPLE_HEAVY, expand=True)
        se_table.add_column("Engine", style="bold magenta", no_wrap=True)
        se_table.add_column("Query Type", style="bold cyan", no_wrap=True)
        se_table.add_column("URL", style="white", overflow="fold")
        for engine, queries in report["search_engines"].items():
            for name, url in queries.items():
                se_table.add_row(engine, name, url)
        console.print(Panel(se_table, title="[bold cyan]Search Engines[/bold cyan]", border_style="cyan"))

    if "carrier_gateways" in report:
        console.print(Panel(_kv_table(report["carrier_gateways"]), title="[bold cyan]Carrier SMS/Email Gateways[/bold cyan]", border_style="cyan"))

    if "truecaller" in report:
        data = report["truecaller"]
        content = _kv_table(data) if isinstance(data, dict) else Text(str(data))
        console.print(Panel(content, title="[bold cyan]Truecaller[/bold cyan]", border_style="cyan"))

    if "telegram" in report:
        data = report["telegram"]
        content = _kv_table(data) if isinstance(data, dict) else Text(str(data))
        console.print(Panel(content, title="[bold cyan]Telegram[/bold cyan]", border_style="cyan"))

    if "external_tools" in report:
        tools_table = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True)
        tools_table.add_column("Tool", style="bold magenta", no_wrap=True, ratio=1)
        tools_table.add_column("Result", style="white", overflow="fold", ratio=4)
        for tool, data in report["external_tools"].items():
            tools_table.add_row(tool, _format_value(data))
        console.print(Panel(tools_table, title="[bold cyan]External Tools (auto-run)[/bold cyan]", border_style="cyan"))

    remote_sections = [
        ("dark_web", "Dark Web Search"),
        ("breach_lookup", "Breach / Paste Lookup"),
        ("local_breach_search", "Search from Breach / Local File"),
        ("email_lookup", "Email Lookup Dorks"),
        ("gravatar", "Gravatar Lookup"),
        ("business_dorks", "Business Directory Dorks"),
        ("aadhaar", "Aadhaar Linkage (informational only)"),
        ("paypal", "PayPal Name-Leak Technique (informational only)"),
        ("extra_apis", "Extra Free-Tier Phone APIs"),
        ("ignorant", "Account Existence (Instagram/Snapchat/Amazon)"),
        ("paid_enrichment", "Paid Enrichment"),
        ("numverify", "Numverify"),
        ("shodan", "Shodan"),
        ("hlr", "HLR / Line-Type (Twilio)"),
        ("upi", "UPI Name Lookup (Cashfree)"),
    ]
    for key, label in remote_sections:
        if key not in report:
            continue
        data = report[key]
        content = _kv_table(data) if isinstance(data, dict) else Text(_format_value(data))
        console.print(Panel(content, title=f"[bold cyan]{label}[/bold cyan]", border_style="cyan"))

    ref_tools = report.get("external_osint_tools", {})
    if ref_tools:
        ref_table = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True)
        ref_table.add_column("Tool", style="bold magenta", no_wrap=True)
        ref_table.add_column("Description", style="white", overflow="fold")
        ref_table.add_column("Command", style="bold green", overflow="fold")
        ref_table.add_column("Note", style="dim white", overflow="fold")
        for tool, meta in ref_tools.items():
            if isinstance(meta, dict):
                ref_table.add_row(
                    tool, meta.get("description", ""), meta.get("command", ""), meta.get("note", "")
                )
            else:
                ref_table.add_row(tool, str(meta), "", "")
        console.print(Panel(ref_table, title="[bold cyan]Reference: External OSINT Tool Commands[/bold cyan]", border_style="cyan"))

    if "_diff" in report:
        diff = report["_diff"]
        if diff.get("previous_scan_time"):
            if diff.get("changes"):
                diff_text = Text()
                for change in diff["changes"]:
                    diff_text.append(f"  • {change}\n", style="white")
            else:
                diff_text = Text("No changes since the last scan.", style="green")
            console.print(Panel(diff_text, title=f"[bold blue]Changes Since Last Scan ({diff['previous_scan_time']})[/bold blue]", border_style="blue"))
        else:
            console.print(Panel(Text(diff.get("note", "No previous scan found for this number.")), title="[bold blue]Changes Since Last Scan[/bold blue]", border_style="blue"))

    console.print(Panel(
        Text(report.get("disclaimer", ""), style="italic yellow"),
        title="[bold yellow]Disclaimer[/bold yellow]",
        border_style="yellow",
    ))
    console.print("[dim]Full JSON report can be saved with -o <file>[/dim]\n")


def _history_file_for(e164: str) -> Path:
    safe_name = e164.lstrip("+") or "unknown"
    return HISTORY_DIR / f"{safe_name}.json"


def save_history(e164: str, report: dict):
    """Append a timestamped copy of this scan to the number's local history
    file, keeping only the last HISTORY_KEEP entries."""
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        hist_file = _history_file_for(e164)
        entries = []
        if hist_file.exists():
            try:
                entries = json.loads(hist_file.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        entries.append({
            "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "report": report,
        })
        entries = entries[-HISTORY_KEEP:]
        hist_file.write_text(json.dumps(entries, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        print(Fore.YELLOW + f"[!] Could not save scan history: {exc}" + Style.RESET_ALL, file=sys.stderr)


def load_last_history(e164: str):
    """Return the most recent prior history entry for this number, or None."""
    hist_file = _history_file_for(e164)
    if not hist_file.exists():
        return None
    try:
        entries = json.loads(hist_file.read_text(encoding="utf-8"))
        return entries[-1] if entries else None
    except Exception:
        return None


def _strip_volatile(value):
    """Remove top-level '*_error' keys before diffing -- these embed
    non-deterministic content (e.g. object memory addresses in exception
    text) that would otherwise cause false-positive 'changed' diffs on every
    run even when nothing meaningful actually changed."""
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if not k.endswith("_error")}
    return value


def diff_reports(previous: dict, current: dict) -> dict:
    """Shallow diff of section presence/keys plus a stringified deep-compare
    of nested values, without needing a dedicated diff library."""
    changes = []
    prev_keys = set(previous.keys()) - {"_diff"}
    curr_keys = set(current.keys()) - {"_diff"}

    for key in sorted(curr_keys - prev_keys):
        changes.append(f"New section added: '{key}'")
    for key in sorted(prev_keys - curr_keys):
        changes.append(f"Section removed: '{key}'")

    for key in sorted(prev_keys & curr_keys):
        if key in ("target", "external_osint_tools", "disclaimer", "exposure_score", "_diff"):
            continue
        prev_val = previous[key]
        curr_val = current[key]
        try:
            prev_str = json.dumps(_strip_volatile(prev_val), sort_keys=True, default=str)
            curr_str = json.dumps(_strip_volatile(curr_val), sort_keys=True, default=str)
        except Exception:
            prev_str, curr_str = str(prev_val), str(curr_val)
        if prev_str != curr_str:
            if isinstance(prev_val, dict) and isinstance(curr_val, dict) and "registered" in prev_val and "registered" in curr_val:
                changes.append(f"'{key}': registered {prev_val.get('registered')} -> {curr_val.get('registered')}")
            else:
                changes.append(f"'{key}' changed since last scan")

    return {"changes": changes}


def export_csv(report, path):
    """Flatten the report into (section, key, value) rows and write a CSV.
    Nested dict/list values are serialized as JSON strings per cell."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["section", "key", "value"])
        reports = report if isinstance(report, list) else [report]
        for rep in reports:
            target = rep.get("target", "")
            for section, data in rep.items():
                if isinstance(data, dict):
                    for key, value in data.items():
                        writer.writerow([f"{target}:{section}", key, _format_value(value)])
                else:
                    writer.writerow([target, section, _format_value(data)])


def export_html(report, path):
    """Render the same panels used in print_report() to an HTML file via
    rich's built-in Console(record=True).export_html()."""
    record_console = Console(record=True, width=120)
    original = globals()["console"]
    globals()["console"] = record_console
    try:
        reports = report if isinstance(report, list) else [report]
        for rep in reports:
            print_report(rep)
    finally:
        globals()["console"] = original
    record_console.save_html(str(path))


def export_pdf(report, path):
    """Render a simple multi-section PDF using reportlab (pure-Python, no
    system dependencies). Falls back to an error dict if reportlab isn't
    installed."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table as RLTable, TableStyle
    except ImportError:
        return {"error": "reportlab not installed. Run: pip install reportlab"}

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    story = [Paragraph(f"{TOOL_NAME} Report", styles["Title"])]

    reports = report if isinstance(report, list) else [report]
    for rep in reports:
        story.append(Paragraph(f"Target: {rep.get('target', '')}", styles["Heading2"]))
        story.append(Spacer(1, 8))
        for section, data in rep.items():
            if section == "target":
                continue
            story.append(Paragraph(section, styles["Heading3"]))
            if isinstance(data, dict):
                rows = [["Key", "Value"]] + [[str(k), _format_value(v)[:500]] for k, v in data.items()]
                table = RLTable(rows, colWidths=[140, 340])
                table.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                ]))
                story.append(table)
            else:
                story.append(Paragraph(_format_value(data)[:1000], styles["Normal"]))
            story.append(Spacer(1, 10))

    doc.build(story)
    return {"saved_to": str(path)}


def _safe_call(fn, *args, **kwargs):
    """Run a synchronous, supposedly-pure section function defensively --
    if it ever raises for an unexpected edge-case input, capture the error
    instead of taking down the entire scan."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        return {"error": f"{fn.__name__} failed: {exc}"}


def build_report(number: str, country: str, sections: set, args, config: dict, use_tor: bool = False, allow_prompt: bool = True):
    """Build a full OSINT report for a single number, running network-bound
    sections concurrently for speed. Never raises: any unexpected internal
    error is converted into an (None, error_message) result."""
    try:
        parsed = normalize(number, country)
    except Exception as exc:
        return None, f"Failed to parse phone number {number}: {exc}"
    if not parsed:
        return None, f"Invalid or unsupported phone number: {number} (country: {country})"

    try:
        info = basic_info(parsed)
    except Exception as exc:
        return None, f"Failed to extract basic info for {number}: {exc}"
    e164 = info["e164"]
    digits = e164.lstrip("+")

    report = {
        "target": number,
        "basic_info": info,
        "external_osint_tools": _safe_call(external_tool_commands, e164),
        "disclaimer": (
            "This tool only gathers publicly accessible metadata. "
            "Names, exact addresses, and private accounts require paid/authorized data or breach sources. "
            "City/region accuracy depends on carrier and public data availability."
        ),
    }

    if "dorks" in sections:
        report["dorks"] = _safe_call(generate_dorks, e164)
    if "search_engines" in sections:
        report["search_engines"] = _safe_call(search_engine_dorks, e164)
    if "direct_links" in sections:
        report["direct_links"] = _safe_call(direct_links, e164)
    if "carrier_gateways" in sections:
        report["carrier_gateways"] = _safe_call(carrier_gateways, digits, country)
    if "email_lookup" in sections:
        report["email_lookup"] = _safe_call(email_lookup_from_phone, e164)
    if "business_dorks" in sections:
        report["business_dorks"] = _safe_call(business_directory_dorks, e164)
    if "aadhaar" in sections:
        report["aadhaar"] = _safe_call(aadhaar_linkage_note, e164)
    if "paypal" in sections:
        report["paypal"] = _safe_call(paypal_name_leak_note, e164)

    # Network-bound / subprocess-bound sections run concurrently since they're
    # independent of each other and mostly I/O-bound.
    jobs = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        if "dark_web" in sections:
            jobs[pool.submit(dark_web_search, e164, use_tor)] = "dark_web"
        if "breach_lookup" in sections:
            jobs[pool.submit(breach_lookup, e164)] = "breach_lookup"
        if "local_breach_search" in sections:
            jobs[pool.submit(
                local_file_search, e164, info["national_number"],
                getattr(args, "breach_file", None) or [],
            )] = "local_breach_search"
        if "gravatar" in sections:
            jobs[pool.submit(gravatar_lookup, getattr(args, "email", None))] = "gravatar"
        if "extra_apis" in sections:
            jobs[pool.submit(
                extra_phone_apis, e164,
                getattr(args, "numlookupapi_key", None),
                getattr(args, "abstractapi_key", None),
                getattr(args, "veriphone_key", None),
            )] = "extra_apis"
        if "ignorant" in sections:
            jobs[pool.submit(ignorant_lookup, info["country_code"], info["national_number"])] = "ignorant"
        if "truecaller" in sections:
            jobs[pool.submit(truecaller_lookup, e164, country, config, allow_prompt)] = "truecaller"
        if "telegram" in sections:
            jobs[pool.submit(telegram_lookup, e164, config, allow_prompt)] = "telegram"
        if "external_tools" in sections:
            jobs[pool.submit(run_external_tools, e164, digits)] = "external_tools"
        if "paid_apis" in sections:
            jobs[pool.submit(numverify_check, e164, args.numverify_key)] = "numverify"
            jobs[pool.submit(shodan_check, e164, args.shodan_key)] = "shodan"
            jobs[pool.submit(paid_enrichment, e164, args.ipqs_key, args.opencnam_key)] = "paid_enrichment"
            jobs[pool.submit(twilio_lookup, e164, args.twilio_sid, args.twilio_token)] = "hlr"
            jobs[pool.submit(upi_lookup, e164, args.cashfree_client_id, args.cashfree_client_secret)] = "upi"

        for future in as_completed(jobs):
            key = jobs[future]
            try:
                report[key] = future.result()
            except Exception as exc:
                report[key] = {"error": str(exc)}

    report["exposure_score"] = _safe_call(exposure_score, report)

    if getattr(args, "diff", False):
        try:
            last = load_last_history(e164)
            if last:
                report["_diff"] = diff_reports(last.get("report", {}), report)
                report["_diff"]["previous_scan_time"] = last.get("scanned_at", "unknown")
            else:
                report["_diff"] = {"changes": [], "note": "No previous scan found for this number."}
        except Exception as exc:
            report["_diff"] = {"changes": [], "note": f"Diff computation failed: {exc}"}

    if not getattr(args, "no_history", False):
        save_history(e164, report)

    return report, None


def _safe_write_json(data, path, label):
    """Write JSON to disk, printing a friendly error on failure (bad path,
    missing parent directory, permissions, non-serializable data, etc.)
    instead of crashing after the report was already generated/shown."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        status_console.print(f"[bold green][+] {label} saved to {path}[/bold green]")
    except Exception as exc:
        status_console.print(f"[bold red][!] Could not save {label.lower()} to {path}: {exc}[/bold red]")


def _safe_export(label, fn, *args):
    """Run an export function (CSV/HTML/PDF), printing a friendly error on
    failure instead of crashing after the report was already generated/shown."""
    try:
        result = fn(*args)
        if isinstance(result, dict) and result.get("error"):
            status_console.print(f"[bold red][!] {label} failed: {result['error']}[/bold red]")
        else:
            status_console.print(f"[bold green][+] {label} saved to {args[-1]}[/bold green]")
    except Exception as exc:
        status_console.print(f"[bold red][!] {label} failed: {exc}[/bold red]")


def main():
    parser = argparse.ArgumentParser(
        description="PhoneOsint - Master phone number OSINT aggregator"
    )
    parser.add_argument(
        "number", nargs="?", default=None,
        help="Phone number to investigate (interactive if omitted)"
    )
    parser.add_argument(
        "--file", "-f", help="Batch-scan phone numbers listed one per line in this file"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output raw JSON instead of pretty report"
    )
    parser.add_argument(
        "--country", "-c", default=DEFAULT_COUNTRY,
        help="Default ISO region code when no + prefix (e.g., US, IN, GB -- not the numeric calling code)"
    )
    parser.add_argument(
        "--numverify-key", help="Numverify API access key (optional)"
    )
    parser.add_argument(
        "--shodan-key", help="Shodan API key (optional)"
    )
    parser.add_argument(
        "--ipqs-key", help="IPQualityScore API key (optional)"
    )
    parser.add_argument(
        "--opencnam-key", help="OpenCNAM API key (optional caller ID name)"
    )
    parser.add_argument(
        "--twilio-sid", help="Twilio Account SID for HLR/line-type lookup (optional, paid Twilio feature)"
    )
    parser.add_argument(
        "--twilio-token", help="Twilio Auth Token for HLR/line-type lookup (optional, paid Twilio feature)"
    )
    parser.add_argument(
        "--cashfree-client-id", help="Cashfree Payouts Client ID for UPI name lookup (optional, requires KYC'd merchant account)"
    )
    parser.add_argument(
        "--cashfree-client-secret", help="Cashfree Payouts Client Secret for UPI name lookup (optional, requires KYC'd merchant account)"
    )
    parser.add_argument(
        "--email", help="A known email linked to this number, used for the free Gravatar lookup (optional; PhoneOsint cannot discover this automatically)"
    )
    parser.add_argument(
        "--breach-file", action="append", metavar="PATH",
        help="Path to a CSV/XLSX file you are authorized to search for this number (repeatable). "
             "PhoneOsint does not bundle or hardcode any dataset -- you must supply your own."
    )
    parser.add_argument(
        "--numlookupapi-key", help="numlookupapi.com free-tier API key (optional)"
    )
    parser.add_argument(
        "--abstractapi-key", help="Abstract API Phone Validation free-tier key (optional)"
    )
    parser.add_argument(
        "--veriphone-key", help="Veriphone.io free-tier API key (optional)"
    )
    parser.add_argument(
        "--output", "-o", help="Save JSON report to a file"
    )
    parser.add_argument(
        "--export-csv", help="Also export the report as a flattened CSV file"
    )
    parser.add_argument(
        "--export-html", help="Also export the pretty report as an HTML file"
    )
    parser.add_argument(
        "--export-pdf", help="Also export the report as a PDF file (requires reportlab)"
    )
    parser.add_argument(
        "--diff", action="store_true", help="Show what changed since the last saved scan of this number"
    )
    parser.add_argument(
        "--no-history", action="store_true", help="Do not save this scan to local history (~/.phoneosint/history)"
    )
    parser.add_argument(
        "--all", action="store_true", help="Run every section non-interactively (skips selection menu)"
    )
    parser.add_argument(
        "--run-tools", action="store_true", help="Auto-run installed external OSINT tools (Sherlock, Maigret, PhoneInfoga, etc.)"
    )
    parser.add_argument(
        "--tor", action="store_true", help="Route dark web search through local Tor SOCKS proxy (127.0.0.1:9050)"
    )
    parser.add_argument(
        "--truecaller", action="store_true", help="Enable free Truecaller name lookup (requires one-time login)"
    )
    parser.add_argument(
        "--telegram", action="store_true", help="Enable free Telegram registration/name check (requires one-time login)"
    )
    parser.add_argument(
        "--save-config", action="store_true", help="Save provided API keys to ~/.phoneosint/config.json"
    )
    parser.add_argument(
        "--no-config", action="store_true", help="Do not load saved keys from ~/.phoneosint/config.json"
    )
    parser.add_argument(
        "--no-banner", action="store_true", help="Suppress the startup banner"
    )
    parser.add_argument(
        "--version", action="version",
        version=f"{TOOL_NAME} — by {AUTHOR} ({GITHUB_URL})",
    )
    args = parser.parse_args()

    if not args.json and not args.no_banner and (args.number is not None or args.file):
        print_banner()

    config = {} if args.no_config else load_config()
    # Truecaller, Telegram, and external_tools are opt-in only (via --truecaller /
    # --telegram / --run-tools or the interactive menu) since they can block on
    # input() or run slow subprocesses.
    default_cli_sections = set(ALL_SECTIONS) - {"truecaller", "telegram", "external_tools"}
    sections = set(default_cli_sections)
    use_tor = args.tor

    if args.number is None and not args.file:
        choice = interactive(show_banner=not args.no_banner)
        args.number = choice["number"]
        args.country = choice["country"]
        args.numverify_key = args.numverify_key or choice["numverify_key"]
        args.shodan_key = args.shodan_key or choice["shodan_key"]
        args.ipqs_key = args.ipqs_key or choice["ipqs_key"]
        args.opencnam_key = args.opencnam_key or choice["opencnam_key"]
        args.twilio_sid = args.twilio_sid or choice["twilio_sid"]
        args.twilio_token = args.twilio_token or choice["twilio_token"]
        args.cashfree_client_id = args.cashfree_client_id or choice["cashfree_client_id"]
        args.cashfree_client_secret = args.cashfree_client_secret or choice["cashfree_client_secret"]
        args.numlookupapi_key = args.numlookupapi_key or choice["numlookupapi_key"]
        args.abstractapi_key = args.abstractapi_key or choice["abstractapi_key"]
        args.veriphone_key = args.veriphone_key or choice["veriphone_key"]
        args.breach_file = args.breach_file or choice["breach_files"] or None
        sections = choice["sections"]
        use_tor = use_tor or choice["use_tor"]
    else:
        args.numverify_key = args.numverify_key or config.get("numverify_key")
        args.shodan_key = args.shodan_key or config.get("shodan_key")
        args.ipqs_key = args.ipqs_key or config.get("ipqs_key")
        args.opencnam_key = args.opencnam_key or config.get("opencnam_key")
        args.twilio_sid = args.twilio_sid or config.get("twilio_sid")
        args.twilio_token = args.twilio_token or config.get("twilio_token")
        args.cashfree_client_id = args.cashfree_client_id or config.get("cashfree_client_id")
        args.cashfree_client_secret = args.cashfree_client_secret or config.get("cashfree_client_secret")
        args.numlookupapi_key = args.numlookupapi_key or config.get("numlookupapi_key")
        args.abstractapi_key = args.abstractapi_key or config.get("abstractapi_key")
        args.veriphone_key = args.veriphone_key or config.get("veriphone_key")
        if args.all:
            sections = set(ALL_SECTIONS)

    if args.run_tools:
        sections.add("external_tools")
    if args.truecaller:
        sections.add("truecaller")
    if args.telegram:
        sections.add("telegram")

    if args.save_config:
        config["numverify_key"] = args.numverify_key
        config["shodan_key"] = args.shodan_key
        config["ipqs_key"] = args.ipqs_key
        config["opencnam_key"] = args.opencnam_key
        config["twilio_sid"] = args.twilio_sid
        config["twilio_token"] = args.twilio_token
        config["cashfree_client_id"] = args.cashfree_client_id
        config["cashfree_client_secret"] = args.cashfree_client_secret
        config["numlookupapi_key"] = args.numlookupapi_key
        config["abstractapi_key"] = args.abstractapi_key
        config["veriphone_key"] = args.veriphone_key
        save_config(config)

    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                numbers = [line.strip() for line in f if line.strip()]
        except OSError as exc:
            status_console.print(f"[bold red][!] Could not read {args.file}: {exc}[/bold red]")
            sys.exit(1)

        if not numbers:
            status_console.print(f"[bold red][!] No numbers found in {args.file}[/bold red]")
            sys.exit(1)

        batch_sections = sections - {"truecaller", "telegram"}  # avoid login prompts across many numbers
        status_console.print(f"[bold cyan][*] Batch-scanning {len(numbers)} numbers...[/bold cyan]")
        batch_results = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            future_map = {
                pool.submit(build_report, num, args.country, batch_sections, args, config, use_tor, False): num
                for num in numbers
            }
            for future in as_completed(future_map):
                num = future_map[future]
                try:
                    rep, err = future.result()
                except Exception as exc:
                    rep, err = None, str(exc)
                if err:
                    status_console.print(f"[bold red][!] {num}: {err}[/bold red]")
                    continue
                batch_results.append(rep)
                status_console.print(f"[bold green][+] Done: {num}[/bold green]")

        if args.json:
            print(json.dumps(batch_results, indent=2))
        if args.output:
            _safe_write_json(batch_results, args.output, "Batch report")
        elif not args.json:
            for rep in batch_results:
                print_report(rep)
        if args.export_csv:
            _safe_export("CSV export", export_csv, batch_results, args.export_csv)
        if args.export_html:
            _safe_export("HTML export", export_html, batch_results, args.export_html)
        if args.export_pdf:
            _safe_export("PDF export", export_pdf, batch_results, args.export_pdf)
        return

    if not args.number:
        parser.print_help()
        sys.exit(1)

    report, err = build_report(args.number, args.country, sections, args, config, use_tor)
    if err:
        status_console.print(f"[bold red][!] {err}[/bold red]")
        sys.exit(1)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    if args.output:
        _safe_write_json(report, args.output, "Report")

    if args.export_csv:
        _safe_export("CSV export", export_csv, report, args.export_csv)
    if args.export_html:
        _safe_export("HTML export", export_html, report, args.export_html)
    if args.export_pdf:
        _safe_export("PDF export", export_pdf, report, args.export_pdf)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n[!] Interrupted by user." + Style.RESET_ALL, file=sys.stderr)
        sys.exit(130)
    except EOFError:
        print(Fore.RED + "\n[!] Input ended unexpectedly (EOF) while waiting for a prompt response." + Style.RESET_ALL, file=sys.stderr)
        sys.exit(1)
