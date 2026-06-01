import { Container, Element } from '@playcanvas/pcui';

import { Events } from '../events';

// ── Configuration ────────────────────────────────────────────────────────────
const LS_KEY_URL = 'supersplat-backend-url';
const DEFAULT_URL = 'http://127.0.0.1:8000';
const APP_VERSION = '2.27';

const getBackendUrl = () => localStorage.getItem(LS_KEY_URL) || DEFAULT_URL;
const setBackendUrl = (u: string) => {
    if (u) localStorage.setItem(LS_KEY_URL, u.replace(/\/$/, ''));
    else localStorage.removeItem(LS_KEY_URL);
};

const VIDEO_EXTS = ['.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'];
const IMAGE_EXTS = ['.jpg', '.jpeg', '.png'];

const isVideoFile = (name: string) => VIDEO_EXTS.some(e => name.toLowerCase().endsWith(e));
const isImageFile = (name: string) => IMAGE_EXTS.some(e => name.toLowerCase().endsWith(e));

const STAGE_ORDER = ['preparing', 'extracting', 'colmap', 'training', 'exporting', 'done'];
const STAGE_LABELS: Record<string, string> = {
    queued: 'QUEUED',
    preparing: 'PREPARE',
    extracting: 'EXTRACT',
    colmap: 'COLMAP',
    training: 'TRAIN',
    exporting: 'EXPORT',
    done: 'DONE',
    failed: 'FAIL',
    cancelled: 'STOP'
};

// ── Friendly error mapping ───────────────────────────────────────────────────
// Translate technical stack traces / subprocess errors into actionable text.
type ErrorHint = { title: string; advice: string };

const ERROR_PATTERNS: { match: RegExp; hint: ErrorHint }[] = [
    {
        match: /CUDA out of memory|OutOfMemoryError|cuda.+OOM/i,
        hint: {
            title: 'GPU out of memory',
            advice: 'Try the "Preview" preset (5 k iterations) or pick a shorter video. RTX 3060-class GPUs may struggle past 25 k iterations on high-res scenes.'
        }
    },
    {
        match: /transforms\.json|registered only \d+ camera poses?|need \d+\+/i,
        hint: {
            title: "COLMAP couldn't recover camera positions",
            advice: 'Capture with slower camera movement and 60–70 % frame overlap. Try the "vocab_tree" matcher for unordered photos. Bright, evenly-lit scenes work best.'
        }
    },
    {
        match: /Only \d+ frames? extracted|need at least \d+ frames?/i,
        hint: {
            title: 'Not enough usable frames',
            advice: 'Use a video at least 60 seconds long, or increase the FPS in the Parameters section to 3–5 fps.'
        }
    },
    {
        match: /ffmpeg failed|moov atom not found|Invalid data found/i,
        hint: {
            title: 'Video file could not be decoded',
            advice: 'The source file may be corrupted or in an unusual container. Try re-encoding it to .mp4 with H.264 first (HandBrake, VLC, or any video editor).'
        }
    },
    {
        match: /rate limit|429/i,
        hint: {
            title: 'Too many jobs queued recently',
            advice: 'The backend caps jobs per hour to keep the GPU available. Wait a few minutes, then dispatch again.'
        }
    },
    {
        match: /only [\d.]+ GB free|disk space|insufficient.*space/i,
        hint: {
            title: 'Not enough disk space',
            advice: 'A typical job needs 5–10 GB. Clean older jobs from the Recent Jobs list (× button) or free up space on the install drive.'
        }
    },
    {
        match: /exceeds \d+ MB|413|file too large/i,
        hint: {
            title: 'Upload file too large',
            advice: 'The per-file upload cap is 4 GB by default. Re-encode the video at a lower bitrate, or set OCS_MAX_UPLOAD_MB in the backend .env to raise the limit.'
        }
    },
    {
        match: /fetch|Failed to fetch|NetworkError|ECONNREFUSED|connection refused/i,
        hint: {
            title: 'Backend not reachable',
            advice: 'The FastAPI server stopped responding. Re-launch OneClickSPLAT.cmd, or check Windows Firewall isn\'t blocking port 8000.'
        }
    },
    {
        match: /401|unauthorized|missing bearer token/i,
        hint: {
            title: 'Authentication required',
            advice: 'The backend was started with OCS_AUTH_TOKEN set. Configure the matching token in the SETTINGS row of the offline panel.'
        }
    }
];

const friendlyError = (rawMessage: string): ErrorHint | null => {
    const msg = String(rawMessage ?? '');
    for (const { match, hint } of ERROR_PATTERNS) {
        if (match.test(msg)) return hint;
    }
    return null;
};

// ── Formatters ───────────────────────────────────────────────────────────────
const pad2 = (n: number) => String(n).padStart(2, '0');
const fmtBytes = (n: number) => {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
};
const fmtElapsed = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `t+ ${pad2(m)}:${pad2(s)}`;
};
const fmtAgo = (epoch: number) => {
    const sec = Date.now() / 1000 - epoch;
    if (sec < 60) return `${Math.floor(sec)}s ago`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
};

// ── Auto-FPS: aim for ~150 frames from a video duration ──────────────────────
const autoFps = (durationSec: number): number => {
    const target = 150;
    const raw = target / Math.max(durationSec, 1);
    const options = [1, 2, 3, 5];
    return options.reduce((prev, cur) => (Math.abs(cur - raw) < Math.abs(prev - raw) ? cur : prev));
};

// ── Capture preset definitions (mirror server-side CAPTURE_PRESETS) ──────────
type Preset = { id: string; label: string; description: string; matcher: string; max_iters: number; extract_fps: number; };
const PRESETS: Preset[] = [
    { id: 'object', label: 'OBJECT', description: 'sculpture, product · sequential · 20k', matcher: 'sequential', max_iters: 20000, extract_fps: 3 },
    { id: 'indoor', label: 'INDOOR', description: 'room, café, museum · sequential · 30k', matcher: 'sequential', max_iters: 30000, extract_fps: 2 },
    { id: 'outdoor', label: 'OUTDOOR', description: 'building, monument · vocab_tree · 40k', matcher: 'vocab_tree', max_iters: 40000, extract_fps: 2 },
    { id: 'drone', label: 'DRONE', description: 'aerial orbit · subject-centric · vocab_tree · 30k', matcher: 'vocab_tree', max_iters: 30000, extract_fps: 3 },
    { id: 'portrait', label: 'PORTRAIT', description: 'person, full-body · sequential · 25k', matcher: 'sequential', max_iters: 25000, extract_fps: 3 },
    { id: 'preview', label: 'PREVIEW', description: 'quick 5k iters · fast check first', matcher: 'sequential', max_iters: 5000, extract_fps: 2 },
    { id: 'custom', label: 'CUSTOM', description: 'manual control of every parameter', matcher: 'sequential', max_iters: 30000, extract_fps: 2 }
];

// ── Cost estimator ───────────────────────────────────────────────────────────
const estimateJob = (
    files: File[],
    videoMeta: { duration: number; sizeMB: number } | null,
    fps: number,
    iters: number
): { frames: number; uploadMB: number; diskMB: number; trainMin: number } => {
    const uploadMB = files.reduce((a, f) => a + f.size, 0) / 1024 / 1024;
    const frames = videoMeta ?
        Math.round(videoMeta.duration * fps) :
        files.length;
    // ~3 MB/frame raw + ~5 MB COLMAP + ~50 MB ckpt + ~80 MB PLY
    const diskMB = Math.round(frames * 3 + 50 + 80 + uploadMB);
    // ~0.4 s/iter on RTX 3090 + ~10 s/frame for COLMAP
    const trainSec = (iters * 0.05) + (frames * 8);
    return { frames, uploadMB: Math.round(uploadMB), diskMB, trainMin: Math.round(trainSec / 60) };
};

// ── Static HTML template — NO user-interpolation, parsed via DOMParser ───────
const TEMPLATE = `<!DOCTYPE html><body><div class="tk-console" data-state="idle">

    <div class="tk-grid"></div>
    <div class="tk-noise"></div>

    <!-- Offline overlay (shown when backend is unreachable) -->
    <div class="tk-offline" hidden data-tk-offline>
        <div class="tk-offline-inner">
            <span class="tk-offline-icon">&#9888;</span>
            <span class="tk-offline-title">BACKEND OFFLINE</span>
            <p class="tk-offline-desc">Start the training server, then retry:</p>
            <code class="tk-offline-cmd">cd server &amp;&amp; uvicorn main:app --reload</code>
            <div class="tk-offline-url-row">
                <label>SERVER URL</label>
                <input class="tk-offline-url" data-tk-url type="text" spellcheck="false">
            </div>
            <button class="tk-btn tk-btn--ghost tk-offline-retry" data-tk-retry type="button">&#8635; RETRY CONNECTION</button>
        </div>
    </div>

    <div class="tk-frame">
        <span class="tk-corner tk-tl">+</span>
        <span class="tk-corner tk-tr">+</span>
        <span class="tk-corner tk-bl">+</span>
        <span class="tk-corner tk-br">+</span>

        <header class="tk-head">
            <div class="tk-title">
                <span class="tk-bracket">[</span>
                <h2>Photogrammetric Reconstruction Console</h2>
                <span class="tk-bracket">]</span>
            </div>
            <dl class="tk-meta">
                <div><dt>JOB</dt><dd data-tk-jobid>&mdash;</dd></div>
                <div><dt>UTC</dt><dd data-tk-utc>&mdash;</dd></div>
                <div><dt>GPU</dt><dd data-tk-gpu>detecting&hellip;</dd></div>
                <div><dt>VER</dt><dd data-tk-ver>SS&middot;? / GS&middot;1.4</dd></div>
            </dl>
        </header>

        <div class="tk-rule"><span>SOURCE</span></div>

        <section class="tk-section">
            <span class="tk-step">01</span>
            <div class="tk-step-body">
                <p class="tk-help">Drop a video clip or a set of stills &mdash; the rig will recover poses and densify.</p>
                <button class="tk-drop" data-tk-pick type="button">
                    <span class="tk-drop-corner tk-tl">&#9484;</span>
                    <span class="tk-drop-corner tk-tr">&#9488;</span>
                    <span class="tk-drop-corner tk-bl">&#9492;</span>
                    <span class="tk-drop-corner tk-br">&#9496;</span>
                    <span class="tk-drop-icon">&#9636;</span>
                    <span class="tk-drop-text">SELECT VIDEO OR PHOTOS</span>
                    <span class="tk-drop-sub">.mp4 .mov .mkv &middot; .jpg .png</span>
                </button>
                <div class="tk-source" hidden data-tk-source>
                    <span class="tk-source-kind" data-tk-kind>&mdash;</span>
                    <span class="tk-source-name" data-tk-name>no input</span>
                    <span class="tk-source-bytes" data-tk-bytes>0 B</span>
                    <button class="tk-source-clear" data-tk-clear type="button">&times;</button>
                </div>

                <div class="tk-hints" hidden data-tk-hints>
                    <div class="tk-hints-row">
                        <span class="tk-hints-label">CAPTURE&middot;ANALYSIS</span>
                        <span class="tk-hints-summary" data-tk-hints-summary>&mdash;</span>
                    </div>
                    <ul class="tk-hints-list" data-tk-hints-list></ul>
                </div>
            </div>
        </section>

        <div class="tk-rule"><span>PRESET</span></div>

        <section class="tk-section tk-section--preset">
            <span class="tk-step">02</span>
            <div class="tk-step-body">
                <div class="tk-presets" data-tk-presets role="radiogroup" aria-label="Capture preset"></div>
                <span class="tk-preset-hint" data-tk-preset-hint>manual control of every parameter</span>
            </div>
        </section>

        <div class="tk-rule"><span>PARAMETERS</span></div>

        <section class="tk-section tk-section--params">
            <span class="tk-step">03</span>
            <div class="tk-step-body">
                <div class="tk-param">
                    <label>COLMAP&middot;MATCHER</label>
                    <div class="tk-segmented" role="radiogroup" data-tk-matcher>
                        <button data-v="sequential" class="is-active" type="button">&#9655; SEQ</button>
                        <button data-v="vocab_tree" type="button">&#9670; VOCAB</button>
                        <button data-v="exhaustive" type="button">&#8862; EXHAUS</button>
                    </div>
                    <span class="tk-param-hint" data-tk-matcher-hint>frame-ordered &middot; fastest</span>
                </div>

                <div class="tk-param">
                    <label>ITERATIONS</label>
                    <div class="tk-dial">
                        <input type="range" min="5000" max="60000" step="1000" value="30000" data-tk-iters>
                        <div class="tk-dial-ticks">
                            <span>5K</span><span>15K</span><span>30K</span><span>45K</span><span>60K</span>
                        </div>
                    </div>
                    <span class="tk-param-value"><span data-tk-iters-out>30000</span></span>
                </div>

                <div class="tk-param">
                    <label>FRAME&middot;EXTRACT</label>
                    <div class="tk-segmented" data-tk-fps>
                        <button data-v="1" type="button">1 fps</button>
                        <button data-v="2" class="is-active" type="button">2 fps</button>
                        <button data-v="3" type="button">3 fps</button>
                        <button data-v="5" type="button">5 fps</button>
                    </div>
                    <span class="tk-param-hint" data-tk-fps-hint>video &rarr; ~120 frames at 2 fps</span>
                </div>

                <div class="tk-param">
                    <label>ENHANCE</label>
                    <div class="tk-toggles">
                        <button type="button" class="tk-toggle" data-tk-rembg aria-pressed="false">
                            <span class="tk-toggle-led"></span>REMOVE&nbsp;BACKGROUND
                        </button>
                        <button type="button" class="tk-toggle" data-tk-refine aria-pressed="false">
                            <span class="tk-toggle-led"></span>CLEAN&nbsp;GEOMETRY
                        </button>
                    </div>
                    <span class="tk-param-hint" data-tk-enhance-hint>isolate subject &middot; cull floaters &middot; fix exposure</span>
                </div>
            </div>
        </section>

        <div class="tk-rule"><span>ESTIMATE</span></div>

        <section class="tk-estimate" data-tk-estimate>
            <div class="tk-est-row">
                <span class="tk-est-key">FRAMES</span>
                <span class="tk-est-val" data-tk-est-frames>—</span>
            </div>
            <div class="tk-est-row">
                <span class="tk-est-key">UPLOAD</span>
                <span class="tk-est-val" data-tk-est-upload>—</span>
            </div>
            <div class="tk-est-row">
                <span class="tk-est-key">DISK</span>
                <span class="tk-est-val" data-tk-est-disk>—</span>
            </div>
            <div class="tk-est-row">
                <span class="tk-est-key">TRAIN</span>
                <span class="tk-est-val" data-tk-est-train>—</span>
            </div>
        </section>

        <div class="tk-rule"><span>PIPELINE</span></div>

        <section class="tk-pipeline">
            <ol class="tk-stages" data-tk-stages>
                <li data-stage="preparing"><span class="tk-stage-n">01</span><span>PREPARE</span></li>
                <li data-stage="extracting"><span class="tk-stage-n">02</span><span>EXTRACT</span></li>
                <li data-stage="colmap"><span class="tk-stage-n">03</span><span>COLMAP</span></li>
                <li data-stage="training"><span class="tk-stage-n">04</span><span>TRAIN</span></li>
                <li data-stage="exporting"><span class="tk-stage-n">05</span><span>EXPORT</span></li>
                <li data-stage="done"><span class="tk-stage-n">06</span><span>DONE</span></li>
            </ol>

            <div class="tk-progress">
                <div class="tk-progress-track">
                    <div class="tk-progress-fill" data-tk-fill></div>
                    <div class="tk-progress-cursor" data-tk-cursor></div>
                </div>
                <div class="tk-progress-meta">
                    <span class="tk-progress-pct" data-tk-pct>00.0%</span>
                    <span class="tk-progress-msg" data-tk-msg>standby &mdash; awaiting dispatch</span>
                    <span class="tk-progress-eta" data-tk-eta>t+ 00:00</span>
                </div>

                <div class="tk-metrics" hidden data-tk-metrics>
                    <div class="tk-metric">
                        <span class="tk-metric-k">GAUSSIANS</span>
                        <span class="tk-metric-v" data-tk-met-count>—</span>
                    </div>
                    <div class="tk-metric">
                        <span class="tk-metric-k">PLY SIZE</span>
                        <span class="tk-metric-v" data-tk-met-size>—</span>
                    </div>
                    <div class="tk-metric">
                        <span class="tk-metric-k">DURATION</span>
                        <span class="tk-metric-v" data-tk-met-dur>—</span>
                    </div>
                </div>
            </div>

            <div class="tk-viewer-wrap" hidden data-tk-viewer-wrap>
                <div class="tk-viewer-header">
                    <span class="tk-blink">&#9646;</span>
                    <span class="tk-viewer-title">LIVE 3D PREVIEW</span>
                    <a class="tk-viewer-link" data-tk-viewer-link target="_blank" rel="noopener">[ POP OUT &nearr; ]</a>
                </div>
                <iframe class="tk-viewer-frame" data-tk-viewer-frame
                        title="nerfstudio live viewer"
                        allow="cross-origin-isolated"></iframe>
            </div>

            <div class="tk-log-header">
                <span class="tk-blink">&#9646;</span>
                <span class="tk-log-title">EVENT LOG</span>
                <button class="tk-log-toggle" data-tk-rawlog-toggle type="button">[ VIEW RAW LOG ]</button>
            </div>
            <div class="tk-log" data-tk-log></div>
            <div class="tk-rawlog" hidden data-tk-rawlog>
                <pre class="tk-rawlog-pre" data-tk-rawlog-content></pre>
            </div>
        </section>

        <div class="tk-rule tk-rule--recent" hidden data-tk-rule-recent><span>RECENT JOBS</span></div>

        <section class="tk-recent" hidden data-tk-recent>
            <ul class="tk-recent-list" data-tk-recent-list></ul>
        </section>

        <footer class="tk-foot">
            <div class="tk-foot-meta">
                <span class="tk-blink">&#9646;</span>
                <span data-tk-status>READY</span>
            </div>
            <div class="tk-foot-buttons">
                <button class="tk-btn tk-btn--ghost" data-tk-cancel type="button">esc &middot; CLOSE</button>
                <button class="tk-btn tk-btn--danger" hidden data-tk-abort type="button">&#9632; ABORT JOB</button>
                <button class="tk-btn tk-btn--primary" data-tk-start disabled type="button">
                    <span>&#9655; DISPATCH JOB</span>
                </button>
            </div>
        </footer>
    </div>
</div></body>`;

// ── Video probing ─────────────────────────────────────────────────────────────
const probeVideo = (file: File): Promise<{ duration: number; width: number; height: number; sizeMB: number } | null> => new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const v = document.createElement('video');
    v.preload = 'metadata';
    v.muted = true;
    let done = false;
    const finish = (result: any) => {
        if (done) return;
        done = true;
        URL.revokeObjectURL(url);
        resolve(result);
    };
    v.onloadedmetadata = () => finish({
        duration: v.duration,
        width: v.videoWidth,
        height: v.videoHeight,
        sizeMB: file.size / 1024 / 1024
    });
    v.onerror = () => finish(null);
    setTimeout(() => finish(null), 5000);
    v.src = url;
});

// ── Capture-quality hints ─────────────────────────────────────────────────────
type Hint = { tone: 'good' | 'warn' | 'bad'; text: string };

const buildVideoHints = (
    m: { duration: number; width: number; height: number; sizeMB: number },
    fps: number
): { summary: string; hints: Hint[] } => {
    const hints: Hint[] = [];
    const minSide = Math.min(m.width, m.height);
    const frames = Math.round(m.duration * fps);

    if (m.duration < 20) hints.push({ tone: 'bad', text: `Duration ${m.duration.toFixed(0)}s — too short, aim for 60–90s` });
    else if (m.duration < 45) hints.push({ tone: 'warn', text: `Duration ${m.duration.toFixed(0)}s — ok for small objects` });
    else if (m.duration > 180) hints.push({ tone: 'warn', text: `Duration ${m.duration.toFixed(0)}s — long, consider 1 fps` });
    else hints.push({ tone: 'good', text: `Duration ${m.duration.toFixed(0)}s — good range` });

    if (minSide < 720) hints.push({ tone: 'bad', text: `Resolution ${m.width}×${m.height} — too low, use 1080p+` });
    else if (minSide < 1080) hints.push({ tone: 'warn', text: `Resolution ${m.width}×${m.height} — 720p works, 1080p is better` });
    else hints.push({ tone: 'good', text: `Resolution ${m.width}×${m.height} — good` });

    if (frames < 30) hints.push({ tone: 'bad', text: `Will extract ${frames} frames — too few, raise fps or duration` });
    else if (frames < 80) hints.push({ tone: 'warn', text: `Will extract ${frames} frames — workable for small objects` });
    else if (frames > 500) hints.push({ tone: 'warn', text: `Will extract ${frames} frames — many, consider 1 fps` });
    else hints.push({ tone: 'good', text: `Will extract ${frames} frames — sweet spot for COLMAP` });

    const badCount = hints.filter(h => h.tone === 'bad').length;
    const goodCount = hints.filter(h => h.tone === 'good').length;
    const summary = badCount > 0 ? 'risks detected' : goodCount === hints.length ? 'looks great' : 'acceptable';
    return { summary, hints };
};

const buildPhotoHints = (count: number, totalBytes: number): { summary: string; hints: Hint[] } => {
    const hints: Hint[] = [];
    const totalMB = totalBytes / 1024 / 1024;
    const avgMB = totalMB / count;

    if (count < 30) hints.push({ tone: 'bad', text: `${count} photos — too few, COLMAP needs 50+` });
    else if (count < 80) hints.push({ tone: 'warn', text: `${count} photos — minimum for small objects` });
    else if (count > 400) hints.push({ tone: 'warn', text: `${count} photos — many, training will be slow` });
    else hints.push({ tone: 'good', text: `${count} photos — good count` });

    if (avgMB < 0.4) hints.push({ tone: 'warn', text: `${avgMB.toFixed(1)} MB/image — low resolution likely` });
    else hints.push({ tone: 'good', text: `${avgMB.toFixed(1)} MB/image — good resolution` });

    const badCount = hints.filter(h => h.tone === 'bad').length;
    const goodCount = hints.filter(h => h.tone === 'good').length;
    const summary = badCount > 0 ? 'risks detected' : goodCount === hints.length ? 'looks great' : 'acceptable';
    return { summary, hints };
};


// ── Component ─────────────────────────────────────────────────────────────────
class TrainPopup extends Container {
    show: () => Promise<void>;
    hide: () => void;
    destroy: () => void;

    constructor(events: Events, args = {}) {
        args = { id: 'train-popup', hidden: true, tabIndex: -1, ...args };
        super(args);

        const doc = new DOMParser().parseFromString(TEMPLATE, 'text/html');
        const consoleEl = doc.querySelector('.tk-console') as HTMLElement;
        this.append(new Element({ dom: consoleEl }));

        const q  = <T extends HTMLElement = HTMLElement>(s: string) => consoleEl.querySelector(s) as T;
        const qa = <T extends HTMLElement = HTMLElement>(s: string) => Array.from(consoleEl.querySelectorAll(s)) as T[];

        // ── DOM refs ────────────────────────────────────────────────
        const presetsWrap     = q('[data-tk-presets]');
        const presetHint      = q('[data-tk-preset-hint]');
        const estFrames       = q('[data-tk-est-frames]');
        const estUpload       = q('[data-tk-est-upload]');
        const estDisk         = q('[data-tk-est-disk]');
        const estTrain        = q('[data-tk-est-train]');
        const metricsBox      = q('[data-tk-metrics]');
        const metCount        = q('[data-tk-met-count]');
        const metSize         = q('[data-tk-met-size]');
        const metDur          = q('[data-tk-met-dur]');
        const viewerWrap      = q('[data-tk-viewer-wrap]');
        const viewerFrame     = q<HTMLIFrameElement>('[data-tk-viewer-frame]');
        const viewerLink      = q<HTMLAnchorElement>('[data-tk-viewer-link]');
        const offlineBox      = q('[data-tk-offline]');
        const urlInput        = q<HTMLInputElement>('[data-tk-url]');
        const retryBtn        = q<HTMLButtonElement>('[data-tk-retry]');
        const pickBtn         = q<HTMLButtonElement>('[data-tk-pick]');
        const sourceRow       = q('[data-tk-source]');
        const sourceKind      = q('[data-tk-kind]');
        const sourceName      = q('[data-tk-name]');
        const sourceBytes     = q('[data-tk-bytes]');
        const clearBtn        = q<HTMLButtonElement>('[data-tk-clear]');
        const hintsBox        = q('[data-tk-hints]');
        const hintsSummary    = q('[data-tk-hints-summary]');
        const hintsList       = q<HTMLUListElement>('[data-tk-hints-list]');
        const itersSlider     = q<HTMLInputElement>('[data-tk-iters]');
        const itersOut        = q('[data-tk-iters-out]');
        const matcherWrap     = q('[data-tk-matcher]');
        const matcherHint     = q('[data-tk-matcher-hint]');
        const fpsWrap         = q('[data-tk-fps]');
        const fpsHint         = q('[data-tk-fps-hint]');
        const rembgBtn        = q<HTMLButtonElement>('[data-tk-rembg]');
        const refineBtn       = q<HTMLButtonElement>('[data-tk-refine]');
        const enhanceHint     = q('[data-tk-enhance-hint]');
        const stages          = qa<HTMLLIElement>('[data-tk-stages] li');
        const fill            = q<HTMLDivElement>('[data-tk-fill]');
        const cursor          = q<HTMLDivElement>('[data-tk-cursor]');
        const pct             = q('[data-tk-pct]');
        const msg             = q('[data-tk-msg]');
        const eta             = q('[data-tk-eta]');
        const logEl           = q('[data-tk-log]');
        const rawlogToggleBtn = q<HTMLButtonElement>('[data-tk-rawlog-toggle]');
        const rawlogBox       = q('[data-tk-rawlog]');
        const rawlogPre       = q('[data-tk-rawlog-content]');
        const recentRule      = q('[data-tk-rule-recent]');
        const recentBox       = q('[data-tk-recent]');
        const recentList      = q<HTMLUListElement>('[data-tk-recent-list]');
        const startBtn        = q<HTMLButtonElement>('[data-tk-start]');
        const abortBtn        = q<HTMLButtonElement>('[data-tk-abort]');
        const cancelBtn       = q<HTMLButtonElement>('[data-tk-cancel]');
        const utcEl           = q('[data-tk-utc]');
        const jobidEl         = q('[data-tk-jobid]');
        const gpuEl           = q('[data-tk-gpu]');
        const verEl           = q('[data-tk-ver]');
        const statusEl        = q('[data-tk-status]');

        verEl.textContent = `SS·${APP_VERSION} / GS·1.4`;

        const fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.multiple = true;
        fileInput.accept = [...VIDEO_EXTS, ...IMAGE_EXTS].join(',');
        fileInput.style.display = 'none';
        consoleEl.appendChild(fileInput);

        // ── State ───────────────────────────────────────────────────
        let pickedFiles: File[] = [];
        let currentMatcher = 'sequential';
        let currentFps = 2;
        let currentPreset = 'custom';
        let removeBackground = false;
        let refineGeometry = false;
        let lastFailedJobId: string | null = null;  // for resume-on-retry
        let startedAt = 0;
        let activeJob: string | null = null;
        let videoMeta: { duration: number; width: number; height: number; sizeMB: number } | null = null;
        let rawlogPollId: ReturnType<typeof setInterval> | null = null;
        let rawlogVisible = false;
        let gpuLivePollId: ReturnType<typeof setInterval> | null = null;

        // ── UTC clock ───────────────────────────────────────────────
        const updateUTC = () => {
            const d = new Date();
            utcEl.textContent = `${d.toISOString().slice(11, 19)}Z`;
        };
        updateUTC();
        const utcTimer = setInterval(updateUTC, 1000);

        // ── Preset selector ─────────────────────────────────────────
        const applyPreset = (id: string) => {
            const p = PRESETS.find(x => x.id === id);
            if (!p) return;
            currentPreset = id;
            presetHint.textContent = p.description;
            presetsWrap.querySelectorAll('button').forEach((b) => {
                b.classList.toggle('is-active', (b as HTMLButtonElement).dataset.v === id);
            });
            // Non-custom presets push their values into the controls
            if (id !== 'custom') {
                currentMatcher = p.matcher;
                setSegment(matcherWrap, p.matcher);
                matcherHint.textContent = MATCHER_HINTS[p.matcher] || '';
                currentFps = p.extract_fps;
                setSegment(fpsWrap, String(p.extract_fps));
                itersSlider.value = String(p.max_iters);
                syncSlider();
            }
            void refreshEstimate();
        };

        const buildPresets = () => {
            presetsWrap.replaceChildren();
            for (const p of PRESETS) {
                const b = document.createElement('button');
                b.type = 'button';
                b.dataset.v = p.id;
                b.className = p.id === currentPreset ? 'is-active' : '';
                b.textContent = p.label;
                b.addEventListener('click', () => applyPreset(p.id));
                presetsWrap.appendChild(b);
            }
        };

        // ── Estimator ───────────────────────────────────────────────
        const refreshEstimate = () => {
            if (pickedFiles.length === 0) {
                estFrames.textContent = '—';
                estUpload.textContent = '—';
                estDisk.textContent = '—';
                estTrain.textContent = '—';
                return;
            }
            const iters = +itersSlider.value;
            const est = estimateJob(pickedFiles, videoMeta, currentFps, iters);
            estFrames.textContent = String(est.frames);
            estUpload.textContent = est.uploadMB < 1024 ? `${est.uploadMB} MB` : `${(est.uploadMB / 1024).toFixed(2)} GB`;
            estDisk.textContent = est.diskMB < 1024 ? `${est.diskMB} MB` : `${(est.diskMB / 1024).toFixed(2)} GB`;
            estTrain.textContent = est.trainMin < 60 ? `~${est.trainMin} min` : `~${(est.trainMin / 60).toFixed(1)} h`;
        };

        // ── GPU live VRAM polling ───────────────────────────────────
        const pollGpuLive = async () => {
            try {
                const r = await fetch(`${getBackendUrl()}/gpu/live`, { signal: AbortSignal.timeout(2500) });
                if (!r.ok) return;
                const info = await r.json();
                if (info.available && info.free_gb != null && info.total_gb) {
                    gpuEl.textContent = `${info.free_gb}/${info.total_gb}G free · ${info.pct_used}% used`;
                }
            } catch { /* ignore */ }
        };
        const startGpuLivePoll = () => {
            if (gpuLivePollId !== null) return;
            void pollGpuLive();
            gpuLivePollId = setInterval(pollGpuLive, 3000);
        };
        const stopGpuLivePoll = () => {
            if (gpuLivePollId !== null) {
                clearInterval(gpuLivePollId);
                gpuLivePollId = null;
            }
        };

        // ── Metrics display (done state) ────────────────────────────
        const showMetrics = async (jobId: string) => {
            try {
                const r = await fetch(`${getBackendUrl()}/jobs/${jobId}/metrics`);
                if (!r.ok) return;
                const data = await r.json();
                const m = data.metrics || {};
                metCount.textContent = m.gaussian_count != null ? m.gaussian_count.toLocaleString() : '—';
                metSize.textContent = m.file_size_mb != null ? `${m.file_size_mb} MB` : '—';
                metDur.textContent = data.duration_sec != null ?
                    `${Math.floor(data.duration_sec / 60)}m ${Math.round(data.duration_sec % 60)}s` :
                    '—';
                metricsBox.removeAttribute('hidden');
            } catch { /* ignore */ }
        };
        const hideMetrics = () => metricsBox.setAttribute('hidden', '');

        // ── Backend connectivity ────────────────────────────────────
        const checkBackend = async (): Promise<boolean> => {
            try {
                urlInput.value = getBackendUrl();
                const r = await fetch(`${getBackendUrl()}/`, { signal: AbortSignal.timeout(4000) });
                if (r.ok) {
                    offlineBox.setAttribute('hidden', '');
                    return true;
                }
            } catch { /* fall through */ }
            offlineBox.removeAttribute('hidden');
            urlInput.value = getBackendUrl();
            return false;
        };

        retryBtn.addEventListener('click', async () => {
            const prev = retryBtn.textContent!;
            retryBtn.textContent = '⟳ CHECKING…';
            retryBtn.disabled = true;
            const newUrl = urlInput.value.trim();
            if (newUrl) setBackendUrl(newUrl);
            await checkBackend();
            retryBtn.textContent = prev;
            retryBtn.disabled = false;
        });

        // ── GPU info ────────────────────────────────────────────────
        const loadGpuInfo = async () => {
            try {
                const r = await fetch(`${getBackendUrl()}/gpu`, { signal: AbortSignal.timeout(5000) });
                if (r.ok) {
                    const info = await r.json();
                    gpuEl.textContent = info.label || 'no GPU';
                    gpuEl.title = info.name || '';
                    if (info.free_gb != null) {
                        gpuEl.textContent += ` (${info.free_gb}G free)`;
                    }
                }
            } catch { /* backend offline, ignore */ }
        };

        // ── System notifications ────────────────────────────────────
        const maybeRequestNotificationPermission = () => {
            if ('Notification' in window && Notification.permission === 'default') {
                void Notification.requestPermission();
            }
        };
        const notify = (title: string, body: string) => {
            if ('Notification' in window && Notification.permission === 'granted') {
                try {
                    new Notification(title, { body, icon: '/favicon.ico' });
                } catch { /* ignore */ }
            }
        };

        // ── Log panel ───────────────────────────────────────────────
        const appendLog = (tag: string, message: string, tone: 'system' | 'ok' | 'warn' | 'err' = 'system') => {
            const elapsed = startedAt ? (Date.now() - startedAt) / 1000 : 0;
            const row = document.createElement('div');
            row.className = `tk-log-row tk-log-row--${tone}`;
            const t = document.createElement('span'); t.className = 'tk-log-t'; t.textContent = fmtElapsed(elapsed);
            const tg = document.createElement('span'); tg.className = 'tk-log-tag'; tg.textContent = tag;
            const m = document.createElement('span'); m.className = 'tk-log-m'; m.textContent = message;
            row.append(t, tg, m);
            logEl.insertBefore(row, logEl.firstChild);
            while (logEl.children.length > 60) logEl.removeChild(logEl.lastChild!);
        };

        // ── Raw log viewer ──────────────────────────────────────────
        const startRawLogPoll = (jobId: string) => {
            if (rawlogPollId !== null) return;
            rawlogPollId = setInterval(async () => {
                try {
                    const r = await fetch(`${getBackendUrl()}/jobs/${jobId}/log`);
                    if (r.ok) {
                        const text = await r.text();
                        rawlogPre.textContent = text;
                        if (rawlogVisible) rawlogPre.scrollTop = rawlogPre.scrollHeight;
                    }
                } catch { /* ignore */ }
            }, 2000);
        };
        const stopRawLogPoll = () => {
            if (rawlogPollId !== null) {
                clearInterval(rawlogPollId);
                rawlogPollId = null;
            }
        };

        rawlogToggleBtn.addEventListener('click', () => {
            rawlogVisible = !rawlogVisible;
            if (rawlogVisible) {
                rawlogBox.removeAttribute('hidden');
                rawlogToggleBtn.textContent = '[ HIDE RAW LOG ]';
                if (rawlogPre.textContent) rawlogPre.scrollTop = rawlogPre.scrollHeight;
            } else {
                rawlogBox.setAttribute('hidden', '');
                rawlogToggleBtn.textContent = '[ VIEW RAW LOG ]';
            }
        });

        // ── Progress ────────────────────────────────────────────────
        const setProgress = (overall: number, stage: string, message: string) => {
            const p = Math.max(0, Math.min(1, overall || 0));
            const w = (p * 100).toFixed(1);
            fill.style.width = `${w}%`;
            cursor.style.left = `${w}%`;
            pct.textContent = `${w.padStart(4, '0')}%`;
            msg.textContent = message || '';
            const elapsed = startedAt ? (Date.now() - startedAt) / 1000 : 0;
            eta.textContent = fmtElapsed(elapsed);
            stages.forEach((li) => {
                const ls = li.dataset.stage!;
                li.classList.remove('is-active', 'is-done', 'is-failed');
                if (stage === 'failed' || stage === 'cancelled') return;
                if (STAGE_ORDER.indexOf(ls) < STAGE_ORDER.indexOf(stage)) li.classList.add('is-done');
                else if (ls === stage) li.classList.add('is-active');
            });
            if (stage === 'failed' || stage === 'cancelled') stages.forEach(li => li.classList.add('is-failed'));
        };

        const setState = (state: 'idle' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled') => {
            consoleEl.dataset.state = state;
        };

        // ── Segmented control ───────────────────────────────────────
        const setSegment = (wrap: HTMLElement, value: string) => {
            wrap.querySelectorAll('button').forEach((b) => {
                b.classList.toggle('is-active', (b as HTMLButtonElement).dataset.v === value);
            });
        };

        // ── Hints ───────────────────────────────────────────────────
        const renderHints = (summary: string, hints: Hint[]) => {
            hintsSummary.textContent = summary;
            hintsSummary.dataset.tone = hints.some(h => h.tone === 'bad') ? 'bad' :
                hints.every(h => h.tone === 'good') ? 'good' : 'warn';
            while (hintsList.firstChild) hintsList.removeChild(hintsList.firstChild);
            for (const h of hints) {
                const li = document.createElement('li');
                li.className = `tk-hints-item tk-hints-item--${h.tone}`;
                const dot = document.createElement('span'); dot.className = 'tk-hints-dot';
                const txt = document.createElement('span'); txt.className = 'tk-hints-text'; txt.textContent = h.text;
                li.append(dot, txt);
                hintsList.appendChild(li);
            }
            hintsBox.removeAttribute('hidden');
        };

        const refreshFpsHint = () => {
            if (videoMeta) {
                const frames = Math.round(videoMeta.duration * currentFps);
                fpsHint.textContent = `video → ~${frames} frames at ${currentFps} fps`;
            } else {
                fpsHint.textContent = 'video → frame sampling rate';
            }
        };

        const refreshHints = async () => {
            if (pickedFiles.length === 0) {
                hintsBox.setAttribute('hidden', '');
                videoMeta = null;
                refreshFpsHint();
                return;
            }
            const hasVideo = pickedFiles.some(f => isVideoFile(f.name));
            if (hasVideo) {
                videoMeta = await probeVideo(pickedFiles[0]);
                if (videoMeta) {
                    // Auto-select the best FPS
                    const suggested = autoFps(videoMeta.duration);
                    currentFps = suggested;
                    setSegment(fpsWrap, String(currentFps));
                    const { summary, hints } = buildVideoHints(videoMeta, currentFps);
                    renderHints(summary, hints);
                }
            } else {
                videoMeta = null;
                const total = pickedFiles.reduce((a, f) => a + f.size, 0);
                const { summary, hints } = buildPhotoHints(pickedFiles.length, total);
                renderHints(summary, hints);
            }
            refreshFpsHint();
        };

        const refreshSource = () => {
            if (pickedFiles.length === 0) {
                sourceRow.setAttribute('hidden', '');
                // Keep START live when a failed job can be resumed.
                startBtn.disabled = lastFailedJobId === null;
                void refreshHints();
                return;
            }
            const hasVideo = pickedFiles.some(f => isVideoFile(f.name));
            const totalBytes = pickedFiles.reduce((a, f) => a + f.size, 0);
            sourceRow.removeAttribute('hidden');
            sourceBytes.textContent = fmtBytes(totalBytes);
            if (hasVideo) {
                sourceKind.textContent = 'VIDEO';
                sourceKind.setAttribute('data-tone', 'cyan');
                sourceName.textContent = pickedFiles[0].name;
                setSegment(matcherWrap, 'sequential');
                currentMatcher = 'sequential';
                matcherHint.textContent = 'frame-ordered · fastest';
            } else {
                sourceKind.textContent = `STILLS · ${pickedFiles.length}`;
                sourceKind.setAttribute('data-tone', 'amber');
                sourceName.textContent = pickedFiles[0].name;
                setSegment(matcherWrap, 'vocab_tree');
                currentMatcher = 'vocab_tree';
                matcherHint.textContent = 'unordered · vocab tree · robust';
            }
            startBtn.disabled = false;
            void refreshHints();
            void refreshEstimate();
        };

        // ── Recent jobs ─────────────────────────────────────────────
        const downloadPly = (jobId: string, name: string) => {
            const a = document.createElement('a');
            a.href = `${getBackendUrl()}/jobs/${jobId}/splat.ply`;
            a.download = `${name}.ply`;
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        };

        const renderRecent = (jobs: any[]) => {
            const usable = jobs.filter(j => j.has_ply);
            while (recentList.firstChild) recentList.removeChild(recentList.firstChild);
            if (usable.length === 0) {
                recentBox.setAttribute('hidden', '');
                recentRule.setAttribute('hidden', '');
                return;
            }
            recentBox.removeAttribute('hidden');
            recentRule.removeAttribute('hidden');
            usable.slice(0, 8).forEach((j) => {
                const li = document.createElement('li');
                li.className = 'tk-recent-item';
                li.dataset.tone = j.stage === 'failed' ? 'fail' : 'ok';

                const thumb = document.createElement('span');
                thumb.className = 'tk-recent-thumb';
                if (j.has_thumbnail) {
                    const img = document.createElement('img');
                    img.src = `${getBackendUrl()}/jobs/${j.id}/thumbnail`;
                    img.alt = '';
                    img.loading = 'lazy';
                    thumb.appendChild(img);
                } else {
                    thumb.textContent = j.upload_kind === 'video' ? '▶' : '▦';
                    thumb.dataset.empty = '1';
                }

                const stamp = document.createElement('span'); stamp.className = 'tk-recent-stamp';
                stamp.textContent = fmtAgo(j.created_at);
                const id = document.createElement('span'); id.className = 'tk-recent-id';
                id.textContent = j.id.slice(0, 8);
                const name = document.createElement('span'); name.className = 'tk-recent-name';
                name.textContent = j.name;
                const meta = document.createElement('span'); meta.className = 'tk-recent-meta';
                const metaBits = [j.upload_kind, `${j.max_iters.toLocaleString()} iter`];
                if (j.metrics?.gaussian_count) metaBits.push(`${(j.metrics.gaussian_count / 1000).toFixed(0)}k gs`);
                if (j.metrics?.file_size_mb) metaBits.push(`${j.metrics.file_size_mb} MB`);
                meta.textContent = metaBits.join(' · ');

                const loadBtn = document.createElement('button'); loadBtn.className = 'tk-recent-load'; loadBtn.type = 'button';
                loadBtn.textContent = 'load →';
                loadBtn.onclick = (ev) => {
                    ev.stopPropagation(); void loadJobIntoEditor(j.id, j.name);
                };

                const dlBtn = document.createElement('button'); dlBtn.className = 'tk-recent-dl'; dlBtn.type = 'button';
                dlBtn.title = 'Save .ply to disk';
                dlBtn.textContent = '↓';
                dlBtn.onclick = (ev) => {
                    ev.stopPropagation(); downloadPly(j.id, j.name);
                };

                const delBtn = document.createElement('button'); delBtn.className = 'tk-recent-del'; delBtn.type = 'button';
                delBtn.textContent = '×';
                delBtn.title = 'Delete job';
                delBtn.onclick = async (ev) => {
                    ev.stopPropagation();
                    await fetch(`${getBackendUrl()}/jobs/${j.id}`, { method: 'DELETE' });
                    await refreshRecent();
                };

                li.append(thumb, stamp, id, name, meta, loadBtn, dlBtn, delBtn);
                recentList.appendChild(li);
            });
        };

        const refreshRecent = async () => {
            try {
                const r = await fetch(`${getBackendUrl()}/jobs`);
                if (!r.ok) return;
                renderRecent(await r.json());
            } catch {
                recentBox.setAttribute('hidden', '');
                recentRule.setAttribute('hidden', '');
            }
        };

        // ── Backend I/O ─────────────────────────────────────────────
        const submit = async (): Promise<string> => {
            const fd = new FormData();
            for (const f of pickedFiles) fd.append('files', f, f.name);
            fd.append('matcher', currentMatcher);
            fd.append('max_iters', itersSlider.value);
            fd.append('extract_fps', String(currentFps));
            fd.append('preset', currentPreset);
            fd.append('remove_background', String(removeBackground));
            fd.append('refine_geometry', String(refineGeometry));
            const r = await fetch(`${getBackendUrl()}/jobs`, { method: 'POST', body: fd });
            if (!r.ok) throw new Error(`upload ${r.status}: ${await r.text()}`);
            const j = await r.json();
            return j.id as string;
        };

        // Mount/unmount the live nerfstudio viewer iframe based on job state.
        const showViewer = (url: string) => {
            if (viewerFrame.src === url) return;          // already loaded
            viewerFrame.src = url;
            viewerLink.href = url;
            viewerWrap.removeAttribute('hidden');
            appendLog('VIEW', 'live 3D preview attached', 'ok');
        };
        const hideViewer = () => {
            if (viewerWrap.hasAttribute('hidden')) return;
            viewerWrap.setAttribute('hidden', '');
            viewerFrame.removeAttribute('src');           // stop the websocket
            viewerLink.removeAttribute('href');
        };

        const subscribe = (jobId: string) => new Promise<void>((resolve, reject) => {
            const src = new EventSource(`${getBackendUrl()}/jobs/${jobId}/stream`);
            let lastStage = '';
            src.addEventListener('status', (ev) => {
                const s = JSON.parse((ev as MessageEvent).data);
                setProgress(s.overall, s.stage, s.message);
                if (s.stage !== lastStage) {
                    appendLog(STAGE_LABELS[s.stage] || s.stage.toUpperCase(), s.message || '',
                        s.stage === 'failed' ? 'err' : s.stage === 'cancelled' ? 'warn' : 'ok');
                    lastStage = s.stage;
                }
                // Live viewer URL handling -- show during training, drop on end.
                if (s.viewer_url && s.stage === 'training') {
                    showViewer(s.viewer_url);
                } else if (s.stage !== 'training') {
                    hideViewer();
                }
                if (s.stage === 'done') {
                    src.close(); resolve();
                }
                if (s.stage === 'failed') {
                    src.close(); reject(new Error(s.error || s.message || 'failed'));
                }
                if (s.stage === 'cancelled') {
                    src.close(); reject(new Error('cancelled'));
                }
            });
            src.addEventListener('error', () => { /* auto-reconnect */ });
        });

        const fetchPlyAsFile = async (jobId: string): Promise<File> => {
            const url = `${getBackendUrl()}/jobs/${jobId}/splat.ply`;
            const resp = await fetch(url, { mode: 'cors', credentials: 'omit' });
            if (!resp.ok) throw new Error(`fetch PLY ${resp.status}`);
            const total = Number(resp.headers.get('content-length') || 0);
            const reader = resp.body?.getReader();
            const chunks: Uint8Array[] = [];
            let received = 0;
            if (reader) {
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    if (value) {
                        chunks.push(value);
                        received += value.byteLength;
                        if (total) {
                            const mb = (received / 1024 / 1024).toFixed(1);
                            const totMb = (total / 1024 / 1024).toFixed(1);
                            appendLog('FETCH', `${mb} / ${totMb} MB`, 'system');
                        }
                    }
                }
            }
            const blob = new Blob(chunks as BlobPart[], { type: 'application/octet-stream' });
            return new File([blob], `train-${jobId}.ply`, { type: 'application/octet-stream' });
        };

        const loadJobIntoEditor = async (jobId: string, name: string) => {
            try {
                appendLog('LOAD', `fetching PLY for ${name}`, 'system');
                const file = await fetchPlyAsFile(jobId);
                await events.invoke('import', [{ filename: file.name, contents: file }]);
                appendLog('READY', `${name} loaded into scene`, 'ok');
                this.hide();
            } catch (e: any) {
                appendLog('ERROR', e?.message ?? String(e), 'err');
            }
        };

        const cancelJob = async (jobId: string) => {
            try {
                appendLog('ABORT', 'requesting cancel…', 'warn');
                await fetch(`${getBackendUrl()}/jobs/${jobId}/cancel`, { method: 'POST' });
            } catch (e: any) {
                appendLog('ERROR', e?.message ?? String(e), 'err');
            }
        };

        // ── File picking / drag-and-drop ────────────────────────────
        const acceptFiles = (files: File[]) => {
            const hasVideo = files.some(f => isVideoFile(f.name));
            pickedFiles = hasVideo ?
                files.filter(f => isVideoFile(f.name)).slice(0, 1) :
                files.filter(f => isImageFile(f.name));
            // Picking fresh files means a fresh dispatch, not a resume.
            lastFailedJobId = null;
            refreshSource();
        };

        pickBtn.addEventListener('click', () => fileInput.click());
        clearBtn.addEventListener('click', () => {
            pickedFiles = [];
            fileInput.value = '';
            refreshSource();
        });
        fileInput.addEventListener('change', () => {
            if (fileInput.files) acceptFiles(Array.from(fileInput.files));
        });

        // Global drag-and-drop: listens on the whole console, not just the button
        consoleEl.addEventListener('dragover', (e: DragEvent) => {
            e.preventDefault();
            pickBtn.classList.add('is-over');
        });
        consoleEl.addEventListener('dragleave', (e: DragEvent) => {
            if (!e.relatedTarget || !consoleEl.contains(e.relatedTarget as Node)) {
                pickBtn.classList.remove('is-over');
            }
        });
        consoleEl.addEventListener('drop', (e: DragEvent) => {
            e.preventDefault();
            pickBtn.classList.remove('is-over');
            if (e.dataTransfer) acceptFiles(Array.from(e.dataTransfer.files));
        });

        // ── Matcher wiring ──────────────────────────────────────────
        const MATCHER_HINTS: Record<string, string> = {
            sequential: 'frame-ordered · fastest',
            vocab_tree: 'unordered · vocab tree · robust',
            exhaustive: 'unordered · brute-force · slow'
        };
        matcherWrap.querySelectorAll('button').forEach((b) => {
            b.addEventListener('click', () => {
                currentMatcher = (b as HTMLButtonElement).dataset.v!;
                setSegment(matcherWrap, currentMatcher);
                matcherHint.textContent = MATCHER_HINTS[currentMatcher] || '';
                if (currentPreset !== 'custom') applyPreset('custom');
                void refreshEstimate();
            });
        });

        // ── FPS wiring ──────────────────────────────────────────────
        fpsWrap.querySelectorAll('button').forEach((b) => {
            b.addEventListener('click', () => {
                currentFps = parseInt((b as HTMLButtonElement).dataset.v!, 10);
                setSegment(fpsWrap, String(currentFps));
                if (currentPreset !== 'custom') applyPreset('custom');
                void refreshHints();
                void refreshEstimate();
            });
        });

        // ── Enhance toggles (remove background / clean geometry) ────
        const ENHANCE_HINTS = {
            none:  'isolate subject · cull floaters · fix exposure',
            rembg: 'subject isolated — background masked during training',
            refine: 'scale-reg + bilateral grid + floater removal',
            both:  'clean isolated subject — best for object captures',
        };
        const syncEnhanceHint = () => {
            const key = removeBackground && refineGeometry ? 'both'
                : removeBackground ? 'rembg'
                    : refineGeometry ? 'refine' : 'none';
            enhanceHint.textContent = ENHANCE_HINTS[key];
        };
        rembgBtn.addEventListener('click', () => {
            removeBackground = !removeBackground;
            rembgBtn.classList.toggle('is-active', removeBackground);
            rembgBtn.setAttribute('aria-pressed', String(removeBackground));
            syncEnhanceHint();
        });
        refineBtn.addEventListener('click', () => {
            refineGeometry = !refineGeometry;
            refineBtn.classList.toggle('is-active', refineGeometry);
            refineBtn.setAttribute('aria-pressed', String(refineGeometry));
            syncEnhanceHint();
        });

        const syncSlider = () => {
            const min = +itersSlider.min;  // 5000
            const max = +itersSlider.max;  // 60000
            const p = ((+itersSlider.value - min) / (max - min) * 100).toFixed(1);
            itersSlider.style.setProperty('--p', `${p}%`);
            itersOut.textContent = Number(itersSlider.value).toLocaleString();
        };
        syncSlider(); // initialise the gradient fill
        itersSlider.addEventListener('input', () => {
            syncSlider();
            // changing iters manually flips us back to "custom"
            if (currentPreset !== 'custom') applyPreset('custom');
            void refreshEstimate();
        });

        // Build the preset row now that all helpers exist.
        buildPresets();

        // ── Reset / Detach logic ────────────────────────────────────
        const reset = () => {
            pickedFiles = [];
            fileInput.value = '';
            refreshSource();
            setProgress(0, '', 'standby — awaiting dispatch');
            stages.forEach(li => li.classList.remove('is-active', 'is-done', 'is-failed'));
            startBtn.disabled = true;
            const span = document.createElement('span'); span.textContent = '▷ DISPATCH JOB';
            startBtn.replaceChildren(span);
            cancelBtn.textContent = 'esc · CLOSE';
            abortBtn.setAttribute('hidden', '');
            setState('idle');
            jobidEl.textContent = '—';
            activeJob = null;
            startedAt = 0;
            stopRawLogPoll();
            rawlogBox.setAttribute('hidden', '');
            rawlogPre.textContent = '';
            rawlogToggleBtn.textContent = '[ VIEW RAW LOG ]';
            rawlogVisible = false;
            while (logEl.firstChild) logEl.removeChild(logEl.firstChild);
            appendLog('SYS', 'console initialized', 'system');
            statusEl.textContent = 'READY';
            hideMetrics();
            hideViewer();
            currentPreset = 'custom';
            applyPreset('custom');
            void refreshEstimate();
        };

        const setStartLabel = (text: string) => {
            const span = document.createElement('span');
            span.textContent = text;
            startBtn.replaceChildren(span);
        };

        // ── Keyboard handler ────────────────────────────────────────
        let onCloseResolve: (() => void) | null = null;
        const keydown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                e.stopPropagation(); onCloseResolve?.();
            } else {
                e.stopPropagation();
            }
        };

        // ── Show / Hide ─────────────────────────────────────────────
        this.show = () => {
            // If there's no active job in progress, reset the UI for a fresh session
            if (!activeJob) {
                reset();
            } else {
                // Reconnect to the background job
                appendLog('SYS', `reconnecting to job ${activeJob.slice(0, 8)}…`, 'system');
                statusEl.textContent = 'BACKGROUND JOB';
            }

            maybeRequestNotificationPermission();
            this.hidden = false;
            this.dom.addEventListener('keydown', keydown);
            this.dom.focus();

            // Async init — don't block
            checkBackend().then((online) => {
                if (online) {
                    Promise.all([loadGpuInfo(), refreshRecent()]).catch(() => undefined);
                    startGpuLivePoll();
                }
            }).catch(() => undefined);

            return new Promise<void>((resolve) => {
                onCloseResolve = () => {
                    resolve(); this.hide();
                };
                cancelBtn.onclick = () => onCloseResolve?.();

                abortBtn.onclick = async () => {
                    if (activeJob) await cancelJob(activeJob);
                };

                startBtn.onclick = async () => {
                    // Resume a previous failure when no new files are picked.
                    const resumeId = pickedFiles.length === 0 ? lastFailedJobId : null;
                    if (pickedFiles.length === 0 && !resumeId) return;
                    setState('queued');
                    startBtn.disabled = true;
                    setStartLabel('⟳ TRAINING…');
                    cancelBtn.textContent = 'esc · DETACH';
                    abortBtn.removeAttribute('hidden');
                    startedAt = Date.now();

                    const localId = Math.random().toString(36).slice(2, 6).toUpperCase();
                    jobidEl.textContent = localId;
                    appendLog('DISPATCH',
                        resumeId ? `resuming ${resumeId.slice(0, 8)}…` : `local id ${localId} — uploading…`,
                        'system');

                    try {
                        let id: string;
                        if (resumeId) {
                            const r = await fetch(`${getBackendUrl()}/jobs/${resumeId}/retry`, { method: 'POST' });
                            if (!r.ok) throw new Error(`resume failed (${r.status})`);
                            id = resumeId;
                            lastFailedJobId = null;
                            appendLog('RESUME', `↻ job ${id.slice(0, 8)} — reusing frames · COLMAP · checkpoint`, 'ok');
                        } else {
                            id = await submit();
                            appendLog('UPLOAD', `→ backend job ${id.slice(0, 8)}`, 'ok');
                        }
                        activeJob = id;
                        setState('running');
                        statusEl.textContent = 'TRAINING';
                        startRawLogPoll(id);
                        await subscribe(id);

                        // Done
                        setState('done');
                        statusEl.textContent = 'DONE';
                        notify('OneClick SPLAT — training complete', `${id.slice(0, 8)} is ready to load`);
                        showMetrics(id).catch(() => undefined);
                        appendLog('LOAD', 'loading PLY into editor…', 'ok');
                        const file = await fetchPlyAsFile(id);
                        await events.invoke('import', [{ filename: file.name, contents: file }]);
                        appendLog('READY', 'splat scene ready — closing in 3s', 'ok');
                        setStartLabel('✓ SPLAT LOADED');
                        startBtn.disabled = false;
                        startBtn.onclick = () => onCloseResolve?.();
                        abortBtn.setAttribute('hidden', '');
                        activeJob = null;
                        stopRawLogPoll();
                        refreshRecent().catch(() => undefined);

                        // Auto-close after a short delay so the user sees the
                        // success state but isn't forced to click the button.
                        // Countdown is reflected in the log so it's obvious.
                        let countdown = 3;
                        const closeTimer = window.setInterval(() => {
                            countdown -= 1;
                            if (countdown <= 0) {
                                window.clearInterval(closeTimer);
                                onCloseResolve?.();
                            } else {
                                setStartLabel(`✓ SPLAT LOADED — CLOSING IN ${countdown}s`);
                            }
                        }, 1000);
                        // Clicking the button cancels the auto-close (in case
                        // the user wants to keep the popup open for the log).
                        const cancelAutoClose = () => window.clearInterval(closeTimer);
                        startBtn.addEventListener('click', cancelAutoClose, { once: true });
                    } catch (e: any) {
                        // Remember the job so the next click resumes it
                        // (reusing frames + COLMAP + checkpoint) instead of
                        // re-uploading from scratch.
                        lastFailedJobId = activeJob;
                        const isCancelled = String(e?.message ?? '').toLowerCase().includes('cancel');
                        if (isCancelled) {
                            setState('cancelled');
                            appendLog('STOP', 'job cancelled', 'warn');
                            msg.textContent = 'cancelled — START to resume, or pick new files';
                            setStartLabel('↻ RESUME');
                            statusEl.textContent = 'CANCELLED';
                        } else {
                            setState('failed');
                            const raw  = e?.message ?? String(e);
                            const hint = friendlyError(raw);
                            if (hint) {
                                appendLog('REASON',  hint.title,  'err');
                                appendLog('FIX',     hint.advice, 'warn');
                                appendLog('TRACE',   raw,         'system');
                                msg.textContent = hint.title;
                            } else {
                                appendLog('ERROR', raw, 'err');
                                msg.textContent = 'pipeline halted — START to resume';
                            }
                            setStartLabel(lastFailedJobId ? '↻ RESUME' : 'RETRY');
                            statusEl.textContent = 'FAILED';
                            notify('OneClick SPLAT — training failed', hint ? hint.title : (raw || 'See the console for details'));
                        }
                        startBtn.disabled = false;
                        cancelBtn.textContent = 'esc · CLOSE';
                        abortBtn.setAttribute('hidden', '');
                        activeJob = null;
                        stopRawLogPoll();
                        refreshRecent().catch(() => undefined);
                    }
                };
            }).finally(() => {
                this.dom.removeEventListener('keydown', keydown);
            });
        };

        this.hide = () => {
            // If a job is running, DETACH (keep activeJob so show() can reconnect)
            if (activeJob) {
                appendLog('SYS', 'detached — training continues in background', 'warn');
            }
            stopGpuLivePoll();
            this.hidden = true;
        };

        this.destroy = () => {
            clearInterval(utcTimer);
            stopRawLogPoll();
            stopGpuLivePoll();
            this.hide();
            super.destroy();
        };
    }
}

export { TrainPopup };
