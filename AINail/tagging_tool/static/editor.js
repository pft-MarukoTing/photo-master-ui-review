// ── Constants ──────────────────────────────────────────────
const FINGER_LABELS     = ['拇指', '食指', '中指', '無名指', '小指'];
const FINGER_COLORS_HEX = ['#ff4444', '#44dd44', '#4488ff', '#ffcc22', '#ff44ff'];
const MAX_UNDO = 20;

function hexToRgb(hex) {
    return [parseInt(hex.slice(1,3),16), parseInt(hex.slice(3,5),16), parseInt(hex.slice(5,7),16)];
}

// ── State ──────────────────────────────────────────────────
let images = [], currentIdx = 0, currentStem = null;
let currentFinger = 0, brushSize = 20, isErasing = false, maskOpacity = 0.9;
let isDrawing = false, lastX = 0, lastY = 0;
let cursorImgX = -1, cursorImgY = -1;
let maskStatus = Array(5).fill({saved: false, model: false});
let targetImage = null;

// Viewport transform (pan + zoom)
let viewScale = 1, viewOffX = 0, viewOffY = 0;
let isPanning = false, panCX = 0, panCY = 0, panOX = 0, panOY = 0;

// Offscreen mask canvases (one per finger, at full image resolution)
const maskCanvases = Array.from({length: 5}, () => document.createElement('canvas'));
const undoStacks   = Array.from({length: 5}, () => []);

// Shared temp canvas for compositing (image resolution)
const tmpCanvas = document.createElement('canvas');
const tmpCtx    = tmpCanvas.getContext('2d');

// Display canvas (fills container)
const canvas = document.getElementById('editor-canvas');
const ctx    = canvas.getContext('2d');

// ── Coordinate helpers ────────────────────────────────────
function loadImg(url) {
    return new Promise((res, rej) => {
        const img = new Image();
        img.onload = () => res(img);
        img.onerror = rej;
        img.src = url;
    });
}

function screenToCanvas(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    return {
        x: (clientX - r.left) * (canvas.width  / r.width),
        y: (clientY - r.top)  * (canvas.height / r.height),
    };
}

function screenToImage(clientX, clientY) {
    const {x: cx, y: cy} = screenToCanvas(clientX, clientY);
    return { x: (cx - viewOffX) / viewScale, y: (cy - viewOffY) / viewScale };
}

// ── Viewport ───────────────────────────────────────────────
function ensureTmp(w, h) {
    if (tmpCanvas.width !== w || tmpCanvas.height !== h) {
        tmpCanvas.width = w; tmpCanvas.height = h;
    }
}

function fitToCanvas() {
    if (!targetImage) return;
    viewScale = Math.min(canvas.width / targetImage.width, canvas.height / targetImage.height);
    viewOffX  = (canvas.width  - targetImage.width  * viewScale) / 2;
    viewOffY  = (canvas.height - targetImage.height * viewScale) / 2;
}

function resizeCanvas() {
    const c = canvas.parentElement;
    canvas.width  = c.clientWidth  || 800;
    canvas.height = c.clientHeight || 600;
    fitToCanvas();
    render();
}

// ── Render ─────────────────────────────────────────────────
function render() {
    ctx.save();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.translate(viewOffX, viewOffY);
    ctx.scale(viewScale, viewScale);

    if (targetImage) {
        ctx.drawImage(targetImage, 0, 0);
        ensureTmp(targetImage.width, targetImage.height);

        for (let i = 0; i < 5; i++) {
            const mc = maskCanvases[i];
            if (!mc.width) continue;
            const [r, g, b] = hexToRgb(FINGER_COLORS_HEX[i]);
            const alpha = (i === currentFinger) ? maskOpacity : maskOpacity * 0.3;

            tmpCtx.clearRect(0, 0, tmpCanvas.width, tmpCanvas.height);
            tmpCtx.fillStyle = `rgb(${r},${g},${b})`;
            tmpCtx.fillRect(0, 0, tmpCanvas.width, tmpCanvas.height);
            tmpCtx.globalCompositeOperation = 'destination-in';
            tmpCtx.drawImage(mc, 0, 0);
            tmpCtx.globalCompositeOperation = 'source-over';

            ctx.globalAlpha = alpha;
            ctx.drawImage(tmpCanvas, 0, 0);
            ctx.globalAlpha = 1;
        }
    }

    // Brush cursor (image space, scales with zoom)
    if (cursorImgX >= 0) {
        const lw = 1.5 / viewScale;
        ctx.beginPath();
        ctx.arc(cursorImgX, cursorImgY, brushSize / 2, 0, Math.PI * 2);
        ctx.strokeStyle = isErasing ? '#ff8888' : '#fff';
        ctx.lineWidth = lw;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cursorImgX, cursorImgY, lw * 1.5, 0, Math.PI * 2);
        ctx.fillStyle = isErasing ? '#ff8888' : '#fff';
        ctx.fill();
    }

    ctx.restore();
}

// ── Mask I/O ───────────────────────────────────────────────
async function loadMask(finger) {
    const mc   = maskCanvases[finger];
    const mctx = mc.getContext('2d');
    mctx.clearRect(0, 0, mc.width, mc.height);
    try {
        const img = await loadImg(`/api/mask/${currentStem}/${finger}?t=${Date.now()}`);
        mctx.drawImage(img, 0, 0, mc.width, mc.height);
        // grayscale PNG (R=G=B=val, A=255) → white-on-alpha (R=G=B=255, A=val)
        const id = mctx.getImageData(0, 0, mc.width, mc.height);
        const d  = id.data;
        for (let i = 0; i < d.length; i += 4) {
            d[i+3] = d[i]; d[i] = d[i+1] = d[i+2] = 255;
        }
        mctx.putImageData(id, 0, 0);
    } catch(_) { /* no mask — stays blank */ }
}

async function saveFinger(finger) {
    const mc   = maskCanvases[finger];
    const mctx = mc.getContext('2d');
    // white-on-alpha → grayscale PNG (R=G=B=alpha, A=255)
    const id = mctx.getImageData(0, 0, mc.width, mc.height);
    const d  = id.data;
    for (let i = 0; i < d.length; i += 4) {
        const a = d[i+3];
        d[i] = d[i+1] = d[i+2] = a; d[i+3] = 255;
    }
    const save = document.createElement('canvas');
    save.width = mc.width; save.height = mc.height;
    save.getContext('2d').putImageData(id, 0, 0);
    const resp = await fetch(`/api/mask/${currentStem}/${finger}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ image: save.toDataURL('image/png') })
    });
    return resp.ok;
}

// ── Drawing ────────────────────────────────────────────────
function saveUndo() {
    const mc = maskCanvases[currentFinger];
    const id = mc.getContext('2d').getImageData(0, 0, mc.width, mc.height);
    const stack = undoStacks[currentFinger];
    stack.push(id);
    if (stack.length > MAX_UNDO) stack.shift();
}

function undo() {
    const stack = undoStacks[currentFinger];
    if (!stack.length) return;
    maskCanvases[currentFinger].getContext('2d').putImageData(stack.pop(), 0, 0);
    render();
}

function drawAt(x, y) {
    const mctx = maskCanvases[currentFinger].getContext('2d');
    mctx.globalCompositeOperation = isErasing ? 'destination-out' : 'source-over';
    mctx.fillStyle = 'white';
    mctx.beginPath();
    mctx.arc(x, y, brushSize / 2, 0, Math.PI * 2);
    mctx.fill();
    mctx.globalCompositeOperation = 'source-over';
}

function strokeTo(x, y) {
    const dx = x - lastX, dy = y - lastY;
    const steps = Math.max(1, Math.ceil(Math.sqrt(dx*dx + dy*dy) / (brushSize * 0.2)));
    for (let i = 1; i <= steps; i++) {
        drawAt(lastX + dx * i / steps, lastY + dy * i / steps);
    }
    lastX = x; lastY = y;
}

// ── Navigation / loading ───────────────────────────────────
async function loadPair(stem) {
    currentStem = stem;
    document.getElementById('img-name').textContent = stem;
    document.getElementById('img-counter').textContent = `${currentIdx + 1} / ${images.length}`;

    document.getElementById('control-img').src = `/api/image/control/${stem}`;
    targetImage = await loadImg(`/api/image/target/${stem}`);

    resizeCanvas();
    for (let i = 0; i < 5; i++) {
        maskCanvases[i].width  = targetImage.width;
        maskCanvases[i].height = targetImage.height;
        undoStacks[i].length   = 0;
    }

    await Promise.allSettled(Array.from({length: 5}, (_, i) => loadMask(i)));

    try {
        const r = await fetch(`/api/mask_status/${stem}`);
        maskStatus = await r.json();
    } catch(_) {
        maskStatus = Array(5).fill({saved: false, model: false});
    }

    updateFingerTabs();
    render();
}

async function navigate(dir) {
    currentIdx = (currentIdx + dir + images.length) % images.length;
    await loadPair(images[currentIdx]);
}

// ── UI helpers ─────────────────────────────────────────────
function updateFingerTabs() {
    const el = document.getElementById('finger-tabs');
    el.innerHTML = '';
    for (let i = 0; i < 5; i++) {
        const btn = document.createElement('button');
        btn.className = 'finger-btn' + (i === currentFinger ? ' active' : '');
        btn.style.borderColor = FINGER_COLORS_HEX[i];
        if (i === currentFinger) btn.style.background = FINGER_COLORS_HEX[i] + '33';
        const st = maskStatus[i] || {};
        btn.textContent = FINGER_LABELS[i] + (st.saved ? ' ✓' : st.model ? ' ·' : '');
        btn.onclick = () => { currentFinger = i; updateFingerTabs(); render(); };
        el.appendChild(btn);
    }
}

function setStatus(msg, color = '#4ade80') {
    const el = document.getElementById('save-status');
    el.style.color = color;
    el.textContent = msg;
    setTimeout(() => { el.textContent = ''; }, 2500);
}

function updateToolBtns() {
    document.getElementById('btn-paint').classList.toggle('active', !isErasing);
    document.getElementById('btn-erase').classList.toggle('active',  isErasing);
}

// ── Event listeners ────────────────────────────────────────
canvas.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    if (e.ctrlKey || e.metaKey) {
        isPanning = true;
        const {x, y} = screenToCanvas(e.clientX, e.clientY);
        panCX = x; panCY = y; panOX = viewOffX; panOY = viewOffY;
        canvas.style.cursor = 'grabbing';
        return;
    }
    isDrawing = true;
    saveUndo();
    const {x, y} = screenToImage(e.clientX, e.clientY);
    lastX = x; lastY = y;
    drawAt(x, y);
    render();
});

canvas.addEventListener('mousemove', e => {
    const img = screenToImage(e.clientX, e.clientY);
    cursorImgX = img.x; cursorImgY = img.y;
    if (isPanning) {
        const {x: cx, y: cy} = screenToCanvas(e.clientX, e.clientY);
        viewOffX = panOX + (cx - panCX);
        viewOffY = panOY + (cy - panCY);
        render();
        return;
    }
    if (isDrawing) strokeTo(img.x, img.y);
    render();
});

canvas.addEventListener('mouseup', () => {
    isDrawing = false;
    if (isPanning) { isPanning = false; canvas.style.cursor = 'none'; }
});

canvas.addEventListener('mouseleave', () => {
    isDrawing = false; isPanning = false;
    cursorImgX = -1; cursorImgY = -1;
    canvas.style.cursor = 'none';
    render();
});

canvas.addEventListener('contextmenu', e => e.preventDefault());

canvas.addEventListener('wheel', e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    const {x: cx, y: cy} = screenToCanvas(e.clientX, e.clientY);
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    viewOffX  = cx - (cx - viewOffX) * factor;
    viewOffY  = cy - (cy - viewOffY) * factor;
    viewScale = Math.max(0.05, Math.min(40, viewScale * factor));
    render();
}, { passive: false });

document.getElementById('btn-prev').onclick = () => navigate(-1);
document.getElementById('btn-next').onclick = () => navigate(1);

document.getElementById('btn-paint').onclick = () => { isErasing = false; updateToolBtns(); };
document.getElementById('btn-erase').onclick = () => { isErasing = true;  updateToolBtns(); };
document.getElementById('btn-clear').onclick = () => {
    saveUndo();
    const mc = maskCanvases[currentFinger];
    mc.getContext('2d').clearRect(0, 0, mc.width, mc.height);
    render();
};

document.getElementById('brush-size').oninput = e => {
    brushSize = +e.target.value;
    document.getElementById('brush-val').textContent = brushSize;
};

document.getElementById('opacity').oninput = e => {
    maskOpacity = +e.target.value / 100;
    document.getElementById('opacity-val').textContent = e.target.value;
    render();
};

document.getElementById('btn-save-finger').onclick = async () => {
    setStatus('Saving…', '#facc15');
    const ok = await saveFinger(currentFinger);
    const r  = await fetch(`/api/mask_status/${currentStem}`);
    maskStatus = await r.json();
    updateFingerTabs();
    setStatus(ok ? `✓ Finger ${currentFinger} saved` : '⚠ Failed', ok ? '#4ade80' : '#f87171');
};

document.getElementById('btn-save-all').onclick = async () => {
    setStatus('Saving all…', '#facc15');
    const results = await Promise.all(Array.from({length:5}, (_, i) => saveFinger(i)));
    const r = await fetch(`/api/mask_status/${currentStem}`);
    maskStatus = await r.json();
    updateFingerTabs();
    const ok = results.every(Boolean);
    setStatus(ok ? '✓ All 5 saved' : '⚠ Some failed', ok ? '#4ade80' : '#f87171');
};

document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); undo(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key === '0') { e.preventDefault(); fitToCanvas(); render(); return; }
    if (document.activeElement.tagName === 'INPUT') return;
    if (e.key === 'ArrowLeft')  navigate(-1);
    if (e.key === 'ArrowRight') navigate(1);
    if (e.key === 'p' || e.key === 'P') { isErasing = false; updateToolBtns(); }
    if (e.key === 'e' || e.key === 'E') { isErasing = true;  updateToolBtns(); }
    if (e.key >= '1' && e.key <= '5') { currentFinger = +e.key - 1; updateFingerTabs(); render(); }
});

new ResizeObserver(resizeCanvas).observe(canvas.parentElement);

// ── Init ───────────────────────────────────────────────────
(async () => {
    const r = await fetch('/api/images');
    images = await r.json();
    updateFingerTabs();
    if (images.length > 0) {
        await loadPair(images[0]);
    } else {
        document.getElementById('img-name').textContent = 'No images found';
    }
})();
