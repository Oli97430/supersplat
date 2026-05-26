#!/usr/bin/env node
// Generate a minimal multi-resolution .ico from the icon design.
// Uses the `canvas` module that's already in the repo's node_modules.
//
// Run from the repo root:    node installer/scripts/make-icon.js
// Output:                    installer/assets/icon.ico

const fs   = require('fs');
const path = require('path');
const { createCanvas } = require(path.join(__dirname, '..', '..', 'node_modules', 'canvas'));

const SIZES = [256, 128, 64, 48, 32, 16];

function drawIcon(size) {
    const c = createCanvas(size, size);
    const ctx = c.getContext('2d');

    // Dark base circle
    ctx.fillStyle = '#0a0807';
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - 1, 0, Math.PI * 2);
    ctx.fill();

    // Amber gaussian splat (top-left)
    const g1 = ctx.createRadialGradient(
        size * 0.35, size * 0.35, 0,
        size * 0.35, size * 0.35, size * 0.45
    );
    g1.addColorStop(0,    'rgba(255, 180, 122, 0.95)');
    g1.addColorStop(0.4,  'rgba(255, 138,  61, 0.55)');
    g1.addColorStop(1,    'rgba(255, 107,  53, 0)');
    ctx.fillStyle = g1;
    ctx.fillRect(0, 0, size, size);

    // Indigo gaussian splat (bottom-right)
    const g2 = ctx.createRadialGradient(
        size * 0.68, size * 0.68, 0,
        size * 0.68, size * 0.68, size * 0.40
    );
    g2.addColorStop(0,    'rgba(91, 78, 255, 0.85)');
    g2.addColorStop(0.5,  'rgba(91, 78, 255, 0.30)');
    g2.addColorStop(1,    'rgba(91, 78, 255, 0)');
    ctx.fillStyle = g2;
    ctx.fillRect(0, 0, size, size);

    // Bright cyan-white core (centre)
    const g3 = ctx.createRadialGradient(
        size * 0.52, size * 0.50, 0,
        size * 0.52, size * 0.50, size * 0.18
    );
    g3.addColorStop(0,   'rgba(255, 255, 255, 0.95)');
    g3.addColorStop(0.6, 'rgba(168, 240, 250, 0.40)');
    g3.addColorStop(1,   'rgba(255, 255, 255, 0)');
    ctx.fillStyle = g3;
    ctx.fillRect(0, 0, size, size);

    // Subtle amber outline
    ctx.strokeStyle = 'rgba(255, 138, 61, 0.45)';
    ctx.lineWidth   = Math.max(1, size / 128);
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - 1, 0, Math.PI * 2);
    ctx.stroke();

    return c.toBuffer('image/png');
}

// Build a multi-icon ICO file: header + N image entries + concatenated PNGs.
// ICO spec: https://en.wikipedia.org/wiki/ICO_(file_format)
function buildIco(pngs) {
    const count = pngs.length;
    const header = Buffer.alloc(6);
    header.writeUInt16LE(0, 0);     // reserved
    header.writeUInt16LE(1, 2);     // type = 1 (ICO)
    header.writeUInt16LE(count, 4);

    const dirEntrySize = 16;
    const dirOffsetStart = header.length;
    const dirSize = dirEntrySize * count;
    let dataOffset = dirOffsetStart + dirSize;

    const dirEntries = Buffer.alloc(dirSize);
    pngs.forEach((png, i) => {
        const e = Buffer.alloc(dirEntrySize);
        const sz = png.size;
        e.writeUInt8 (sz >= 256 ? 0 : sz, 0);   // width  (0 = 256)
        e.writeUInt8 (sz >= 256 ? 0 : sz, 1);   // height (0 = 256)
        e.writeUInt8 (0, 2);                     // colour palette
        e.writeUInt8 (0, 3);                     // reserved
        e.writeUInt16LE(1, 4);                   // colour planes
        e.writeUInt16LE(32, 6);                  // bpp
        e.writeUInt32LE(png.data.length, 8);     // image size
        e.writeUInt32LE(dataOffset, 12);         // offset
        e.copy(dirEntries, i * dirEntrySize);
        dataOffset += png.data.length;
    });

    return Buffer.concat([header, dirEntries, ...pngs.map(p => p.data)]);
}

const pngs = SIZES.map(size => ({ size, data: drawIcon(size) }));
const ico  = buildIco(pngs);

const outPath = path.join(__dirname, '..', 'assets', 'icon.ico');
fs.writeFileSync(outPath, ico);
console.log(`Wrote ${outPath} (${ico.length} bytes, ${SIZES.length} resolutions)`);
