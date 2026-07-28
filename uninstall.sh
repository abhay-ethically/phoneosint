#!/usr/bin/env bash
# Uninstall everything install.sh set up: the global 'phoneosint' command,
# the PhoneInfoga global binary (if linked), the local venv, and
# (optionally) local config/history and cloned external OSINT tools.
set -u

TOOL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[*] Removing global 'phoneosint' command..."
if [ -L /usr/local/bin/phoneosint ] || [ -f /usr/local/bin/phoneosint ]; then
    sudo rm -f /usr/local/bin/phoneosint
    echo "[+] Removed /usr/local/bin/phoneosint"
else
    echo "[*] /usr/local/bin/phoneosint not found, skipping."
fi

if [ -L /usr/local/bin/phoneinfoga ] || [ -f /usr/local/bin/phoneinfoga ]; then
    read -rp "Remove global 'phoneinfoga' command too (needs sudo)? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        sudo rm -f /usr/local/bin/phoneinfoga
        echo "[+] Removed /usr/local/bin/phoneinfoga"
    fi
fi

VENV_DIR="$TOOL_DIR/venv"
if [ -d "$VENV_DIR" ]; then
    read -rp "Delete the isolated venv at $VENV_DIR? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        rm -rf "$VENV_DIR"
        echo "[+] Removed $VENV_DIR"
    else
        echo "[*] Kept $VENV_DIR"
    fi
fi

CONFIG_DIR="$HOME/.phoneosint"
if [ -d "$CONFIG_DIR" ]; then
    read -rp "Delete local config/history at $CONFIG_DIR (saved API keys, scan history)? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        rm -rf "$CONFIG_DIR"
        echo "[+] Removed $CONFIG_DIR"
    else
        echo "[*] Kept $CONFIG_DIR"
    fi
fi

TOOLS_DIR="$HOME/.phoneosint-tools"
if [ -d "$TOOLS_DIR" ]; then
    read -rp "Delete cloned external tools at $TOOLS_DIR (Maigret, Sherlock, Infoga, SpiderFoot, theHarvester, Mr.Holmes, PhoneInfoga binary)? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        rm -rf "$TOOLS_DIR"
        echo "[+] Removed $TOOLS_DIR"
    else
        echo "[*] Kept $TOOLS_DIR"
    fi
fi

echo "[+] Uninstall complete."
echo "[+] The repo/source directory itself was left untouched -- delete it manually if desired."
