/* ─── STATE ──────────────────────────────────────── */
let currentSlide = 0;
const SLIDES = ['Upload a non-image file', 'Upload an image', 'Convert URL', 'Convert text'];
let currentMarkdown = '';
let currentImages = [];

/* ─── ELEMENTS ───────────────────────────────────── */
const landingPage    = document.getElementById('landingPage');
const converterPage  = document.getElementById('converterPage');
const startBtn       = document.getElementById('startBtn');
const backBtn        = document.getElementById('backBtn');
const themeToggle    = document.getElementById('themeToggle');

const slideLabel     = document.getElementById('slideLabel');
const slideNum       = document.getElementById('slideNum');
const prevBtn        = document.getElementById('prevBtn');
const nextBtn        = document.getElementById('nextBtn');
const dots           = document.querySelectorAll('.dot');
const slides         = document.querySelectorAll('.slide');

const fileDropZone   = document.getElementById('fileDropZone');
const fileInput      = document.getElementById('fileInput');
const imageDropZone  = document.getElementById('imageDropZone');
const imageFileInput = document.getElementById('imageFileInput');
const urlInput       = document.getElementById('urlInput');
const textInput      = document.getElementById('textInput');
const convertUrlBtn  = document.getElementById('convertUrlBtn');
const convertTextBtn = document.getElementById('convertTextBtn');

const markdownOutput = document.getElementById('markdownOutput');
const copyBtn        = document.getElementById('copyBtn');
const downloadBtn    = document.getElementById('downloadBtn');
const clearBtn       = document.getElementById('clearBtn');
const copyToast      = document.getElementById('copyToast');
const statusMsg      = document.getElementById('statusMsg');
const errorMsg       = document.getElementById('errorMsg');

// GitHub push feature
const githubBtn         = document.getElementById('githubBtn');
const filenameRow        = document.getElementById('filenameRow');
const filenameInput      = document.getElementById('filenameInput');
const githubResult       = document.getElementById('githubResult');
const githubUrlOutput    = document.getElementById('githubUrlOutput');
const copyGithubUrlBtn   = document.getElementById('copyGithubUrlBtn');

/* ─── THEME ──────────────────────────────────────── */
const savedTheme = localStorage.getItem('theme') || 'dark';
document.documentElement.setAttribute('data-theme', savedTheme);

themeToggle.addEventListener('click', () => {
    const curr = document.documentElement.getAttribute('data-theme');
    const next = curr === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
});

/* ─── PAGE NAV ───────────────────────────────────── */
startBtn.addEventListener('click', () => {
    landingPage.classList.remove('active');
    converterPage.classList.add('active');
});

backBtn.addEventListener('click', () => {
    converterPage.classList.remove('active');
    landingPage.classList.add('active');
});

/* ─── CAROUSEL ───────────────────────────────────── */
function goToSlide(idx, direction = 1) {
    // Exit current
    slides[currentSlide].classList.remove('active');
    slides[currentSlide].classList.add(direction > 0 ? 'exit-left' : 'exit-right');

    setTimeout(() => {
        slides[currentSlide].classList.remove('exit-left', 'exit-right');
        currentSlide = idx;

        // Enter new
        slides[currentSlide].classList.add('active');
        dots.forEach((d, i) => d.classList.toggle('active', i === currentSlide));
        slideLabel.textContent = SLIDES[currentSlide];
        slideNum.textContent = currentSlide + 1;
        clearMessages();
    }, 50);
}

prevBtn.addEventListener('click', () => {
    const next = (currentSlide - 1 + SLIDES.length) % SLIDES.length;
    goToSlide(next, -1);
});

nextBtn.addEventListener('click', () => {
    const next = (currentSlide + 1) % SLIDES.length;
    goToSlide(next, 1);
});

dots.forEach(dot => {
    dot.addEventListener('click', () => {
        const idx = parseInt(dot.dataset.idx);
        if (idx === currentSlide) return;
        goToSlide(idx, idx > currentSlide ? 1 : -1);
    });
});

// Keyboard nav
document.addEventListener('keydown', e => {
    if (!converterPage.classList.contains('active')) return;
    if (document.activeElement.tagName === 'TEXTAREA' || document.activeElement.tagName === 'INPUT') return;
    if (e.key === 'ArrowLeft')  prevBtn.click();
    if (e.key === 'ArrowRight') nextBtn.click();
});

/* ─── FILE DROP ZONE ─────────────────────────────── */
fileDropZone.addEventListener('click', () => fileInput.click());

fileDropZone.addEventListener('dragover', e => {
    e.preventDefault();
    fileDropZone.classList.add('over');
});
fileDropZone.addEventListener('dragleave', () => fileDropZone.classList.remove('over'));
fileDropZone.addEventListener('drop', e => {
    e.preventDefault();
    fileDropZone.classList.remove('over');
    const file = e.dataTransfer.files[0];
    if (file) handleFileUpload(file);
});

fileInput.addEventListener('change', e => {
    if (e.target.files[0]) handleFileUpload(e.target.files[0]);
});

/* ─── IMAGE DROP ZONE ────────────────────────────── */
imageDropZone.addEventListener('click', () => imageFileInput.click());

imageDropZone.addEventListener('dragover', e => {
    e.preventDefault();
    imageDropZone.classList.add('over');
});
imageDropZone.addEventListener('dragleave', () => imageDropZone.classList.remove('over'));
imageDropZone.addEventListener('drop', e => {
    e.preventDefault();
    imageDropZone.classList.remove('over');
    const file = e.dataTransfer.files[0];
    if (file) handleImageUpload(file);
});

imageFileInput.addEventListener('change', e => {
    if (e.target.files[0]) handleImageUpload(e.target.files[0]);
});

/* ─── CONVERT: FILE ──────────────────────────────── */
async function handleFileUpload(file) {
    clearMessages();
    showStatus(`Converting "${file.name}"…`);

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res  = await fetch('/api/convert-file', { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) { showError(data.error || 'Conversion failed'); return; }
        setOutput(data.markdown, file.name, data.images || []);
        if (currentImages.length > 0) {
            showStatus(`Converted — found ${currentImages.length} embedded image(s). They'll upload alongside the markdown when you push to GitHub.`);
        } else {
            clearMessages();
        }
    } catch (err) {
        showError('Upload failed: ' + err.message);
    }
}

/* ─── CONVERT: IMAGE ─────────────────────────────── */
async function handleImageUpload(file) {
    clearMessages();
    showStatus(`Reading "${file.name}"…`);

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res  = await fetch('/api/convert-image', { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) { showError(data.error || 'Image conversion failed'); return; }
        setOutput(data.markdown, file.name, data.images || []);
        showStatus('Ready — click "Push to GitHub" to upload the actual image and get its live URL.');
    } catch (err) {
        showError('Image upload failed: ' + err.message);
    }
}

/* ─── CONVERT: URL ───────────────────────────────── */
function isVideoUrl(url) {
    // Basic check for YouTube and common video domains
    return url.includes('youtube.com') ||
           url.includes('youtu.be') ||
           url.includes('vimeo.com') ||
           url.includes('dailymotion.com') ||
           url.includes('twitch.tv');
}

convertUrlBtn.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    if (!url) { showError('Paste a URL first.'); return; }

    clearMessages();
    showStatus('Fetching and converting…');

    let apiUrl = '/api/convert-url';
    if (isVideoUrl(url)) {
        apiUrl = '/api/convert-video-url';
        showStatus('Fetching video transcript and converting…');
    }

    try {
        const res  = await fetch(apiUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        const data = await res.json();
        if (!res.ok) { showError(data.error || 'Conversion failed'); return; }
        setOutput(data.markdown, null, []);
        clearMessages();
    } catch (err) {
        showError('Request failed: ' + err.message);
    }
});

/* ─── CONVERT: TEXT ──────────────────────────────── */
convertTextBtn.addEventListener('click', async () => {
    const text = textInput.value.trim();
    if (!text) { showError('Paste some text first.'); return; }

    clearMessages();
    showStatus('Processing text…');

    try {
        const res  = await fetch('/api/convert-text', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        const data = await res.json();
        if (!res.ok) { showError(data.error || 'Processing failed'); return; }
        setOutput(data.markdown, null, []);
        clearMessages();
    } catch (err) {
        showError('Request failed: ' + err.message);
    }
});

/* ─── OUTPUT ─────────────────────────────────────── */
function setOutput(md, sourceName, images) {
    currentMarkdown = md;
    currentImages = images || [];
    markdownOutput.value = md;
    [copyBtn, downloadBtn, githubBtn, clearBtn].forEach(b => b.style.display = 'inline-block');
    filenameRow.style.display = 'block';

    // Pre-fill filename from source file name if we have one
    if (sourceName) {
        const base = sourceName.replace(/\.[^/.]+$/, '').replace(/[^a-zA-Z0-9-_]/g, '-');
        filenameInput.value = `${base}.md`;
    } else {
        filenameInput.value = 'converted.md';
    }

    githubResult.style.display = 'none';
    githubUrlOutput.value = '';
}

copyBtn.addEventListener('click', async () => {
    try {
        await navigator.clipboard.writeText(currentMarkdown);
        copyToast.classList.add('show');
        setTimeout(() => copyToast.classList.remove('show'), 1800);
    } catch {
        showError('Copy failed — select the text manually.');
    }
});

downloadBtn.addEventListener('click', () => {
    const blob = new Blob([currentMarkdown], { type: 'text/markdown' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = filenameInput.value.trim() || 'converted.md';
    document.body.appendChild(a);
    a.click();
    URL.revokeObjectURL(url);
    a.remove();
});

/* ─── PUSH TO GITHUB ─────────────────────────────── */
githubBtn.addEventListener('click', async () => {
    if (!currentMarkdown && currentImages.length === 0) return;

    const filename = filenameInput.value.trim() || 'converted.md';

    clearMessages();
    githubResult.style.display = 'none';
    showStatus(currentImages.length > 0
        ? `Pushing the actual image${currentImages.length > 1 ? 's' : ''} + markdown to GitHub…`
        : 'Pushing to GitHub…');

    try {
        const res = await fetch('/api/push-to-github', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ markdown: currentMarkdown, filename, images: currentImages })
        });
        const data = await res.json();
        if (!res.ok) { showError(data.error || 'Push to GitHub failed'); return; }

        githubUrlOutput.value = data.raw_url;
        githubResult.style.display = 'block';
        clearMessages();
        showStatus(data.images_pushed
            ? `Pushed to GitHub ✓ (${data.images_pushed} real image(s) included — open the URL to see it)`
            : 'Pushed to GitHub ✓');
    } catch (err) {
        showError('Push failed: ' + err.message);
    }
});

copyGithubUrlBtn.addEventListener('click', async () => {
    if (!githubUrlOutput.value) return;
    try {
        await navigator.clipboard.writeText(githubUrlOutput.value);
        copyToast.classList.add('show');
        setTimeout(() => copyToast.classList.remove('show'), 1800);
    } catch {
        showError('Copy failed — select the URL manually.');
    }
});

/* ─── CLEAR ──────────────────────────────────────── */
clearBtn.addEventListener('click', () => {
    currentMarkdown     = '';
    currentImages       = [];
    markdownOutput.value = '';
    fileInput.value      = '';
    imageFileInput.value = '';
    urlInput.value       = '';
    textInput.value      = '';
    filenameInput.value  = 'converted.md';
    githubUrlOutput.value = '';
    [copyBtn, downloadBtn, githubBtn, clearBtn].forEach(b => b.style.display = 'none');
    filenameRow.style.display = 'none';
    githubResult.style.display = 'none';
    clearMessages();
});

/* ─── MESSAGES ───────────────────────────────────── */
function showStatus(msg) {
    statusMsg.textContent = msg;
    statusMsg.style.display = 'block';
    errorMsg.style.display  = 'none';
}
function showError(msg) {
    errorMsg.textContent   = msg;
    errorMsg.style.display = 'block';
    statusMsg.style.display = 'none';
}
function clearMessages() {
    statusMsg.style.display = 'none';
    errorMsg.style.display  = 'none';
}