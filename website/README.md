# MCBOT Website – Deployment Guide

This folder contains a fully **static, dependency-free** one-page website that lets
visitors add the MCBOT node to their MeshCore contacts by scanning a QR code.

---

## Files

| File | Purpose |
|---|---|
| `index.html` | The styled landing page (sticker-blob / 70s lava-lamp theme) |
| `qr.png` | QR code image linking to `http://meshtastic.local/#/connect?add=true` (now included by default) |
| `README.md` | This file |

---

## Deploying to cPanel (Shared Hosting)

### Upload to the website root

1. Log in to your cPanel account and open **File Manager**.
2. Navigate to `public_html/`.
3. Upload **both** `index.html` and `qr.png` into `public_html/`.
4. Visit `https://yourdomain.com/` – the page should appear immediately.

### Upload to a subfolder (e.g. `https://yourdomain.com/mcbot/`)

1. In File Manager, create a new folder inside `public_html/`, for example `mcbot/`.
2. Upload `index.html` (and `qr.png` once you have it) into `public_html/mcbot/`.
3. Visit `https://yourdomain.com/mcbot/`.

> **Tip:** You can also use cPanel's **FTP / SFTP** credentials with any FTP client
> (FileZilla, Cyberduck, etc.) instead of the browser-based File Manager.

---

## Adding the QR Code

A `qr.png` is now included in this folder by default. It encodes the URL
`http://meshtastic.local/#/connect?add=true` so mobile users can also tap a
link directly when they cannot scan the QR code.

The page will show a styled placeholder if `qr.png` is not present.
To replace it with your own QR code:

1. **Generate your QR code** from the MeshCore app or companion firmware:
   - In the MeshCore app go to your node's profile and choose **Share / Export QR**.
   - Save or export the image.
2. **Rename the image** to exactly `qr.png`.
3. **Upload `qr.png`** to the same directory as `index.html`
   (e.g. `public_html/` or `public_html/mcbot/`).
4. Refresh the page – the real QR code will appear in place of the placeholder.

> The QR code should be scanned with the **MeshCore mobile app**
> (`Contacts → Add Contact → Scan QR`).  Mobile users who cannot scan the
> screen can tap the **"Tap to add this contact"** link shown below the QR image.

---

## No Build Step Required

Everything is plain HTML/CSS/JS.  There is no build tool, no npm, no bundler.
Just copy the files and they work.

---

## Customisation Tips

- **Colours** – all colours are defined as CSS custom properties at the top of
  `index.html`'s `<style>` block (`:root { --blob-orange: …; … }`).  Change
  them to match your branding.
- **Heading text** – edit the `<h1>` and `<p class="tagline">` elements in
  `index.html`.
- **Steps** – the `<ol class="steps">` list can be edited to reflect any local
  instructions or node name.
