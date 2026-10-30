# Make the PC wake from the network

Lazy Link wakes a sleeping gaming PC by sending it a "magic packet" over your home network.
Most PCs ship with that listening switched off, so you turn it on once, in the BIOS and in Windows.

## Why this is needed

The PC's network card must stay powered and listen while the PC sleeps.
Two switches control that: one in the BIOS (the PC's own setup screen) and one in Windows.

## Before you start (about 5 minutes)

- Plug the PC into the router with a **network cable**. Wi-Fi usually can't wake a PC (see below).
- Keep the PC plugged in at the wall, and switch on the power strip.
- Have a keyboard and screen attached to the PC.
- Find the make and model: press **Windows + R**, type `msinfo32`, press Enter.
  Look at *System Manufacturer* and *System Model*. The sticker on the case also works.

## Step 1: Open the BIOS (UEFI setup)

Restart the PC. Tap the key below once a second, starting as you press the power button,
until a setup screen appears. Many PCs flash the right key on the first screen.

| Maker | Setup key | Notes |
|---|---|---|
| ASUS | Del (boards) or F2 (pre-built PCs) | ASUS's own guide uses Del |
| MSI | Del | Some boards use F2 |
| Gigabyte / AORUS | Del or F2 | |
| ASRock | F2 or Del | |
| Dell / Alienware | F2 | F12 is the one-time boot menu, not setup |
| HP / OMEN | Esc first, then F10 | F10 opens setup |
| Lenovo / Legion | F1 (desktops), F2 or Fn+F2 (laptops) | ThinkPads: Enter, then F1 |
| Acer / Predator | F2 or Del | |

**Windows route (if the key is too fast to hit):**

1. Settings > System > Recovery.
2. Next to *Advanced startup*, click **Restart now**.
3. Choose Troubleshoot > Advanced options > **UEFI Firmware Settings** > Restart.

Use this route if Fast Startup (see Step 3) stops the keys from working.

## Step 2: Change the BIOS settings

Setting names change between BIOS versions. Look in the *Advanced*, *Power*, *Boot* and
*Configuration* menus. ASUS keeps these under **Advanced > APM Configuration**.

| Look for | Set to | Why |
|---|---|---|
| Power On By PCI-E (ASUS) | Enabled | Lets the network card wake the PC |
| Wake on LAN, Resume by PCI-E Device, PCIE Devices Power On | Enabled | Same idea, other makers' names |
| ErP Ready, EuP 2013, ErP/EuP Control | **Disabled** | Stops the board cutting power to the network card while asleep |
| Deep Sleep Control (Dell, and some others) | **Disabled** | Dell says it must be off for Wake on LAN |

Dell's guide finds Wake on LAN under the **System Management** tab, in its *Wake on LAN* section.

Then save and leave:

1. Press **F10**.
2. Choose **Yes** or **OK**.
3. The PC restarts.

Change only these settings. Leave overclocking and voltage options alone.

## Step 3: Set up Windows

**a) Turn off Fast Startup.** Fast Startup makes "shut down" a half-sleep, which blocks wake from shutdown.

1. Open Control Panel > Power Options > *Choose what the power buttons do*.
2. Click *Change settings that are currently unavailable*.
3. Untick **Turn on fast startup**, then click *Save changes*. This needs an administrator account.

**b) Let the network card wake the PC.**

1. Right-click the Start button > **Device Manager** > *Network adapters*.
2. Double-click your wired adapter (Realtek, Intel or similar). Do not use the Wi-Fi adapter.
3. On the **Power Management** tab, tick **Allow this device to wake the computer**
   and **Only allow a magic packet to wake the computer**.
4. On the **Advanced** tab, if you see **Wake on Magic Packet**, set it to **Enabled**.
   Intel cards may call it *PME*; set that to Enabled too.
5. Click OK.

**Wi-Fi:** Most Wi-Fi cards can't wake a sleeping PC. Use the cable. If you must use Wi-Fi,
test it first. Do not rely on it.

## Step 4: Test it

1. Put the PC to sleep: Start > Power > Sleep.
2. From the Mac, run `./mac/pcjob wake`.
3. Wait about 30 seconds, then run `./mac/pcjob ping`. It should answer.

Test wake from sleep first. Full shutdown is harder: Dell says which sleep states can be woken
depends on the model, so check your model's manual. Many boards wake from shutdown if the BIOS
setting is on and Fast Startup is off.

## Can this be done remotely or from a USB stick?

Short answer: not on a normal gaming PC. Do it once by hand. It takes about 5 minutes.

- **Remote BIOS changes:** A normal consumer PC can't have its BIOS changed over the network.
  Business PCs with Intel vPro (AMT) can be powered on and off over the network. Gaming PCs
  rarely have it. Check the PC's spec sheet.
- **KVM-over-IP gadget:** A small box (PiKVM or JetKVM) plugs into the PC's video and USB ports.
  You then open a web page on your network to see the screen and press keys, including in the
  BIOS. JetKVM's newer Mini model is reported at about $39; the original JetKVM at about $89 to
  $103; PiKVM is a kit built around a Raspberry Pi. Prices change, so check the maker's site.
  This is overkill for a one-time change.
- **BIOS profiles on a USB stick:** ASUS boards can save and load settings to a USB stick:
  *Tool > ASUS User Profile > Load/Save Profile from/to USB Drive*. The file ends in `.CMO`.
  It only restores to the **same motherboard model**. Saving a profile is a good backup, but
  it doesn't do the setup for you. MSI and Gigabyte boards have similar features; check the manual.
- **Updating the BIOS from USB:** Possible, but a failed update can make the board unusable.
  Don't do it for this. Lazy Link doesn't need a new BIOS.

## Troubleshooting

| Problem | Check |
|---|---|
| PC never wakes from sleep | Network cable (not Wi-Fi). ErP is Disabled. Wake on LAN / PCI-E is Enabled. |
| | The adapter's Power Management boxes are ticked (Step 3b). |
| | The Mac is on the same network (not a guest Wi-Fi). Magic packets stay on your local network. |
| | Power strip and wall socket are switched on. |
| | Run `powercfg /a` in Command Prompt. If it lists only *Modern Standby*, not *S3*, the PC may not wake that way. |
| Wakes from sleep but not from shutdown | Fast Startup is off. Shutdown wake is not supported on every board. Sleep is the reliable choice. |
| Worked before, stopped | A BIOS update or a dead CMOS battery can reset the BIOS. Check the settings again. |
| Settings won't save | Press F10 and choose Yes. Some BIOS screens need the PC to restart before the change counts. |
| Wi-Fi card doesn't wake the PC | Expected. Use a cable. |

## Sources

- ASUS, *How to set and enable WOL in BIOS* (Power On By PCI-E, APM Configuration, F10): https://www.asus.com/support/faq/1045950/
- ASUS, *How to store and load BIOS configuration files* (.CMO, same model only): https://www.asus.com/support/faq/1053878
- ASUS commercial PCs, *How to enable Wake on LAN* (ErP under APM): https://servers.asus.com/support/faq/detail/1048459
- Dell, *What is Wake-on-LAN: Troubleshooting Guide and Best Practices* (F2, System Management tab, Deep Sleep, Energy-Efficient Ethernet, Fast Startup): https://www.dell.com/support/kbdoc/en-us/000129137/wake-on-lan-wol-troubleshooting-best-practices
- Tom's Hardware, *How to Enter the BIOS on Any PC: Access Keys by Manufacturer* (search summary; key table checked across several sources): https://www.tomshardware.com/reviews/bios-keys-to-access-your-firmware%2C5732.html
- Windows 11 adapter steps (Power Management and Advanced tab), third-party guide: https://www.elevenforum.com/t/enable-or-disable-wake-on-lan-wol-in-windows-11.11872
- MSI, *Wake On LAN (WOL)* FAQ (EuP / ERP Ready advice; page blocked our reader, seen in search): https://forum-en.msi.com/faq/article/printer/wake-on-lan-wol
- Intel, *AMT Developer Guide: Remote Power Management* (vPro power on and off): https://intel.com/content/www/us/en/docs/active-management-technology/developer-guide/2021/remote-power-management.html
- JetKVM Mini and original prices, press and retailer reports (conflicting; check the maker): https://borncity.com/news/jetkvm-mini-open-source-kvm-fuer-39-dollar-ab-oktober/
