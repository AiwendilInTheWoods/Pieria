/**
 * Artwork Display Engine - Frontend Client (app.js)
 * Phase 2 Polish: Dynamic Timing, Static Crop, and Manual Navigation.
 */

const API_BASE = (window.location.origin === 'null' || window.location.protocol === 'file:') 
    ? 'http://localhost:8000' 
    : window.location.origin;

let currentPlaylist = '';
let currentImageIndex = null;
let activeLayerId = 1;
let firstLoad = true;
let displayMode = 'ken-burns'; 
let controlsTimeout = null;
let currentImageUrl = '';
let currentDisplayTime = 30000; 
let currentCropData = null;
let cycleTimeout = null;

/**
 * Main entry point.
 */
async function init() {
    console.log(`[Client] Initializing Phase 2 Engine. API Base: ${API_BASE}`);
    setupControlVisibility();
    initModeToggles();
    initNavButtons();

    try {
        const response = await fetch(`${API_BASE}/playlists`);
        const playlists = await response.json();
        
        if (playlists.length > 0) {
            populatePlaylistSelect(playlists);
            const activePlaylist = playlists.find(p => (p.artworks?.length || 0) > 0) || playlists[0];
            currentPlaylist = activePlaylist.name;
            currentDisplayTime = activePlaylist.display_time * 1000;
            
            document.getElementById('playlist-select').value = currentPlaylist;
            showPlaylistTitle(currentPlaylist);
            startDisplayCycle();
        }
    } catch (error) {
        console.error('[Client] Init failed:', error);
    }
}

/**
 * Cycle logic using recursive setTimeout.
 */
async function startDisplayCycle() {
    await fetchAndTransition(1); // Default direction is Forward
    
    // Clear any existing timeout to prevent double-cycling
    if (cycleTimeout) clearTimeout(cycleTimeout);
    cycleTimeout = setTimeout(startDisplayCycle, currentDisplayTime);
}

/**
 * Fetches next/prev image and metadata.
 * @param {number} direction 1 for next, -1 for previous.
 */
async function fetchAndTransition(direction = 1, isManual = false) {
    if (!currentPlaylist) return;

    try {
        const params = new URLSearchParams({ 
            playlist_name: currentPlaylist,
            direction: direction,
            // Honor DB order by disabling shuffle if manual, or add a toggle later
            shuffle: isManual ? 'false' : 'false' 
        });
        if (currentImageIndex !== null) params.append('current_index', currentImageIndex);

        const response = await fetch(`${API_BASE}/next-image?${params.toString()}`);
        const data = await response.json();

        currentImageIndex = data.index;
        currentImageUrl = `${API_BASE}${data.image_url}`;
        currentCropData = data.crop;
        
        if (data.display_time) {
            currentDisplayTime = data.display_time * 1000;
        }
        
        performCrossfade(currentImageUrl, data.crop);
    } catch (error) {
        console.error('[Client] Transition Error:', error);
    }
}

/**
 * Manages the double-buffer crossfade transition.
 */
function performCrossfade(imageUrl, cropData) {
    const targetLayerId = activeLayerId === 1 ? 2 : 1;
    const activeLayer = document.getElementById(`artwork-${activeLayerId}`);
    const targetLayer = document.getElementById(`artwork-${targetLayerId}`);
    const matteLayer = document.getElementById('matte-layer');

    const img = new Image();
    img.src = imageUrl;
    
    img.onload = () => {
        if (displayMode === 'contain-matte') {
            matteLayer.style.backgroundImage = `url('${imageUrl}')`;
        }

        targetLayer.style.backgroundImage = `url('${imageUrl}')`;
        applyModeStyles(targetLayer, img, cropData);

        targetLayer.classList.add('active');
        activeLayer.classList.remove('active');
        activeLayerId = targetLayerId;
        firstLoad = false;
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
    } else if (displayMode === 'static-crop' || displayMode === 'ken-burns') {
        element.style.backgroundSize = 'cover';
        element.style.backgroundPosition = 'center';
    } else if (displayMode === 'contain-matte') {
        element.style.backgroundSize = 'contain';
        element.style.backgroundPosition = 'center';
        element.style.transform = 'none';
    }
}

function initNavButtons() {
    document.getElementById('prev-btn').addEventListener('click', () => {
        // Reset cycle timer on manual interaction
        if (cycleTimeout) clearTimeout(cycleTimeout);
        fetchAndTransition(-1);
        cycleTimeout = setTimeout(startDisplayCycle, currentDisplayTime);
    });

    document.getElementById('next-btn').addEventListener('click', () => {
        if (cycleTimeout) clearTimeout(cycleTimeout);
        fetchAndTransition(1);
        cycleTimeout = setTimeout(startDisplayCycle, currentDisplayTime);
    });
}

function initModeToggles() {
    const modeButtons = {
        'ken-burns': document.getElementById('mode-a'),
        'static-crop': document.getElementById('mode-b'),
        'contain-matte': document.getElementById('mode-c')
    };
    Object.entries(modeButtons).forEach(([mode, btn]) => {
        btn.addEventListener('click', () => {
            setMode(mode);
            Object.values(modeButtons).forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
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

function populatePlaylistSelect(playlists) {
    const select = document.getElementById('playlist-select');
    playlists.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = `${p.name} (${p.artworks?.length || 0})`;
        select.appendChild(opt);
    });
    select.addEventListener('change', (e) => {
        currentPlaylist = e.target.value;
        const p = playlists.find(pl => pl.name === currentPlaylist);
        if (p) currentDisplayTime = p.display_time * 1000;
        currentImageIndex = null;
        showPlaylistTitle(currentPlaylist);
        if (cycleTimeout) clearTimeout(cycleTimeout);
        startDisplayCycle();
    });
}

function setupControlVisibility() {
    const controls = document.getElementById('controls');
    document.addEventListener('mousemove', () => {
        controls.classList.add('visible');
        clearTimeout(controlsTimeout);
        controlsTimeout = setTimeout(() => {
            if (!controls.matches(':hover')) controls.classList.remove('visible');
        }, 3000);
    });
}

function showPlaylistTitle(title) {
    const overlay = document.getElementById('overlay');
    const titleEl = document.getElementById('playlist-title');
    titleEl.textContent = title;
    overlay.classList.add('show');
    setTimeout(() => overlay.classList.remove('show'), 5000);
}

document.addEventListener('DOMContentLoaded', init);
