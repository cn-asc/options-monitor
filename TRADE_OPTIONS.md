# Trade Options (browser app only)

In the Options Monitor **browser app**, each row has a red **Trade Options** button. Clicking it creates **Gmail drafts** (one per client) for that ticker and opens your Gmail Drafts folder.

## One-time setup

### 1. Gmail compose permission

Draft creation needs Gmail **compose** scope. If you only set up Gmail for sending the daily email before, re-run:

```bash
python gmail_auth.py
```

Log in and approve. Then your `token.json` will have both send and compose; the Trade Options button will work.

### 2. Trade config (clients + email templates)

Copy the example config and add your clients and email text:

```bash
cp trade_config.json.example trade_config.json
```

Edit **`trade_config.json`**:

- **`clients`**: list of `{ "email": "client@example.com", "name": "Client Name" }`. One draft is created per client.
- **`subject_template`**: subject line. Use `{ticker}` and `{client_name}` as placeholders.
- **`body_template`**: email body (plain text). Same placeholders: `{ticker}`, `{client_name}`.

Put **`trade_config.json`** in the same folder as the app (when running from source: project root; when packaged: same folder as the Options Monitor executable).

### 3. SEND_FROM

Your sending address must be set in `.env` or **OptionsMonitor.env** as `SEND_FROM=you@example.com` (same as for the daily email).

## Flow

1. Open the Options Monitor in the browser.
2. Click **Trade Options** for a ticker (e.g. MSTR).
3. The app creates one draft per client in your Gmail, then opens Gmail Drafts.
4. You review/edit and send from Gmail as usual.

When you have the final client list and email body text, update **`trade_config.json`** with the real addresses and templates.
