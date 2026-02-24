# Code signing the Options Monitor (macOS)

If you distribute the app to teammates, macOS will show **"cannot be opened because it is from an unidentified developer"** unless the app is **code-signed** with an Apple Developer ID.

## Option A: No signing (current behavior)

Teammates can still run the app: **right-click the app → Open**, then click **Open** in the dialog. They only need to do this once per copy. This is fine for internal use.

## Option B: Sign the app (no Gatekeeper warning)

You need an **Apple Developer Program** membership ($99/year) and a **Developer ID Application** certificate.

### 1. Enroll and create a certificate

1. Go to [developer.apple.com](https://developer.apple.com) and enroll in the Apple Developer Program.
2. In [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/certificates/list), create a **Developer ID Application** certificate (for distribution outside the App Store).
3. Download and double-click the certificate to add it to your Keychain.

### 2. Find your signing identity

In Terminal:

```bash
security find-identity -v -p codesigning
```

Look for a line like: **"Developer ID Application: Your Name (XXXXXXXXXX)"**. That full string is your signing identity.

### 3. Sign when you build

```bash
export SIGNING_ID="Developer ID Application: Your Name (XXXXXXXXXX)"
./build_monitor.sh
```

The script will run `codesign` on the built executable. Share the **signed** app from `dist/`; recipients can double-click it without the unidentified-developer warning.

### 4. (Optional) Notarize for full trust

For maximum compatibility (especially on newer macOS), you can **notarize** the app after signing so Gatekeeper fully trusts it:

1. Create an **App-specific password** for your Apple ID at [appleid.apple.com](https://appleid.apple.com).
2. After building and signing:

   ```bash
   xcrun notarytool submit "dist/Options Monitor" \
     --apple-id "your@email.com" \
     --password "app-specific-password" \
     --team-id "YOUR_TEAM_ID" \
     --wait
   xcrun stapler staple "dist/Options Monitor"
   ```

Then distribute the notarized app. Users won’t see any security prompt.

---

**Summary:** Set `SIGNING_ID` to your Developer ID Application identity and run `./build_monitor.sh`; the script will sign the app so teammates can open it normally.
