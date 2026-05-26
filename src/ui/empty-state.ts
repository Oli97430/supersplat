import { Container, Element } from '@playcanvas/pcui';

import { Events } from '../events';

// Cinematic editorial empty state. Lives over the canvas. Auto-shows when no
// splats are loaded and auto-hides as soon as one is added. Pointer-events on
// the backdrop are off so menus and the 3D viewport remain interactive — only
// the interactive bits intercept clicks.

const TEMPLATE = `<!DOCTYPE html><body>
<div class="ems-stage" data-state="visible">

    <div class="ems-vignette"></div>
    <div class="ems-grain"></div>
    <div class="ems-glow" data-tk-glow></div>

    <div class="ems-corner ems-corner--tl">
        <span class="ems-mark">SUPERSPLAT</span>
        <span class="ems-dim">/ studio</span>
    </div>
    <div class="ems-corner ems-corner--tr">
        <span class="ems-dim"><span class="ems-dot"></span> live</span>
        <span class="ems-dim" data-ems-utc>00:00:00</span>
        <span class="ems-dim">v2.27</span>
    </div>

    <button class="ems-dismiss" data-ems-dismiss type="button">
        <span class="ems-dim">esc</span>
        <span>dismiss</span>
    </button>

    <div class="ems-content">
        <div class="ems-counter">
            <span class="ems-counter-n">no. 001</span>
            <span class="ems-counter-d">a soft introduction</span>
        </div>

        <h1 class="ems-headline">
            <span class="ems-word">Reconstruct</span>
            <span class="ems-word">the</span>
            <span class="ems-word ems-word--accent"><em>world&mdash;</em></span><br>
            <span class="ems-word">one</span>
            <span class="ems-word"><em>gaussian</em></span>
            <span class="ems-word">at a time.</span>
        </h1>

        <p class="ems-lede">
            A workspace for capturing, refining and publishing 3D scenes
            from <em>photographs &amp; video</em>. Begin with what you have.
        </p>

        <div class="ems-actions">
            <button class="ems-card ems-card--ghost" data-ems-open type="button">
                <span class="ems-card-no">01</span>
                <span class="ems-card-kicker">already have one</span>
                <span class="ems-card-title">Open a splat</span>
                <span class="ems-card-body">
                    Load a .ply, .splat, .sog, .ksplat, .spz or .ssproj
                    file from disk.
                </span>
                <span class="ems-card-cta">
                    <span>open file</span>
                    <span class="ems-arrow">&rarr;</span>
                </span>
            </button>

            <button class="ems-card ems-card--feature" data-ems-train type="button">
                <span class="ems-card-no">02</span>
                <span class="ems-card-kicker">starting from scratch</span>
                <span class="ems-card-title"><em>Train</em> a new scene</span>
                <span class="ems-card-body">
                    Hand over a short video or a folder of photos.
                    Our local rig recovers camera poses and grows
                    the gaussians for you.
                </span>
                <span class="ems-card-meta">
                    <span><b>~30 min</b> on RTX 3090</span>
                    <span class="ems-card-sep">&middot;</span>
                    <span>runs offline</span>
                </span>
                <span class="ems-card-cta ems-card-cta--feature">
                    <span>begin training</span>
                    <span class="ems-arrow">&rarr;</span>
                </span>
            </button>
        </div>

        <p class="ems-foot">
            <span class="ems-dim">or</span>
            drag any splat file onto the canvas.
        </p>
    </div>
</div>
</body>`;

class EmptyState extends Container {
    show: () => void;
    hide: () => void;
    destroy: () => void;

    constructor(events: Events, args = {}) {
        args = {
            id: 'empty-state',
            hidden: true,
            ...args
        };

        super(args);

        const doc = new DOMParser().parseFromString(TEMPLATE, 'text/html');
        const stage = doc.querySelector('.ems-stage') as HTMLElement;
        this.append(new Element({ dom: stage }));

        const q = <T extends HTMLElement = HTMLElement>(s: string) => stage.querySelector(s) as T;

        const openBtn = q<HTMLButtonElement>('[data-ems-open]');
        const trainBtn = q<HTMLButtonElement>('[data-ems-train]');
        const dismissBtn = q<HTMLButtonElement>('[data-ems-dismiss]');
        const glow = q('[data-tk-glow]');
        const utcEl = q('[data-ems-utc]');

        // live UTC label
        const updateUTC = () => {
            const d = new Date();
            const pad = (n: number) => String(n).padStart(2, '0');
            utcEl.textContent = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
        };
        updateUTC();
        const utcTimer = setInterval(updateUTC, 1000);

        // parallax glow follows the cursor
        const onMove = (e: PointerEvent) => {
            const x = (e.clientX / window.innerWidth) * 100;
            const y = (e.clientY / window.innerHeight) * 100;
            glow.style.setProperty('--gx', `${x}%`);
            glow.style.setProperty('--gy', `${y}%`);
        };
        window.addEventListener('pointermove', onMove);

        // CTA handlers
        openBtn.addEventListener('click', () => {
            events.invoke('doc.open');
        });
        trainBtn.addEventListener('click', () => {
            events.invoke('show.trainPopup');
        });
        dismissBtn.addEventListener('click', () => this.hide());

        // escape dismisses
        const keydown = (e: KeyboardEvent) => {
            if (e.key === 'Escape' && !this.hidden) {
                this.hide();
            }
        };
        document.addEventListener('keydown', keydown);

        // Visibility wired to scene state
        const refresh = () => {
            const empty = events.invoke('scene.empty') as boolean;
            if (empty) this.show(); else this.hide();
        };

        events.on('scene.elementAdded', () => this.hide());
        events.on('scene.elementRemoved', () => refresh());
        events.on('scene.clear', () => refresh());

        // initial check after a tick so other systems are ready
        setTimeout(refresh, 50);

        this.show = () => { this.hidden = false; stage.dataset.state = 'visible'; };
        this.hide = () => { this.hidden = true; };
        this.destroy = () => {
            clearInterval(utcTimer);
            window.removeEventListener('pointermove', onMove);
            document.removeEventListener('keydown', keydown);
            super.destroy();
        };
    }
}

export { EmptyState };
