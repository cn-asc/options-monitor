Options Monitor — For Your Team
=================================

This app shows the options dashboard (IV, RV, headlines) in your browser. Double-click to open it.

SETUP (one-time)
----------------
1. Put this file in the same folder as "Options Monitor" (the app).
2. Copy "OptionsMonitor.env.example" to "OptionsMonitor.env" (same folder).
3. Open OptionsMonitor.env in a text editor and add:
   - POLYGON_API_KEY=your_key_here   (get this from your team admin)
   - TICKERS=AAPL,MSFT,...           (or use the list your team uses)
4. Save and close.

USING THE APP
-------------
- Double-click "Options Monitor".
- A browser window will open with the dashboard. (A small black window may also open — leave it open while you use the dashboard.)
- Click "Refresh" on the page to load the latest data.
- When done, close the browser tab. You can close the small black window to fully exit.

TROUBLESHOOTING
---------------
- "Cannot be opened because it is from an unidentified developer" (Mac) — Right-click the app → Open, then click Open. You only need to do this once.
- "POLYGON_API_KEY is missing" — Add OptionsMonitor.env with POLYGON_API_KEY=... in the same folder as the app.
- Page won't load — Wait 10–20 seconds after opening; the first load can be slow.
- "Address already in use" — Another copy of the app is already running. Close it or use the tab you already opened.

Need help? Contact your team admin.
