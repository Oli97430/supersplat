import { Container, Element } from '@playcanvas/pcui';

import { Events } from '../events';

const BACKEND_URL = 'http://127.0.0.1:8000';
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

// Static HTML template — no user-supplied interpolation goes here. Parsed via
// DOMParser instead of innerHTML for safety.
const TEMPLATE = `<!DOCTYPE html><body><div class="tk-console" data-state="idle">

    <div class="tk-grid"></div>
    <div class="tk-noise"></div>

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
                <div><dt>GPU</dt><dd>RTX&middot;3090 / 24G</dd></div>
                <div><dt>VER</dt><dd>SS&middot;2.26 / GS&middot;1.4</dd></div>
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
                    <span class="tk-drop-sub">.mp4 .mov .mkv  &middot;  .jpg .png</span>
                </button>
                <div class="tk-source" hidden data-tk-source>
                    <span class="tk-source-kind" data-tk-kind>&mdash;</span>
                    <span class="tk-source-name" data-tk-name>no input</span>
                    <span class="tk-source-bytes" data-tk-bytes>0 B</span>
                    <button class="tk-source-clear" data-tk-clear type="button">&times;</button>
                </div>

                <!-- Capture quality hints (populated from media metadata) -->
                <div class="tk-hints" hidden data-tk-hints>
                    <div class="tk-hints-row">
                        <span class="tk-hints-label">CAPTURE&middot;ANALYSIS</span>
                        <span class="tk-hints-summary" data-tk-hints-summary>&mdash;</span>
                    </div>
                    <ul class="tk-hints-list" data-tk-hints-list></ul>
                </div>
            </div>
        </section>

        <div class="tk-rule"><span>PARAMETERS</span></div>

        <section class="tk-section tk-section--params">
            <span class="tk-step">02</span>
            <div class="tk-step-body">
                <div class="tk-param">
                    <label>COLMAP&middot;MATCHER</label>
                    <div class="tk-segmented" role="radiogroup" data-tk-matcher>
                        <button data-v="sequential" class="is-active" type="button">&#9655; SEQUENTIAL</button>
                        <button data-v="exhaustive" type="button">&#8862; EXHAUSTIVE</button>
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
            </div>

            <div class="tk-log" data-tk-log></div>
        </section>

        <div class="tk-rule tk-rule--recent" hidden data-tk-rule-recent><span>RECENT JOBS</span></div>

        <section class="tk-recent" hidden data-tk-recent>
            <ul class="tk-recent-list" data-tk-recent-list></ul>
        </section>

        <footer class="tk-foot">
            <div class="tk-foot-meta">
                <span class="tk-blink">&#9646;</span> READY
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

// ── Inspect a video file to get duration, resolution, fps estimate ─────────
const probeVideo = (file: File): Promise<{ duration: number; width: number; height: number; sizeMB: number } | null> => {
    return new Promise((resolve) => {
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
        v.onloadedmetadata = () => {
            finish({
                duration: v.duration,
                width: v.videoWidth,
                height: v.videoHeight,
                sizeMB: file.size / 1024 / 1024
            });
        };
        v.onerror = () => finish(null);
        setTimeout(() => finish(null), 5000);
        v.src = url;
    });
};

type Hint = { tone: 'good' | 'warn' | 'bad'; text: string };

const buildVideoHints = (m: { duration: number; width: number; height: number; sizeMB: number }, fps: number): { summary: string; hints: Hint[] } => {
    const hints: Hint[] = [];
    const minSide = Math.min(m.width, m.height);
    const frames = Math.round(m.duration * fps);

    if (m.duration < 20) hints.push({ tone: 'bad', text: `Duration ${m.duration.toFixed(0)}s — too short, aim for 60-90s` });
    else if (m.duration < 45) hints.push({ tone: 'warn', text: `Duration ${m.duration.toFixed(0)}s — acceptable for small objects` });
    else if (m.duration > 180) hints.push({ tone: 'warn', text: `Duration ${m.duration.toFixed(0)}s — long, training will crawl` });
    else hints.push({ tone: 'good', text: `Duration ${m.duration.toFixed(0)}s — good range` });

    if (minSide < 720) hints.push({ tone: 'bad', text: `Resolution ${m.width}×${m.height} — too low, use 1080p+` });
    else if (minSide < 1080) hints.push({ tone: 'warn', text: `Resolution ${m.width}×${m.height} — 720p works but 1080p is better` });
    else hints.push({ tone: 'good', text: `Resolution ${m.width}×${m.height} — good` });

    if (frames < 30) hints.push({ tone: 'bad', text: `Will extract ${frames} frames — too few, raise fps or duration` });
    else if (frames < 80) hints.push({ tone: 'warn', text: `Will extract ${frames} frames — workable for a small object` });
    else if (frames > 500) hints.push({ tone: 'warn', text: `Will extract ${frames} frames — many, consider 1 fps to halve` });
    else hints.push({ tone: 'good', text: `Will extract ${frames} frames — sweet spot for COLMAP` });

    const goodCount = hints.filter(h => h.tone === 'good').length;
    const badCount = hints.filter(h => h.tone === 'bad').length;
    let summary: string;
    if (badCount > 0) summary = 'risks detected';
    else if (goodCount === hints.length) summary = 'looks great';
    else summary = 'acceptable';

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

    if (avgMB < 0.4) hints.push({ tone: 'warn', text: `${avgMB.toFixed(1)} MB/image avg — low resolution likely` });
    else hints.push({ tone: 'good', text: `${avgMB.toFixed(1)} MB/image avg — good resolution` });

    const goodCount = hints.filter(h => h.tone === 'good').length;
    const badCount = hints.filter(h => h.tone === 'bad').length;
    const summary = badCount > 0 ? 'risks detected' : goodCount === hints.length ? 'looks great' : 'acceptable';
    return { summary, hints };
};


class TrainPopup extends Container {
    show: () => Promise<void>;
    hide: () => void;
    destroy: () => void;

    constructor(events: Events, args = {}) {
        args = {
            id: 'train-popup',
            hidden: true,
            tabIndex: -1,
            ...args
        };

        super(args);

        const doc = new DOMParser().parseFromString(TEMPLATE, 'text/html');
        const consoleEl = doc.querySelector('.tk-console') as HTMLElement;
        this.append(new Element({ dom: consoleEl }));

        const q = <T extends HTMLElement = HTMLElement>(s: string) => consoleEl.querySelector(s) as T;
        const qa = <T extends HTMLElement = HTMLElement>(s: string) => Array.from(consoleEl.querySelectorAll(s)) as T[];

        const pickBtn = q<HTMLButtonElement>('[data-tk-pick]');
        const sourceRow = q('[data-tk-source]');
        const sourceKind = q('[data-tk-kind]');
        const sourceName = q('[data-tk-name]');
        const sourceBytes = q('[data-tk-bytes]');
        const clearBtn = q<HTMLButtonElement>('[data-tk-clear]');
        const hintsBox = q('[data-tk-hints]');
        const hintsSummary = q('[data-tk-hints-summary]');
        const hintsList = q<HTMLUListElement>('[data-tk-hints-list]');
        const itersSlider = q<HTMLInputElement>('[data-tk-iters]');
        const itersOut = q('[data-tk-iters-out]');
        const matcherWrap = q('[data-tk-matcher]');
        const matcherHint = q('[data-tk-matcher-hint]');
        const fpsWrap = q('[data-tk-fps]');
        const fpsHint = q('[data-tk-fps-hint]');
        const stages = qa<HTMLLIElement>('[data-tk-stages] li');
        const fill = q<HTMLDivElement>('[data-tk-fill]');
        const cursor = q<HTMLDivElement>('[data-tk-cursor]');
        const pct = q('[data-tk-pct]');
        const msg = q('[data-tk-msg]');
        const eta = q('[data-tk-eta]');
        const logEl = q('[data-tk-log]');
        const recentRule = q('[data-tk-rule-recent]');
        const recentBox = q('[data-tk-recent]');
        const recentList = q<HTMLUListElement>('[data-tk-recent-list]');
        const startBtn = q<HTMLButtonElement>('[data-tk-start]');
        const abortBtn = q<HTMLButtonElement>('[data-tk-abort]');
        const cancelBtn = q<HTMLButtonElement>('[data-tk-cancel]');
        const utcEl = q('[data-tk-utc]');
        const jobidEl = q('[data-tk-jobid]');

        const fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.multiple = true;
        fileInput.accept = [...VIDEO_EXTS, ...IMAGE_EXTS].join(',');
        fileInput.style.display = 'none';
        consoleEl.appendChild(fileInput);

        let pickedFiles: File[] = [];
        let currentMatcher = 'sequential';
        let currentFps = 2;
        let startedAt = 0;
        let activeJob: string | null = null;
        let videoMeta: { duration: number; width: number; height: number; sizeMB: number } | null = null;

        const setState = (state: 'idle' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled') => {
            consoleEl.dataset.state = state;
        };

        const updateUTC = () => {
            const d = new Date();
            utcEl.textContent = `${d.toISOString().slice(11, 19)}Z`;
        };
        updateUTC();
        const utcTimer = setInterval(updateUTC, 1000);

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

        const setSegment = (wrap: HTMLElement, value: string) => {
            wrap.querySelectorAll('button').forEach((b) => {
                b.classList.toggle('is-active', (b as HTMLButtonElement).dataset.v === value);
            });
        };

        // ── Hints ────────────────────────────────────────────────────────
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
                fpsHint.textContent = `video → frames sampling rate`;
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
                startBtn.disabled = true;
                refreshHints();
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
                setSegment(matcherWrap, 'exhaustive');
                currentMatcher = 'exhaustive';
                matcherHint.textContent = 'unordered photos · robust';
            }
            startBtn.disabled = false;
            refreshHints();
        };

        // ── Recent jobs ──────────────────────────────────────────────────
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

                const stamp = document.createElement('span'); stamp.className = 'tk-recent-stamp';
                stamp.textContent = fmtAgo(j.created_at);
                const id = document.createElement('span'); id.className = 'tk-recent-id';
                id.textContent = j.id.slice(0, 8);
                const name = document.createElement('span'); name.className = 'tk-recent-name';
                name.textContent = j.name;
                const meta = document.createElement('span'); meta.className = 'tk-recent-meta';
                meta.textContent = `${j.upload_kind} · ${j.max_iters.toLocaleString()} iter`;

                const loadBtn = document.createElement('button'); loadBtn.className = 'tk-recent-load'; loadBtn.type = 'button';
                loadBtn.textContent = 'load →';
                loadBtn.onclick = (ev) => {
                    ev.stopPropagation();
                    void loadJobIntoEditor(j.id, j.name);
                };

                const delBtn = document.createElement('button'); delBtn.className = 'tk-recent-del'; delBtn.type = 'button';
                delBtn.textContent = '×';
                delBtn.title = 'Delete this job';
                delBtn.onclick = async (ev) => {
                    ev.stopPropagation();
                    await fetch(`${BACKEND_URL}/jobs/${j.id}`, { method: 'DELETE' });
                    await refreshRecent();
                };

                li.append(stamp, id, name, meta, loadBtn, delBtn);
                recentList.appendChild(li);
            });
        };

        const refreshRecent = async () => {
            try {
                const r = await fetch(`${BACKEND_URL}/jobs`);
                if (!r.ok) return;
                const jobs = await r.json();
                renderRecent(jobs);
            } catch {
                // backend offline; quietly hide
                recentBox.setAttribute('hidden', '');
                recentRule.setAttribute('hidden', '');
            }
        };

        // ── Backend interactions ─────────────────────────────────────────
        const submit = async (): Promise<string> => {
            const fd = new FormData();
            for (const f of pickedFiles) fd.append('files', f, f.name);
            fd.append('matcher', currentMatcher);
            fd.append('max_iters', itersSlider.value);
            fd.append('extract_fps', String(currentFps));
            const r = await fetch(`${BACKEND_URL}/jobs`, { method: 'POST', body: fd });
            if (!r.ok) throw new Error(`upload ${r.status}: ${await r.text()}`);
            const j = await r.json();
            return j.id as string;
        };

        const subscribe = (jobId: string) => new Promise<void>((resolve, reject) => {
            const src = new EventSource(`${BACKEND_URL}/jobs/${jobId}/stream`);
            let lastStage = '';
            src.addEventListener('status', (ev) => {
                const s = JSON.parse((ev as MessageEvent).data);
                setProgress(s.overall, s.stage, s.message);
                if (s.stage !== lastStage) {
                    appendLog(STAGE_LABELS[s.stage] || s.stage.toUpperCase(), s.message || '',
                        s.stage === 'failed' ? 'err' : s.stage === 'cancelled' ? 'warn' : 'ok');
                    lastStage = s.stage;
                }
                if (s.stage === 'done') { src.close(); resolve(); }
                if (s.stage === 'failed') { src.close(); reject(new Error(s.error || s.message || 'failed')); }
                if (s.stage === 'cancelled') { src.close(); reject(new Error('cancelled')); }
            });
            src.addEventListener('error', () => { /* auto-reconnect */ });
        });

        const fetchPlyAsFile = async (jobId: string): Promise<File> => {
            const url = `${BACKEND_URL}/jobs/${jobId}/splat.ply`;
            const resp = await fetch(url, { mode: 'cors', credentials: 'omit' });
            if (!resp.ok) throw new Error(`fetch PLY ${resp.status}`);
            const total = Number(resp.headers.get('content-length') || 0);
            const reader = resp.body?.getReader();
            const chunks: Uint8Array[] = [];
            let received = 0;
            if (reader) {
                // eslint-disable-next-line no-constant-condition
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
            const blob = new Blob(chunks, { type: 'application/octet-stream' });
            const filename = `train-${jobId}.ply`;
            return new File([blob], filename, { type: 'application/octet-stream' });
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
                await fetch(`${BACKEND_URL}/jobs/${jobId}/cancel`, { method: 'POST' });
            } catch (e: any) {
                appendLog('ERROR', e?.message ?? String(e), 'err');
            }
        };

        // ── Wiring ───────────────────────────────────────────────────────
        pickBtn.addEventListener('click', () => fileInput.click());
        clearBtn.addEventListener('click', () => {
            pickedFiles = [];
            fileInput.value = '';
            refreshSource();
        });

        fileInput.addEventListener('change', () => {
            const all = fileInput.files ? Array.from(fileInput.files) : [];
            const hasVideo = all.some(f => isVideoFile(f.name));
            pickedFiles = hasVideo
                ? all.filter(f => isVideoFile(f.name)).slice(0, 1)
                : all.filter(f => isImageFile(f.name));
            refreshSource();
        });

        matcherWrap.querySelectorAll('button').forEach((b) => {
            b.addEventListener('click', () => {
                currentMatcher = (b as HTMLButtonElement).dataset.v!;
                setSegment(matcherWrap, currentMatcher);
                matcherHint.textContent = currentMatcher === 'sequential'
                    ? 'frame-ordered · fastest'
                    : 'unordered photos · robust';
            });
        });
        fpsWrap.querySelectorAll('button').forEach((b) => {
            b.addEventListener('click', () => {
                currentFps = parseInt((b as HTMLButtonElement).dataset.v!, 10);
                setSegment(fpsWrap, String(currentFps));
                refreshHints();
            });
        });

        itersSlider.addEventListener('input', () => {
            itersOut.textContent = itersSlider.value;
        });

        pickBtn.addEventListener('dragover', (e: DragEvent) => {
            e.preventDefault();
            pickBtn.classList.add('is-over');
        });
        pickBtn.addEventListener('dragleave', () => pickBtn.classList.remove('is-over'));
        pickBtn.addEventListener('drop', (e: DragEvent) => {
            e.preventDefault();
            pickBtn.classList.remove('is-over');
            const files = e.dataTransfer ? Array.from(e.dataTransfer.files) : [];
            const hasVideo = files.some(f => isVideoFile(f.name));
            pickedFiles = hasVideo
                ? files.filter(f => isVideoFile(f.name)).slice(0, 1)
                : files.filter(f => isImageFile(f.name));
            refreshSource();
        });

        const reset = () => {
            pickedFiles = [];
            fileInput.value = '';
            refreshSource();
            setProgress(0, '', 'standby — awaiting dispatch');
            stages.forEach(li => li.classList.remove('is-active', 'is-done', 'is-failed'));
            startBtn.disabled = true;
            const dispatch = document.createElement('span'); dispatch.textContent = '▷ DISPATCH JOB';
            startBtn.replaceChildren(dispatch);
            cancelBtn.textContent = 'esc · CLOSE';
            abortBtn.setAttribute('hidden', '');
            setState('idle');
            jobidEl.textContent = '—';
            activeJob = null;
            startedAt = 0;
            while (logEl.firstChild) logEl.removeChild(logEl.firstChild);
            appendLog('SYS', 'console initialized', 'system');
        };

        let onCloseResolve: (() => void) | null = null;
        const keydown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                e.stopPropagation();
                onCloseResolve?.();
            } else {
                e.stopPropagation();
            }
        };

        const setStartLabel = (text: string) => {
            const span = document.createElement('span');
            span.textContent = text;
            startBtn.replaceChildren(span);
        };

        this.show = () => {
            reset();
            void refreshRecent();
            this.hidden = false;
            this.dom.addEventListener('keydown', keydown);
            this.dom.focus();

            return new Promise<void>((resolve) => {
                onCloseResolve = () => { resolve(); this.hide(); };
                cancelBtn.onclick = () => onCloseResolve?.();

                abortBtn.onclick = async () => {
                    if (activeJob) await cancelJob(activeJob);
                };

                startBtn.onclick = async () => {
                    if (pickedFiles.length === 0) return;
                    setState('queued');
                    startBtn.disabled = true;
                    setStartLabel('⟳ TRAINING…');
                    cancelBtn.textContent = 'esc · DETACH';
                    abortBtn.removeAttribute('hidden');
                    startedAt = Date.now();
                    const localId = Math.random().toString(36).slice(2, 6).toUpperCase();
                    jobidEl.textContent = localId;
                    appendLog('DISPATCH', `local id ${localId} — uploading…`, 'system');
                    try {
                        const id = await submit();
                        activeJob = id;
                        appendLog('UPLOAD', `→ backend job ${id.slice(0, 8)}`, 'ok');
                        setState('running');
                        await subscribe(id);
                        setState('done');
                        appendLog('LOAD', 'loading PLY into editor…', 'ok');
                        const file = await fetchPlyAsFile(id);
                        await events.invoke('import', [{ filename: file.name, contents: file }]);
                        appendLog('READY', 'splat scene ready', 'ok');
                        setStartLabel('✓ VIEW IN EDITOR');
                        startBtn.disabled = false;
                        startBtn.onclick = () => onCloseResolve?.();
                        abortBtn.setAttribute('hidden', '');
                        void refreshRecent();
                    } catch (e: any) {
                        if (String(e?.message ?? '').toLowerCase().includes('cancel')) {
                            setState('cancelled');
                            appendLog('STOP', 'job cancelled', 'warn');
                            msg.textContent = 'cancelled — you can dispatch a new one';
                            setStartLabel('RETRY');
                        } else {
                            setState('failed');
                            appendLog('ERROR', e?.message ?? String(e), 'err');
                            msg.textContent = 'pipeline halted — see log above';
                            setStartLabel('RETRY');
                        }
                        startBtn.disabled = false;
                        cancelBtn.textContent = 'esc · CLOSE';
                        abortBtn.setAttribute('hidden', '');
                        void refreshRecent();
                    }
                };
            }).finally(() => {
                this.dom.removeEventListener('keydown', keydown);
            });
        };

        this.hide = () => { this.hidden = true; };

        this.destroy = () => {
            clearInterval(utcTimer);
            this.hide();
            super.destroy();
        };
    }
}

export { TrainPopup };
