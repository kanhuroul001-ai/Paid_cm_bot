# ClipsMines Recovery Guide

## Protected backup
- GitHub repository: https://github.com/kanhuroul001-ai/Paid_cm_bot
- Working branch: `clipsmines-movie`
- Snapshot backup branch (2026-11-12): `clipsmines-backup-2026-11-12`
- One-click source ZIP: https://github.com/kanhuroul001-ai/Paid_cm_bot/archive/refs/heads/clipsmines-backup-2026-11-12.zip

The GitHub repository snapshot contains the static website source, page styling/theme, movie catalogue data (including poster and download-link fields), and page files committed on the snapshot date. GitHub keeps the commit history so earlier versions can also be recovered.

## Restore on Cloudflare Pages
1. Sign in to Cloudflare and open **Workers & Pages**.
2. Choose **Create application** → **Pages** → **Connect to Git**.
3. Select the GitHub repository `kanhuroul001-ai/Paid_cm_bot`.
4. Select branch `clipsmines-movie` (or `clipsmines-backup-2026-11-12` for the saved snapshot).
5. Set framework preset to **None**; leave the build command empty; set the build output directory to `/` (repository root).
6. Deploy. Add the custom domain later if the old domain is unavailable.

## Important
- This backup preserves files and link values, not the third-party files those links point to. A link may stop working if its provider removes it.
- Only publish movie files and download links you own or are authorized to distribute. This guide does not bypass a platform suspension or legal restriction.
- Keep this GitHub repository accessible and download the ZIP periodically to a separate device/cloud drive. If GitHub itself becomes inaccessible, use that separately saved ZIP.
- Cloudflare account settings, custom-domain DNS, secrets, and ad-network account approvals are not stored in this static-site backup and may need to be configured again.
