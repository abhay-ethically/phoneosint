# PhoneOsint

A Linux/macOS command-line phone number OSINT aggregator.

**Author:** [abhay-ethically](https://github.com/abhay-ethically)

## Features

- Validates and normalizes any phone number
- Extracts country, region, timezone, carrier, and line type
- Generates Google dork URLs for accounts/services
- Builds search URLs for Google, Bing, DuckDuckGo, Yahoo, Yandex, Brave
- Builds direct deep links (WhatsApp, Telegram, Viber)
- Carrier SMS/email gateway address generator (mostly US carriers)
- Free Truecaller name lookup via `truecallerpy` (one-time login, no paid key)
- Free Telegram registration/name check via `telethon` (one-time login with your own account, no paid key)
- Public paste/breach dump lookup (psbdmp)
- **Search from Breach / Local File** (`--breach-file <path.csv|.xlsx>`, repeatable): searches any CSV/XLSX file **you** provide and are authorized to use for rows matching the queried number. PhoneOsint does not bundle, ship, or hardcode any dataset -- you must supply your own.
- Dark web search engine links (Ahmia, DarkSearch, Haystak, Torch), with optional live Tor-routed search (`--tor`)
- **Account existence check** on Instagram, Snapchat, and Amazon via the free `ignorant` library (free, no key, no login -- runs automatically, calls its internal modules directly instead of shelling out)
- Auto-runs installed external tools (**PhoneInfoga** with a real prebuilt binary, Maigret, Sherlock) via `--run-tools` and merges their **full, untruncated** output into the report
- Reference commands for Holehe, Twint, GHunt, SpiderFoot, theHarvester, and **Mr.Holmes** (documented as interactive-menu-only -- it cannot be safely auto-scripted)
- Optional paid remote lookups: Numverify, Shodan, IPQualityScore, OpenCNAM, Twilio Lookup (HLR/line-type/SIM-swap), Cashfree (UPI account-holder name) (all no-op without a key — nothing is blocked by lack of paid access)
- Optional **free-tier** phone validation APIs: numlookupapi.com, Abstract API, Veriphone.io (small free monthly quota, more current than the offline carrier database)
- **Gravatar lookup** for a known email (free, no key) — useful if you already have an email tied to the number
- **Business directory dorks** (Google Maps, JustDial, Sulekha, IndiaMart, Yelp) for numbers registered to businesses
- **Exposure Score**: a derived 0–100 heuristic summarizing how exposed a number is based on the other sections (Telegram registered, Truecaller name resolved, breach hits, etc.)
- **Aadhaar linkage** and **PayPal name-leak** sections: informational-only placeholders explaining why no free/legal automated lookup exists for either — no real requests are made, no login, no scraping
- Scan history (`~/.phoneosint/history/`) and `--diff` to show what changed since the last scan of a number
- Export to CSV (`--export-csv`), HTML (`--export-html`), and PDF (`--export-pdf`, via `reportlab`) in addition to JSON
- Automatic retry with exponential backoff on 429/5xx/connection errors for all remote API calls
- Fully interactive mode with a `rich`-powered checklist menu, prompts, and confirmations when no number is given
- Batch mode (`--file numbers.txt`) to scan multiple numbers in parallel
- Local config file (`~/.phoneosint/config.json`) to save API keys / Truecaller session between runs
- Polished, structured, color-coded terminal report (panels + tables via `rich`), plus a startup banner
- Outputs raw JSON with `--json` or `-o report.json` (banner/formatting never pollutes JSON output)

## Install

```bash
cd PhoneOsint
bash install.sh
```

`install.sh` creates an isolated virtual environment (`venv/`) and installs everything into it -- this avoids the "externally-managed-environment" (PEP 668) pip errors on Kali/Debian/Ubuntu. It also clones/sets up common OSINT tools (Maigret, Sherlock, Holehe, GHunt, PhoneInfoga, SpiderFoot, theHarvester, Mr.Holmes). Run `bash uninstall.sh` to remove the global command, venv, config/history, and cloned tools.

## Usage

```bash
phoneosint +1234567890
phoneosint 9876543210 --country IN
phoneosint --json +1234567890 > report.json
phoneosint +1234567890 -o report.json

# Free extras
phoneosint +1234567890 --run-tools          # auto-run installed Sherlock/Maigret/PhoneInfoga
phoneosint +1234567890 --tor                # live dark web search via local Tor proxy
phoneosint +1234567890 --truecaller         # free Truecaller name lookup (one-time login)
phoneosint +1234567890 --telegram           # free Telegram registration/name check (one-time login)
phoneosint +1234567890 --all                # run every section in one shot

# Optional paid APIs (skipped automatically if no key given)
phoneosint +1234567890 --numverify-key YOUR_KEY -o report.json
phoneosint +1234567890 --shodan-key YOUR_KEY --ipqs-key YOUR_KEY -o report.json
phoneosint +1234567890 --twilio-sid YOUR_SID --twilio-token YOUR_TOKEN         # HLR / line-type / SIM-swap
phoneosint +1234567890 --cashfree-client-id ID --cashfree-client-secret SECRET # UPI account-holder name

# Batch mode: scan many numbers from a file (one per line), run in parallel
phoneosint --file numbers.txt -o batch_report.json --json

# Free-tier phone APIs (numlookupapi.com / Abstract API / Veriphone.io)
phoneosint +1234567890 --numlookupapi-key YOUR_KEY --abstractapi-key YOUR_KEY --veriphone-key YOUR_KEY

# Gravatar lookup (only useful if you already know an email for this number)
phoneosint +1234567890 --email someone@example.com

# Change detection: see what's different since the last scan of this number
phoneosint +1234567890 --diff

# Export formats (in addition to JSON via -o)
phoneosint +1234567890 --export-csv report.csv --export-html report.html --export-pdf report.pdf
```

Network-bound sections (dark web, breach lookup, Truecaller, Telegram, external tools, paid APIs) run **concurrently** via a thread pool, so a full scan is much faster than running each check sequentially. Batch mode (`--file`) also scans multiple numbers in parallel and skips Truecaller/Telegram login prompts automatically.

## Interactive mode

```bash
phoneosint
```

Prompts for a phone number, default country, and a checklist menu to pick which sections to run (or `a` for all). If the paid-API section is selected, you'll be asked for keys with an option to save them locally.

## Free-first philosophy

Everything works with **zero API keys**: dorks, search engine links, direct links, carrier gateways, dark web links (+ live Tor search), breach/paste dorks, email-lookup dorks, Truecaller (free, unofficial), Telegram registration/name check (free, unofficial), and auto-run of installed external tools. The `--numverify-key`, `--shodan-key`, `--ipqs-key`, `--opencnam-key`, `--twilio-sid/--twilio-token`, and `--cashfree-client-id/--cashfree-client-secret` flags remain fully optional and simply no-op if omitted — no feature requires them.

**Honest note on HLR and UPI lookups:** there is no free, public, unauthenticated API for either. Twilio Lookup's line-type/SIM-swap data and Cashfree's UPI-to-name resolution both require a real (Twilio/KYC-approved Cashfree Payouts) account — this tool integrates them as optional enrichers for users who already have such credentials, not as a magic free lookup.

## New flags reference

| Flag | Purpose |
|---|---|
| `--run-tools` | Auto-run installed Sherlock/Maigret/PhoneInfoga via subprocess and merge output |
| `--tor` | Route dark web search through local Tor SOCKS proxy (`127.0.0.1:9050`), falls back to links if Tor isn't running |
| `--truecaller` | Enable free Truecaller name lookup (one-time OTP login, session cached) |
| `--telegram` | Enable free Telegram registration/name check (one-time login, session cached) |
| `--twilio-sid` / `--twilio-token` | Twilio Account SID/Auth Token for HLR/line-type/SIM-swap lookup (paid Twilio feature, no free alternative) |
| `--cashfree-client-id` / `--cashfree-client-secret` | Cashfree Payouts credentials for UPI account-holder name lookup (requires your own KYC-approved merchant account) |
| `--email` | A known email tied to the number, used only for the free Gravatar lookup (not auto-discovered) |
| `--numlookupapi-key` / `--abstractapi-key` / `--veriphone-key` | Free-tier keys for extra live phone validation APIs |
| `--export-csv` / `--export-html` / `--export-pdf` | Export the report in that format in addition to JSON |
| `--diff` | Show what changed since the last saved scan of this number |
| `--no-history` | Don't save this scan to `~/.phoneosint/history` |
| `--all` | Run every section non-interactively |
| `--file FILE` | Batch-scan phone numbers listed one per line in `FILE`, in parallel |
| `--save-config` | Save provided API keys to `~/.phoneosint/config.json` |
| `--no-config` | Skip loading saved keys/session from the config file |
| `--no-banner` | Suppress the startup banner |
| `--version` | Print tool name, author, and GitHub link |

## Config file

Saved keys and the Truecaller/Telegram sessions live in `~/.phoneosint/config.json` (permissions locked to your user). Delete it any time to reset.

## Telegram lookup setup

1. Get a free `api_id`/`api_hash` from [my.telegram.org](https://my.telegram.org) (API Development Tools) using your own Telegram account.
2. Run `phoneosint +1234567890 --telegram` and follow the one-time login prompt (phone number + code, and 2FA password if enabled).
3. The session is cached in `~/.phoneosint/config.json`; subsequent runs won't need to log in again.
4. Results depend on the target's privacy settings (`inputPrivacyKeyAddedByPhone`) — some accounts hide themselves from phone-number lookups entirely.

## Optional paid APIs for name/location

- **Numverify**: country, location, carrier, line type
- **IPQualityScore**: phone validation, location/risks
- **OpenCNAM**: caller ID name (US/CA landlines mainly)

## Aadhaar & PayPal sections — read this

These two sections are **informational only**. There is no free, public, or legal API that maps a phone number to Aadhaar data — this is restricted by India's Aadhaar Act, 2016; only UIDAI-licensed entities can perform consent-based e-KYC, and law enforcement must go through official UIDAI/legal channels. Similarly, the PayPal "send money" name-leak trick is explained but **not automated** — doing so requires a real logged-in session and violates PayPal's Terms of Service. Both sections make **zero network requests** and return **zero real personal data**; they exist purely to document what is (and isn't) actually possible, and why.

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests cover the pure functions only (`normalize`, `carrier_gateways`, `exposure_score`, `diff_reports`, etc.) — no network calls, no external services required.

## Limitations

- No tool can guarantee names or every account linked to a number.
- City/state accuracy depends on the carrier and public data; many countries only return country-level data.
- Carrier SMS/email gateways are unofficial guesses (mostly US-only); the real carrier isn't confirmed without a paid API.
- Sherlock results run against phone-digit-derived usernames are guesses, not confirmed identity matches.
- Truecaller lookup depends on an unofficial library (`truecallerpy`) and requires a one-time login with your own number.
- The Instagram/Snapchat/Amazon check depends on the unofficial `ignorant` library and those platforms' undocumented endpoints, which can change or rate-limit without notice.
- Mr.Holmes is a fully interactive, menu-driven tool with no CLI flags -- it's documented as a reference command only and is never auto-run.
- Infoga (`m4ll0k/Infoga`) has been removed from GitHub and is no longer cloned by `install.sh`. SpiderFoot requires its own dedicated server/API key setup; it's cloned by `install.sh` but not auto-run.
- Live Tor dark web search requires a Tor daemon running locally (`tor`).
- Names and private accounts otherwise require paid, authorized, or breach data sources.
- Use responsibly and legally.

## Credits

Built and maintained by **[abhay-ethically](https://github.com/abhay-ethically)**.
