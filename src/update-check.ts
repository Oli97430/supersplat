// ─────────────────────────────────────────────────────────────────────────────
// Auto-update check: queries the GitHub releases API on boot, compares the
// latest release tag against the local package.json version, and shows a
// dismissible banner if a newer build is available.
//
// CORS: api.github.com responds with `Access-Control-Allow-Origin: *` so a
// browser fetch from any origin works. No backend involvement needed.
//
// Dismissal: the banner sets `localStorage[OCS_DISMISSED_VERSION] = <tag>`,
// suppressing further nags for that specific tag. Subsequent releases prompt
// again automatically.
// ─────────────────────────────────────────────────────────────────────────────

import { version as appVersion } from '../package.json';

const RELEASES_API = 'https://api.github.com/repos/Oli97430/supersplat/releases/latest';
const DISMISS_KEY  = 'OCS_DISMISSED_VERSION';
const STALE_AFTER  = 24 * 60 * 60 * 1000;     // re-check every 24h

// Parse a "vX.Y.Z[-train]" or "X.Y.Z" tag into a comparable [major,minor,patch].
const parseSemver = (tag: string): [number, number, number] | null => {
    const m = String(tag).trim().match(/v?(\d+)\.(\d+)\.(\d+)/);
    if (!m) return null;
    return [Number(m[1]), Number(m[2]), Number(m[3])];
};

const isNewer = (remote: string, local: string): boolean => {
    const r = parseSemver(remote);
    const l = parseSemver(local);
    if (!r || !l) return false;
    for (let i = 0; i < 3; i++) {
        if (r[i] > l[i]) return true;
        if (r[i] < l[i]) return false;
    }
    return false;
};

const renderBanner = (tag: string, htmlUrl: string) => {
    if (document.getElementById('ocs-update-banner')) return;

    const bar = document.createElement('div');
    bar.id = 'ocs-update-banner';
    bar.setAttribute('role', 'status');
    bar.style.cssText = `
        position: fixed;
        top: 0; left: 50%;
        transform: translateX(-50%);
        z-index: 10000;
        margin: 14px 0 0;
        padding: 10px 16px 10px 18px;
        background: #0a0805;
        color: #f4ede0;
        font-family: 'JetBrains Mono', ui-monospace, monospace;
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0.04em;
        border: 1px solid #ff8a3d;
        border-left: 3px solid #ff8a3d;
        box-shadow: 0 6px 24px rgba(0, 0, 0, 0.35);
        display: inline-flex;
        align-items: center;
        gap: 14px;
        animation: ocs-update-in 0.5s cubic-bezier(0.2, 0.7, 0.3, 1);
    `;

    // Inject keyframes once
    if (!document.getElementById('ocs-update-style')) {
        const style = document.createElement('style');
        style.id = 'ocs-update-style';
        style.textContent = `
            @keyframes ocs-update-in {
                from { opacity: 0; transform: translate(-50%, -10px); }
                to   { opacity: 1; transform: translate(-50%, 0); }
            }
            #ocs-update-banner a:hover { background: #ff8a3d; color: #0a0805; }
        `;
        document.head.appendChild(style);
    }

    const dot = document.createElement('span');
    dot.style.cssText = 'display:inline-block;width:7px;height:7px;border-radius:50%;background:#ff8a3d;box-shadow:0 0 8px rgba(255,138,61,0.7);';
    bar.appendChild(dot);

    // Build the message line using DOM methods only (no innerHTML).
    const msg = document.createElement('span');
    msg.appendChild(document.createTextNode('Update available — '));
    const tagEl = document.createElement('strong');
    tagEl.textContent = tag;
    msg.appendChild(tagEl);
    msg.appendChild(document.createTextNode(` (you have v${appVersion})`));
    bar.appendChild(msg);

    const view = document.createElement('a');
    view.href = htmlUrl;
    view.target = '_blank';
    view.rel = 'noopener noreferrer';
    view.textContent = 'VIEW RELEASE →';
    view.style.cssText = `
        color: #ff8a3d;
        text-decoration: none;
        font-weight: 800;
        letter-spacing: 0.15em;
        padding: 5px 10px;
        border: 1px solid #ff8a3d;
        transition: background 0.15s, color 0.15s;
    `;
    bar.appendChild(view);

    const dismiss = document.createElement('button');
    dismiss.textContent = '×';
    dismiss.title = 'Dismiss';
    dismiss.style.cssText = `
        background: none;
        border: none;
        color: #f4ede0;
        font-size: 20px;
        line-height: 1;
        padding: 0 4px;
        cursor: pointer;
        opacity: 0.6;
    `;
    dismiss.onmouseenter = () => { dismiss.style.opacity = '1'; };
    dismiss.onmouseleave = () => { dismiss.style.opacity = '0.6'; };
    dismiss.onclick = () => {
        try { localStorage.setItem(DISMISS_KEY, tag); } catch { /* ignore */ }
        bar.remove();
    };
    bar.appendChild(dismiss);

    document.body.appendChild(bar);
};

const checkForUpdates = async (): Promise<void> => {
    // Only run in browsers; skip when launched in test/headless contexts that
    // don't have window.localStorage.
    if (typeof window === 'undefined' || !('fetch' in window)) return;

    // Rate-limit: store the last check time in localStorage; skip if checked
    // within STALE_AFTER ms.
    try {
        const lastCheck = Number(localStorage.getItem('OCS_LAST_UPDATE_CHECK') || '0');
        if (Date.now() - lastCheck < STALE_AFTER) return;
    } catch { /* localStorage might be unavailable */ }

    try {
        const r = await fetch(RELEASES_API, {
            method: 'GET',
            headers: { 'Accept': 'application/vnd.github.v3+json' },
            signal: AbortSignal.timeout(6000)
        });
        if (!r.ok) return;
        const data = await r.json();
        const tag  = data.tag_name as string;
        const url  = data.html_url as string;

        try { localStorage.setItem('OCS_LAST_UPDATE_CHECK', String(Date.now())); } catch { /* ignore */ }

        if (!tag || !url) return;

        // Skip if user already dismissed this exact tag.
        try {
            if (localStorage.getItem(DISMISS_KEY) === tag) return;
        } catch { /* ignore */ }

        if (isNewer(tag, appVersion)) {
            // Small delay so the editor's main paint has settled.
            setTimeout(() => renderBanner(tag, url), 1200);
        }
    } catch {
        // Silent: offline, GitHub down, rate limited, etc. -- no user-facing error.
    }
};

export { checkForUpdates };
