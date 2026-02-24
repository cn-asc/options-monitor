#!/usr/bin/env bash
# Build the Options Monitor into a single executable for teammates.
# Run from project root: ./build_monitor.sh
# Output: dist/Options Monitor (macOS) or dist/Options Monitor.exe (Windows)

set -e
cd "$(dirname "$0")"

echo "Installing dependencies..."
pip install -r requirements.txt -q

echo "Building executable..."
pyinstaller monitor.spec

# Optional: code-sign so macOS doesn't show "unidentified developer" on teammates' Macs.
# Requires Apple Developer ID. Set SIGNING_ID to your "Developer ID Application" identity, e.g.:
#   export SIGNING_ID="Developer ID Application: Your Name (TEAM_ID)"
# See BUILD_SIGNING.md for setup.
if [ -n "${SIGNING_ID:-}" ]; then
  echo "Signing executable with Developer ID..."
  codesign --force --deep -s "$SIGNING_ID" "dist/Options Monitor"
  echo "Signed. Recipients can open without the Gatekeeper warning."
else
  echo "Skipping code signing (set SIGNING_ID to sign; see BUILD_SIGNING.md)."
fi

echo ""
echo "Done. Executable: dist/Options Monitor (or Options Monitor.exe on Windows)"
echo ""
echo "To share with teammates:"
echo "  1. Copy the 'Options Monitor' app from the dist/ folder."
echo "  2. Copy OptionsMonitor.env.example and rename to OptionsMonitor.env."
echo "  3. Edit OptionsMonitor.env and add POLYGON_API_KEY=... and TICKERS=..."
echo "  4. Put OptionsMonitor.env in the same folder as the app."
echo "  5. They double-click the app; browser opens to the dashboard."
