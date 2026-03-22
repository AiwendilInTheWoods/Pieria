/**
 * Artwork Display Engine - Frontend Client (app.js)
 * Phase 4: Targeted WebSocket Routing for Multiple Displays.
 */

// 1. Digital Signage Rotation Logic
const urlParams = new URLSearchParams(window.location.search);
if (urlParams.get('rotate') === 'true') {
    document.body.classList.add('force-portrait');
}

// 2. True Fullscreen Trigger
document.addEventListener('click', () => {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(err => {
            console.warn(`[Client] Fullscreen failed: ${err.message}`);
        });
    }
}, { once: false });

const API_BASE = (window.location.origin === 'null' || window.location.protocol === 'file:') 
    ? 'http://localhost:8000' 
    : window.location.origin;

// 3. Targeted WebSocket Endpoint
// Connects to /ws/[display_id] based on ?display= URL parameter
const DISPLAY_ID = urlParams.get('display') || 'default';
const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/${DISPLAY_ID}`;

// Global Defaults & URL Overrides
const globalConfig = {
    cycle_time: parseInt(urlParams.get('cycle_time')) || null,
    mode: urlParams.get('mode') || null,
    placard_wait: parseInt(urlParams.get('placard_wait')) || null,
    placard_show: parseInt(urlParams.get('placard_show')) || null,
    placard_manual: parseInt(urlParams.get('placard_manual')) || null,
    shuffle: urlParams.get('shuffle') !== null ? urlParams.get('shuffle') === 'true' : null
};

const DEFAULT_SETTINGS = {
    cycle_time: 30,
    mode: 'ken-burns',
    shuffle: false,
    placard_wait: 5,
    placard_show: 15,
    placard_manual: 10
};

let currentPlaylist = '';
let currentImageIndex = null;
let activeLayerId = 1;
let firstLoad = true;
let displayMode = 'ken-burns'; 
let placardTimeout = null;
let controlsTimeout = null;
let currentImageUrl = '';
let currentDisplayTime = 30000; 
let currentCropData = null;
let cycleTimeout = null;
let currentPlaylists = [];
let socket = null;

async function init() {
    console.log(`[Client] Initializing Display: ${DISPLAY_ID}. API: ${API_BASE}`);
    
    const requestedMode = urlParams.get('mode');
    const validModes = ['ken-burns', 'static-crop', 'contain-matte'];
    if (requestedMode && validModes.includes(requestedMode)) {
        displayMode = requestedMode;
    }

    setupUIInteraction();
    initModeToggles();
    initNavButtons();
    initCustomDropdown();
    connectWS(); 

    await refreshPlaylists(true);
    setInterval(() => refreshPlaylists(false), 15000);
}

/**
 * Initializes Targeted WebSocket connection.
 */
function connectWS() {
    console.log(`[Client] Connecting to Remote Hub at: ${WS_URL}`);
    socket = new WebSocket(WS_URL);

    socket.onmessage = async (event) => {
        try {
            const msg = JSON.parse(event.data);
            console.log('[Client] Command Received:', msg);

            switch (msg.action) {
                case 'set_playlist':
                    handleRemotePlaylistSwitch(msg.playlist);
                    break;
                case 'set_mode':
                    if (msg.mode) {
                        setMode(msg.mode);
                        updateModeButtonUI();
                    }
                    break;
                case 'next_image':
                    startDisplayCycleManually(1);
                    break;
                case 'prev_image':
                    startDisplayCycleManually(-1);
                    break;
                case 'show_placard':
                    if (placardTimeout) clearTimeout(placardTimeout);
                    const manualShowTime = globalConfig.placard_manual !== null ? globalConfig.placard_manual : (currentPlaylistData?.placard_manual !== undefined ? currentPlaylistData.placard_manual : DEFAULT_SETTINGS.placard_manual);
                    showPlacard(manualShowTime * 1000);
                    break;
                default:
                    console.warn('[Client] Unknown action:', msg.action);
            }
        } catch (err) {
            console.error('[Client] Message Parse Error:', err);
        }
    };

    socket.onclose = () => {
        console.warn('[Client] Hub connection lost. Retrying in 5s...');
        setTimeout(connectWS, 5000);
    };
}

async function handleRemotePlaylistSwitch(name) {
    const p = currentPlaylists.find(pl => pl.name === name);
    if (!p) return;

    console.log(`[Client] Targeted Switch: ${name}`);
    currentPlaylist = name;
    currentDisplayTime = p.display_time * 1000;
    currentImageIndex = null; 
    
    updateDropdownLabel(p.name, p.artworks?.length || 0);
    showPlaylistTitle(currentPlaylist);
    startDisplayCycle();
}

/**
 * UI & Interaction Logic
 */
function setupUIInteraction() {
    const getManualTime = () => {
        return (globalConfig.placard_manual !== null ? globalConfig.placard_manual : (currentPlaylistData?.placard_manual !== undefined ? currentPlaylistData.placard_manual : DEFAULT_SETTINGS.placard_manual)) * 1000;
    };

    document.addEventListener('mousemove', (e) => {
        showPlacard(getManualTime());
        const isRotated = document.body.classList.contains('force-portrait');
        if (isRotated) {
            const threshold = window.innerWidth * 0.7; 
            if (e.clientX > threshold) showControls(10000);
        } else {
            const threshold = window.innerHeight * 0.7;
            if (e.clientY > threshold) showControls(10000);
        }
    });

    document.addEventListener('mousedown', (e) => {
        showPlacard(getManualTime());
        const isRotated = document.body.classList.contains('force-portrait');
        if (isRotated) {
            const threshold = window.innerWidth * 0.7;
            if (e.clientX > threshold) showControls(10000);
        } else {
            const threshold = window.innerHeight * 0.7;
            if (e.clientY > threshold) showControls(10000);
        }
    });
}

function showPlacard(duration) {
    document.body.classList.add('placard-visible');
    if (placardTimeout) clearTimeout(placardTimeout);
    placardTimeout = setTimeout(() => { document.body.classList.remove('placard-visible'); }, duration);
}

function showControls(duration) {
    document.body.classList.add('controls-visible');
    if (controlsTimeout) clearTimeout(controlsTimeout);
    controlsTimeout = setTimeout(() => {
        const options = document.getElementById('playlist-options');
        const isOptionsOpen = !options.classList.contains('hidden');
        const controls = document.getElementById('controls');
        const isHovering = controls.matches(':hover');
        if (!isOptionsOpen && !isHovering) {
            document.body.classList.remove('controls-visible');
        } else {
            showControls(2000); 
        }
    }, duration);
}

function initCustomDropdown() {
    const trigger = document.getElementById('playlist-current');
    const options = document.getElementById('playlist-options');
    trigger.addEventListener('click', (e) => { e.stopPropagation(); options.classList.toggle('hidden'); });
    document.addEventListener('click', () => { options.classList.add('hidden'); });
}

async function refreshPlaylists(isInitial = false) {
    try {
        const response = await fetch(`${API_BASE}/playlists`);
        const playlists = await response.json();
        if (playlists.length > 0) {
            currentPlaylists = playlists;
            populatePlaylistSelect(playlists);
            if (isInitial) {
                const requestedPlaylistName = urlParams.get('playlist');
                let activePlaylist = playlists.find(p => p.name === requestedPlaylistName);
                if (!activePlaylist) {
                    activePlaylist = playlists.find(p => (p.artworks?.length || 0) > 0) || playlists[0];
                }
                currentPlaylist = activePlaylist.name;
                currentDisplayTime = activePlaylist.display_time * 1000;
                updateDropdownLabel(activePlaylist.name, activePlaylist.artworks?.length || 0);
                updateModeButtonUI();
                showPlaylistTitle(currentPlaylist);
                startDisplayCycle();
            }
        }
    } catch (error) { console.error('[Client] Sync Failed:', error); }
}

function updateModeButtonUI() {
    const modeMap = { 'ken-burns': 'mode-a', 'static-crop': 'mode-b', 'contain-matte': 'mode-c' };
    const activeBtnId = modeMap[displayMode];
    document.querySelectorAll('.mode-toggles button').forEach(btn => btn.classList.remove('active'));
    const btn = document.getElementById(activeBtnId);
    if (btn) btn.classList.add('active');
    document.getElementById('display-container').className = displayMode;
}

function updateDropdownLabel(name, count) {
    document.getElementById('playlist-current').textContent = `${name} (${count})`;
}

function populatePlaylistSelect(playlists) {
    const optionsContainer = document.getElementById('playlist-options');
    optionsContainer.innerHTML = '';
    playlists.forEach(p => {
        const div = document.createElement('div');
        div.className = `dropdown-option ${p.name === currentPlaylist ? 'active' : ''}`;
        div.textContent = `${p.name} (${p.artworks?.length || 0})`;
        div.onclick = (e) => {
            e.stopPropagation();
            currentPlaylist = p.name;
            currentDisplayTime = p.display_time * 1000;
            currentImageIndex = null;
            updateDropdownLabel(p.name, p.artworks?.length || 0);
            optionsContainer.classList.add('hidden');
            showPlaylistTitle(currentPlaylist);
            startDisplayCycle();
            document.querySelectorAll('.dropdown-option').forEach(el => el.classList.remove('active'));
            div.classList.add('active');
        };
        optionsContainer.appendChild(div);
    });
}

async function startDisplayCycle() {
    if (cycleTimeout) clearTimeout(cycleTimeout);
    await fetchAndTransition(1); 
    cycleTimeout = setTimeout(startDisplayCycle, currentDisplayTime);
}

let currentPlaylistData = null;

async function fetchAndTransition(direction = 1) {
    if (!currentPlaylist) return;
    try {
        // Resolve Shuffle Hierarchy (URL > Playlist > Global Default)
        // We need to fetch once without shuffle to get the playlist default if URL is null
        // But the API already handles current_index and shuffle logic.
        // We'll peek at currentPlaylistData if we have it, or just use globalConfig.
        const resolvedShuffle = globalConfig.shuffle !== null ? globalConfig.shuffle : (currentPlaylistData?.shuffle !== undefined ? currentPlaylistData.shuffle : DEFAULT_SETTINGS.shuffle);

        const params = new URLSearchParams({ 
            playlist_name: currentPlaylist, 
            direction: direction, 
            shuffle: resolvedShuffle.toString() 
        });
        if (currentImageIndex !== null && currentImageIndex !== undefined) params.append('current_index', currentImageIndex);
        const response = await fetch(`${API_BASE}/next-image?${params.toString()}`);
        if (!response.ok) throw new Error('No approved images');
        const data = await response.json();
        
        currentPlaylistData = data;
        currentImageIndex = data.index;
        currentImageUrl = `${API_BASE}${data.image_url}`;
        currentCropData = data.crop;
        
        // Resolve Settings Hierarchy (URL > Playlist > Global Default)
        const cycleTime = globalConfig.cycle_time || data.display_time || DEFAULT_SETTINGS.cycle_time;
        const resolvedMode = globalConfig.mode || data.default_mode || DEFAULT_SETTINGS.mode;
        
        currentDisplayTime = cycleTime * 1000;
        if (displayMode !== resolvedMode) {
            setMode(resolvedMode);
            updateModeButtonUI();
        }

        updatePlacard(data.metadata);
        performCrossfade(currentImageUrl, data.crop);

        // Automatic Placard Flow
        const waitTime = globalConfig.placard_wait !== null ? globalConfig.placard_wait : (data.placard_wait !== undefined ? data.placard_wait : DEFAULT_SETTINGS.placard_wait);
        const showTime = globalConfig.placard_show !== null ? globalConfig.placard_show : (data.placard_show !== undefined ? data.placard_show : DEFAULT_SETTINGS.placard_show);
        
        showPlacardFlow(waitTime, showTime);

    } catch (error) { console.error('[Client] Transition Error:', error.message); }
}

function showPlacardFlow(waitSec, showSec) {
    if (placardTimeout) clearTimeout(placardTimeout);
    document.body.classList.remove('placard-visible');
    
    placardTimeout = setTimeout(() => {
        document.body.classList.add('placard-visible');
        placardTimeout = setTimeout(() => {
            document.body.classList.remove('placard-visible');
        }, showSec * 1000);
    }, waitSec * 1000);
}

function updatePlacard(metadata) {
    const placard = document.getElementById('placard');
    if (!metadata || !metadata.title) { placard.classList.add('hidden'); return; }
    placard.classList.remove('hidden');
    document.getElementById('art-title').textContent = metadata.title;
    document.getElementById('art-artist-year').textContent = `${metadata.artist || 'Unknown Artist'} ${metadata.year ? '• ' + metadata.year : ''}`;
    document.getElementById('art-description').textContent = metadata.description || '';
    const tagsContainer = document.getElementById('art-tags');
    tagsContainer.innerHTML = '';
    if (metadata.tags) {
        metadata.tags.split(',').forEach(tag => {
            const span = document.createElement('span');
            span.textContent = tag.trim();
            tagsContainer.appendChild(span);
        });
    }
    const qrEl = document.getElementById('qrcode');
    qrEl.innerHTML = '';
    new QRCode(qrEl, {
        text: `https://www.google.com/search?q=${encodeURIComponent(metadata.artist + " " + metadata.title)}`,
        width: 80, height: 80, colorDark : "#000000", colorLight : "#ffffff", correctLevel : QRCode.CorrectLevel.H
    });
}

function performCrossfade(imageUrl, cropData) {
    const targetLayerId = activeLayerId === 1 ? 2 : 1;
    const activeLayer = document.getElementById(`artwork-${activeLayerId}`);
    const targetLayer = document.getElementById(`artwork-${targetLayerId}`);
    const img = new Image();
    img.src = imageUrl;
    img.onload = () => {
        const matteLayer = document.getElementById('matte-layer');
        if (displayMode === 'contain-matte') matteLayer.style.backgroundImage = `url('${imageUrl}')`;
        targetLayer.style.backgroundImage = `url('${imageUrl}')`;
        applyModeStyles(targetLayer, img, cropData);
        targetLayer.classList.add('active');
        activeLayer.classList.remove('active');
        activeLayerId = targetLayerId;
        firstLoad = false;
        document.body.classList.remove('controls-visible');
    };
}

function applyModeStyles(element, img, cropData) {
    const hasValidCrop = cropData && cropData.width > 1;
    if (displayMode === 'static-crop' && hasValidCrop) {
        const zoomX = (img.naturalWidth / cropData.width) * 100;
        const zoomY = (img.naturalHeight / cropData.height) * 100;
        const posX = (cropData.x / (img.naturalWidth - cropData.width)) * 100 || 0;
        const posY = (cropData.y / (img.naturalHeight - cropData.height)) * 100 || 0;
        element.style.backgroundSize = `${zoomX}% ${zoomY}%`;
        element.style.backgroundPosition = `${posX}% ${posY}%`;
        element.style.transform = 'none';
    } else {
        element.style.backgroundSize = displayMode === 'contain-matte' ? 'contain' : 'cover';
        element.style.backgroundPosition = 'center';
        element.style.transform = 'none';
    }
}

function initNavButtons() {
    document.getElementById('prev-btn').addEventListener('click', () => startDisplayCycleManually(-1));
    document.getElementById('next-btn').addEventListener('click', () => startDisplayCycleManually(1));
}

async function startDisplayCycleManually(direction) {
    if (cycleTimeout) clearTimeout(cycleTimeout);
    await fetchAndTransition(direction);
    cycleTimeout = setTimeout(startDisplayCycle, currentDisplayTime);
}

function initModeToggles() {
    const modeButtons = { 'ken-burns': document.getElementById('mode-a'), 'static-crop': document.getElementById('mode-b'), 'contain-matte': document.getElementById('mode-c') };
    Object.entries(modeButtons).forEach(([mode, btn]) => {
        btn.addEventListener('click', () => { setMode(mode); Object.values(modeButtons).forEach(b => b.classList.remove('active')); btn.classList.add('active'); });
    });
}

function setMode(mode) {
    displayMode = mode;
    document.getElementById('display-container').className = mode;
    const activeLayer = document.getElementById(`artwork-${activeLayerId}`);
    const activeImg = new Image();
    const urlMatch = activeLayer.style.backgroundImage.match(/url\(['"]?(.*?)['"]?\)/);
    if (urlMatch && urlMatch[1]) {
        activeImg.src = urlMatch[1];
        activeImg.onload = () => applyModeStyles(activeLayer, activeImg, currentCropData);
    }
    const matteLayer = document.getElementById('matte-layer');
    if (mode === 'contain-matte') {
        matteLayer.classList.remove('hidden');
        if (currentImageUrl) matteLayer.style.backgroundImage = `url('${currentImageUrl}')`;
    } else {
        matteLayer.classList.add('hidden');
    }
}

function showPlaylistTitle(title) {
    const overlay = document.getElementById('overlay');
    const titleEl = document.getElementById('playlist-title');
    titleEl.textContent = title;
    overlay.classList.add('show');
    setTimeout(() => overlay.classList.remove('show'), 5000);
}

document.addEventListener('DOMContentLoaded', init);
