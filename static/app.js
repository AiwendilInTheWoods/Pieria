/**
 * Artwork Display Engine - Frontend Client (app.js)
 * Phase 3: Dynamic Timing, Static Crop, Manual Navigation, Museum Placard, and Custom Dropdown.
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
let currentPlaylists = [];

async function init() {
    console.log(`[Client] Initializing Engine. API: ${API_BASE}`);
    setupControlVisibility();
    initModeToggles();
    initNavButtons();
    initCustomDropdown();

    await refreshPlaylists(true);
    setInterval(() => refreshPlaylists(false), 15000);
}

/**
 * Initializes the toggle logic for the custom dropdown.
 */
function initCustomDropdown() {
    const trigger = document.getElementById('playlist-current');
    const options = document.getElementById('playlist-options');

    trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        options.classList.toggle('hidden');
    });

    // Close dropdown when clicking elsewhere
    document.addEventListener('click', () => {
        options.classList.add('hidden');
    });
}

async function refreshPlaylists(isInitial = false) {
    try {
        const response = await fetch(`${API_BASE}/playlists`);
        const playlists = await response.json();
        
        if (playlists.length > 0) {
            currentPlaylists = playlists;
            populatePlaylistSelect(playlists);
            
            if (isInitial) {
                const activePlaylist = playlists.find(p => (p.artworks?.length || 0) > 0) || playlists[0];
                currentPlaylist = activePlaylist.name;
                currentDisplayTime = activePlaylist.display_time * 1000;
                
                updateDropdownLabel(activePlaylist.name, activePlaylist.artworks?.length || 0);
                showPlaylistTitle(currentPlaylist);
                startDisplayCycle();
            }
        }
    } catch (error) { console.error('[Client] Playlist Sync Failed:', error); }
}

function updateDropdownLabel(name, count) {
    const trigger = document.getElementById('playlist-current');
    trigger.textContent = `${name} (${count})`;
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
            
            // Mark active
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

async function fetchAndTransition(direction = 1) {
    if (!currentPlaylist) return;
    try {
        const params = new URLSearchParams({ playlist_name: currentPlaylist, direction: direction, shuffle: 'false' });
        if (currentImageIndex !== null && currentImageIndex !== undefined) params.append('current_index', currentImageIndex);

        const response = await fetch(`${API_BASE}/next-image?${params.toString()}`);
        if (!response.ok) throw new Error('No approved images');
        
        const data = await response.json();
        currentImageIndex = data.index;
        currentImageUrl = `${API_BASE}${data.image_url}`;
        currentCropData = data.crop;
        if (data.display_time) currentDisplayTime = data.display_time * 1000;
        
        updatePlacard(data.metadata);
        performCrossfade(currentImageUrl, data.crop);
    } catch (error) { console.error('[Client] Transition Error:', error.message); }
}

function showUI(duration) {
    const uiWrapper = document.getElementById('ui-wrapper');
    uiWrapper.classList.add('visible');
    if (controlsTimeout) clearTimeout(controlsTimeout);
    controlsTimeout = setTimeout(() => {
        const options = document.getElementById('playlist-options');
        const isOptionsOpen = !options.classList.contains('hidden');
        if (!isOptionsOpen) uiWrapper.classList.remove('visible');
        else showUI(2000);
    }, duration);
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
        setTimeout(() => { showUI(15000); }, 5000);
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

function setupControlVisibility() {
    document.addEventListener('mousemove', () => { showUI(10000); });
    document.addEventListener('mousedown', () => { showUI(10000); });
}

function showPlaylistTitle(title) {
    const overlay = document.getElementById('overlay');
    const titleEl = document.getElementById('playlist-title');
    titleEl.textContent = title;
    overlay.classList.add('show');
    setTimeout(() => overlay.classList.remove('show'), 5000);
}

document.addEventListener('DOMContentLoaded', init);
