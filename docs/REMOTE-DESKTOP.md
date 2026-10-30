# Optional: see and use your PC from elsewhere

Lazy Link doesn't need this. It runs headless, with no screen. Read this only if you also want to
**see the PC's screen** or **use it yourself** from another computer, or from your phone.

## Four free options compared

| Option | Cost | Best for | Works away from home? | Privacy note |
|---|---|---|---|---|
| **Chrome Remote Desktop** | Free for personal use, per third-party guides (Google's help page lists no price) | Easiest first setup; quick help for family | Yes, if both machines are online. The PC must be on and awake; it can't wake from sleep. | Needs a Google account. Sessions are encrypted. Protect the Google account (see Safety). |
| **RustDesk** | Free, open source (AGPL-3.0) | People who want no big-company account, or want their own server | Yes, through the public servers. Running your own server means opening ports to the internet. | Using the public servers means traffic goes through someone else's relay. Self-hosting gives you control, but it's more work and more exposure. |
| **Sunshine + Moonlight** | Free, open source (GPL-3.0). Sunshine needs an NVIDIA (or AMD/Intel) GPU that can encode video. | Gaming and low lag: play the PC's games on a phone or laptop | Possible, but Sunshine's own guide points to port forwarding or a VPN. Use Tailscale (below). | Designed for your home network. Pairing uses a PIN you type; don't share it. |
| **Windows Remote Desktop** | Free, built into Windows | Full control of your own Windows PC from another Windows computer | Only with a VPN such as Tailscale. Never port-forward it. | Only Pro, Enterprise and Education can host it. Home editions can connect out, not be connected to. |

Quick picks:

- **Just want to help a relative?** Chrome Remote Desktop.
- **Want the games to feel smooth on a phone?** Sunshine + Moonlight, over Tailscale.
- **Want to avoid big-company accounts?** RustDesk, with the public servers.
- **Have Windows Pro and a VPN?** Windows Remote Desktop.

## Reaching your computers away from home safely

**Recommended: Tailscale.** It creates a private network between your devices. Nothing is open to the
internet.

- The free Personal plan is **free forever**, with **up to 6 users** and **unlimited user devices**.
  It is for **non-commercial use only**. Source: Tailscale's pricing page.
- It is built on WireGuard, an open encryption protocol. Devices connect across routers and firewalls
  **without port forwarding**. Source: Tailscale's own product page.
- Install it on the PC and on the device you travel with. Sign in with the same account on both.
  Only devices in your account can reach each other.

**Never do this:** open port 22 (SSH), 3389 (Windows Remote Desktop) or a RustDesk port on your
router, pointed at the whole internet. Automated scanners find open ports within hours. A weak password
then becomes a break-in. Microsoft's own guide says that outside access should go through port forwarding
or a VPN, and recommends the VPN.

| Way in from outside | Verdict | Why |
|---|---|---|
| Tailscale | Recommended | No ports open on your router. |
| Port-forwarding SSH or Remote Desktop | Don't | Puts a login screen on the open internet. |
| Chrome Remote Desktop or RustDesk public servers | Acceptable | No router changes. Your account sign-in is the lock, so make it strong. |
| Sunshine + Moonlight over Tailscale | Good | Stream travels inside the private network. |

Lazy Link's own firewall rule is home-network only. Don't widen it to reach the PC from outside.
Running Tailscale on the Mac and PC would let Lazy Link reach the PC away from home too. That is not set up
in this repo and has not been tested here.

## Safety basics

- **Strong PIN or password.** Use a long password for any account that can remote in. Microsoft
  advises strong, unique passwords for every remote-access account.
- **Two-step sign-in on the account.** Turn on 2-Step Verification for the Google account (and the
  Microsoft account, if you use Windows Remote Desktop). Google's help page is
  https://support.google.com/accounts/answer/185839. Google recommends a passkey or security key over text
  codes.
- **Don't leave sessions unattended.** When you finish, sign out or lock the PC (Windows key + L).
  A remote session left open is a door left open.
- **Only turn on what you use.** Turn off Windows Remote Desktop, Sunshine or RustDesk unattended access
  when you no longer need them.
- **Keep the apps updated.** Chrome, RustDesk, Sunshine, Moonlight and Tailscale all ship security fixes.
- **Don't share pairing PINs or codes** by text, chat or screenshot.
- **Check the device list.** Remove devices you no longer own from your Tailscale and Google accounts.

## Sources

- Tailscale, *Pricing* (free Personal plan: free forever, up to 6 users, unlimited user devices,
  non-commercial): https://tailscale.com/pricing
- Tailscale, *What is Tailscale* (WireGuard; works across NAT and firewalls without port forwarding):
  https://tailscale.com/kb/1151/what-is-tailscale
- Google, *Chrome Remote Desktop help* (Chrome required; internet required; supported systems):
  https://support.google.com/chrome/answer/1649523
- Google, *Turn on 2-Step Verification*: https://support.google.com/accounts/answer/185839
- Microsoft Learn, *Enable Remote Desktop on your PC* (Pro, Enterprise and Education can host; Home can't;
  outside access via port forwarding or VPN; strong passwords): https://learn.microsoft.com/en-us/windows-server/remote/remote-desktop-services/remotepc/remote-desktop-allow-access
- RustDesk, GitHub repository (open source, AGPL-3.0, self-hosting): https://github.com/rustdesk/rustdesk
- RustDesk, *Self-host* documentation (server ports; examples suggest opening all ports): https://rustdesk.com/docs/en/self-host/
- Sunshine, GitHub repository (GPL-3.0; NVIDIA NVENC requirement): https://github.com/LizardByte/Sunshine
- Moonlight, *home page* (free, open source GPLv3; port forwarding, ZeroTier or Tailscale for remote play):
  https://moonlight-stream.org/
