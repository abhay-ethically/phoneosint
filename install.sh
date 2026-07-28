#!/usr/bin/env bash
# Installs PhoneOsint and its optional external Python-based OSINT tools
# into a single isolated virtual environment (PhoneOsint/venv). This avoids
# the "externally-managed-environment" (PEP 668) errors that pip raises on
# Kali/Debian/Ubuntu when installing into the system Python.
set -u

TOOL_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TOOL_DIR" || exit 1

VENV_DIR="$TOOL_DIR/venv"
echo "[*] Creating virtual environment at $VENV_DIR (if not already present)..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR" || {
        echo "[!] Failed to create venv. On Debian/Kali/Ubuntu, install it first: sudo apt install python3-venv"
        exit 1
    }
fi
VENV_PIP="$VENV_DIR/bin/pip"
VENV_PY="$VENV_DIR/bin/python3"

echo "[*] Installing PhoneOsint Python dependencies into the venv..."
"$VENV_PIP" install --upgrade pip >/dev/null 2>&1
"$VENV_PIP" install -r requirements.txt

echo "[*] Making phoneosint.py executable..."
chmod +x phoneosint.py

read -rp "Install the global 'phoneosint' command (needs sudo)? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    WRAPPER=$(mktemp)
    cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
export PATH="$VENV_DIR/bin:\$PATH"
exec "$VENV_PY" "$TOOL_DIR/phoneosint.py" "\$@"
EOF
    chmod +x "$WRAPPER"
    sudo mv "$WRAPPER" /usr/local/bin/phoneosint
    echo "[+] phoneosint installed globally (runs via the venv). Run it with: phoneosint +1234567890"
fi

OSINT_DIR="$HOME/.phoneosint-tools"
mkdir -p "$OSINT_DIR"
cd "$OSINT_DIR" || exit 1

echo "[*] Installing pip-based OSINT tools into the same venv (so 'phoneosint' can find them on PATH)..."
"$VENV_PIP" install maigret holehe ghunt truecallerpy sherlock-project ignorant || true

echo "[*] Installing PhoneInfoga (prebuilt binary, no Go toolchain required)..."
if command -v phoneinfoga >/dev/null 2>&1; then
    echo "[+] phoneinfoga already installed: $(command -v phoneinfoga)"
else
    if command -v curl >/dev/null 2>&1; then
        (
            cd "$OSINT_DIR" || exit 1
            curl -sSL https://raw.githubusercontent.com/sundowndev/phoneinfoga/master/support/scripts/install | bash -s -- --skip-checksum 2>/dev/null
            if [ -f "$OSINT_DIR/phoneinfoga" ]; then
                read -rp "Install phoneinfoga globally to /usr/local/bin (needs sudo)? [y/N] " pi_ans
                if [[ "$pi_ans" =~ ^[Yy]$ ]]; then
                    sudo install "$OSINT_DIR/phoneinfoga" /usr/local/bin/phoneinfoga
                    echo "[+] phoneinfoga installed globally."
                else
                    echo "[+] phoneinfoga binary downloaded to $OSINT_DIR/phoneinfoga (add it to your PATH)."
                fi
            else
                echo "[!] PhoneInfoga auto-install failed. Install manually: https://github.com/sundowndev/phoneinfoga/releases"
            fi
        )
    else
        echo "[!] curl not found; install PhoneInfoga manually: https://github.com/sundowndev/phoneinfoga/releases"
    fi
fi

if ! command -v tor >/dev/null 2>&1; then
    echo "[*] Tor not found. Install it for the --tor flag (dark web live search):"
    echo "      macOS:  brew install tor && tor"
    echo "      Linux:  sudo apt install tor && tor"
else
    echo "[+] Tor is installed. Run 'tor' in the background, then use phoneosint --tor."
fi

clone_tool() {
    local name=$1
    local repo=$2
    echo "[*] Setting up $name..."
    if [ ! -d "$name" ]; then
        # GIT_TERMINAL_PROMPT=0 makes a missing/private/renamed repo fail
        # fast instead of hanging on an interactive username/password prompt.
        GIT_TERMINAL_PROMPT=0 git clone --depth 1 "$repo" "$name" || {
            echo "[!] Could not clone $name from $repo (repo may have moved/been removed). Skipping."
            return
        }
    else
        echo "[*] $name already exists, skipping clone"
    fi
    if [ -f "$name/requirements.txt" ]; then
        echo "[*] Installing $name's Python dependencies into the venv..."
        "$VENV_PIP" install -r "$name/requirements.txt" || true
    fi
}

# Infoga (m4ll0k/Infoga) has been removed from GitHub and is intentionally
# no longer cloned here.
clone_tool "SpiderFoot" "https://github.com/smicallef/spiderfoot.git"
clone_tool "theHarvester" "https://github.com/laramies/theHarvester.git"
clone_tool "Mr.Holmes" "https://github.com/Lucksi/Mr.Holmes.git"
clone_tool "Sherlock" "https://github.com/sherlock-project/sherlock.git"

echo "[+] Setup complete."
echo "[+] PhoneOsint can now be run as: $VENV_PY $TOOL_DIR/phoneosint.py"
echo "[+] (or just 'phoneosint' if you installed the global command above)"
echo "[+] All Python dependencies were installed into the isolated venv at: $VENV_DIR"
echo "[+] External tools are cloned under: $OSINT_DIR"
echo "[+] Add individual tool directories to your PATH or run them from there."
echo "[+] PhoneInfoga is installed as a real binary and auto-runs with --run-tools."
echo "[+] Instagram/Snapchat/Amazon account checks (via 'ignorant') run automatically -- free, no login."
echo "[+] Mr.Holmes is fully interactive (menu-driven); run '$VENV_PY $OSINT_DIR/Mr.Holmes/MrHolmes.py' manually."
echo "[+] SpiderFoot's phone-number module (sfp_phonenumber) auto-runs with --run-tools (no server needed)."
echo "[+] Note: Infoga (m4ll0k/Infoga) is no longer available on GitHub and is skipped."
echo "[+] Use --run-tools to auto-run installed tools, --tor for live dark web search,"
echo "[+] --truecaller and --telegram for free name/registration lookups (one-time login required)."
echo "[+] --twilio-sid/--twilio-token (HLR/line-type) and --cashfree-client-id/--cashfree-client-secret (UPI name)"
echo "[+] are optional paid/KYC'd integrations -- no free public API exists for HLR or UPI name lookup."
echo "[+] --numlookupapi-key/--abstractapi-key/--veriphone-key are optional FREE-TIER phone APIs."
echo "[+] --email enables the free Gravatar lookup; --diff shows changes since the last scan;"
echo "[+] --export-csv/--export-html/--export-pdf export the report in those formats alongside JSON."
echo "[+] The 'aadhaar' and 'paypal' sections are informational-only -- no real lookup/login/scraping is performed."
