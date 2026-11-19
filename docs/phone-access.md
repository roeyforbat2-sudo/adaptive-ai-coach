# Opening the coach on your phone (private, via Tailscale)

The dashboard keeps running on your PC at `localhost:8501`. Tailscale gives your PC and
phone a private network, and `tailscale serve` publishes the dashboard on an HTTPS address
that **only your own signed-in devices** can open. It is not public, nothing goes to the
cloud, and it works on Wi-Fi or mobile data. The PC must be on with the dashboard running.

## One-time setup

1. **Passcode.** In PowerShell on the PC:
   `cd C:\dev\adaptive-ai-coach; .venv\Scripts\python.exe -m coach.set_passcode`
   (input is hidden; only a hash is stored in Windows Credential Manager).
2. **Tailscale on the PC.** Install it from https://tailscale.com/download and sign in.
3. **Tailscale on the phone.** Install the Tailscale app from the App Store / Google Play and
   sign in with the **same** account.
4. **Turn on HTTPS for your tailnet** (one click, the first time `tailscale serve` asks, or in the
   admin console under DNS → HTTPS Certificates).
5. **Publish the dashboard privately** (PowerShell on the PC):
   `tailscale serve --bg 8501`
   It prints your private address, like `https://<pc-name>.<tailnet>.ts.net`.
6. On the phone, open that address, enter the passcode, then choose **Add to Home Screen**
   so it opens like an app.

## Everyday use

- The PC must be on and the dashboard running (`.venv\Scripts\python.exe -m streamlit run src\coach\ui\app.py`).
- Phone: Tailscale app connected, then open the home-screen icon.
- Stop sharing: `tailscale serve --https=443 off` (or `tailscale serve reset`).

Never use `tailscale funnel` for this app: Funnel makes it reachable from the public internet.
