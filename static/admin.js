/**
 * Artwork Admin Console - Client Logic (admin.js)
 * Phase 3 Refactor: Many-to-Many Playlists and Centralized Library.
 * Enhanced with automatic background polling for live updates.
 */

const API_BASE = (window.location.origin === 'null' || window.location.protocol === 'file:') 
    ? 'http://localhost:8000' 
    : window.location.origin;

let currentPlaylistId = null;
let currentPlaylists = [];
let fullLibrary = [];
let cropper = null;
let currentArtworkId = null;
let currentView = 'playlists';
let pollInterval = null;

async function init() {
    console.log('[Admin] Initializing Refactored Console...');
    setupUploadZone();
    setupSortable();
    setupPlaylistInput(); // Add key listener
    await refreshData();
    
    // Start background polling every 5 seconds for live updates
    startPolling();
}

/**
 * Handles Enter key on playlist input.
 */
function setupPlaylistInput() {
    const input = document.getElementById('new-playlist-name');
    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            createPlaylist();
        }
    });
}

/**
 * Creates a new playlist via the API.
 */
async function createPlaylist() {
    const input = document.getElementById('new-playlist-name');
    const name = input.value.trim();
    if (!name) return;

    try {
        const fd = new FormData();
        fd.append('name', name);
        const res = await fetch(`${API_BASE}/playlists`, { method: 'POST', body: fd });
        if (res.ok) {
            input.value = '';
            await refreshData();
        } else {
            const err = await res.json();
            alert(`Error: ${err.detail}`);
        }
    } catch (error) {
        console.error('[Admin] Playlist creation failed:', error);
    }
}

/**
 * Periodically refreshes data to reflect background AI processing or uploads.
 */
function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(async () => {
        // Only refresh if no modal is open to avoid disrupting user interaction
        const isModalOpen = document.getElementById('crop-modal').style.display === 'flex' || 
                           document.getElementById('library-modal').style.display === 'flex';
        
        if (!isModalOpen) {
            await refreshData();
        }
    }, 5000);
}

async function refreshData() {
    // We fetch in parallel for efficiency
    await Promise.all([
        fetchPlaylists(),
        fetchLibrary(),
        fetchReviewQueue()
    ]);
}

function switchView(view) {
    currentView = view;
    document.getElementById('nav-playlists').classList.toggle('active', view === 'playlists');
    document.getElementById('nav-library').classList.toggle('active', view === 'library');
    document.getElementById('nav-review').classList.toggle('active', view === 'review');
    
    document.getElementById('view-playlists').classList.toggle('hidden', view !== 'playlists');
    document.getElementById('view-library').classList.toggle('hidden', view !== 'library');
    document.getElementById('view-review').classList.toggle('hidden', view !== 'review');
    
    document.getElementById('sidebar-playlists').classList.toggle('hidden', view !== 'playlists');
}

async function fetchLibrary() {
    try {
        const response = await fetch(`${API_BASE}/artworks`);
        const data = await response.json();
        
        // Simple optimization: only re-render if count changed
        if (data.length !== fullLibrary.length) {
            fullLibrary = data;
            document.getElementById('library-count').textContent = fullLibrary.length;
            renderLibraryGrid();
        }
    } catch (error) { console.error('[Admin] Fetch library failed:', error); }
}

async function fetchPlaylists() {
    try {
        const response = await fetch(`${API_BASE}/playlists`);
        const data = await response.json();
        
        // Check if any playlist input is currently focused to avoid overwriting user edits
        const focusedEl = document.activeElement;
        const isEditingSidebar = focusedEl && focusedEl.tagName === 'INPUT' && focusedEl.closest('.playlist-item');

        currentPlaylists = data;
        document.getElementById('playlist-count').textContent = currentPlaylists.length;
        
        if (!isEditingSidebar) {
            renderSidebar();
        }
        
        if (currentPlaylistId) {
            const active = currentPlaylists.find(p => p.id === currentPlaylistId);
            if (active) selectPlaylist(active.id);
        } else if (currentPlaylists.length > 0) {
            selectPlaylist(currentPlaylists[0].id);
        }
    } catch (error) { console.error('[Admin] Fetch playlists failed:', error); }
}

async function fetchReviewQueue() {
    try {
        const response = await fetch(`${API_BASE}/artworks/pending`);
        const data = await response.json();
        
        // Always update count
        document.getElementById('review-count').textContent = data.length;
        
        // Only re-render list if count changed to preserve scroll/input state if user is looking
        const list = document.getElementById('review-list');
        if (data.length !== list.children.length || (data.length > 0 && list.innerHTML.includes('Queue is empty'))) {
            renderReviewQueue(data);
        }
    } catch (error) { console.error('[Admin] Fetch queue failed:', error); }
}

function renderLibraryGrid() {
    const grid = document.getElementById('library-grid');
    grid.innerHTML = '';
    fullLibrary.forEach(art => {
        const card = document.createElement('div');
        card.className = 'artwork-card';
        card.innerHTML = `
            <img src="${API_BASE}/artworks/${art.id}/thumbnail" alt="${art.filename}">
            <div class="info">
                <strong>${art.title || art.filename}</strong><br>
                <small>${art.artist || 'Unknown'}</small>
            </div>
            <div class="actions">
                <button onclick="openCropModal(${art.id})">Crop</button>
                <button onclick="deleteArtworkPermanently(${art.id})" style="color: #ef4444;">Delete Permanently</button>
            </div>
        `;
        grid.appendChild(card);
    });
}

function renderArtworkGrid(artworks) {
    const grid = document.getElementById('artwork-grid');
    grid.innerHTML = '';
    artworks.forEach(art => {
        const card = document.createElement('div');
        card.className = 'artwork-card';
        card.dataset.id = art.id;
        card.innerHTML = `
            <img src="${API_BASE}/artworks/${art.id}/thumbnail" alt="${art.filename}">
            <div class="info">
                <strong>${art.title || art.filename}</strong><br>
                <small>${art.artist || 'Unknown'}</small>
            </div>
            <div class="actions">
                <button onclick="openCropModal(${art.id})">Crop</button>
                <button onclick="removeArtworkFromPlaylist(${art.id})" style="color: #f59e0b;">Remove</button>
            </div>
        `;
        grid.appendChild(card);
    });
}

async function removeArtworkFromPlaylist(artworkId) {
    if (!currentPlaylistId) return;
    try {
        await fetch(`${API_BASE}/playlists/${currentPlaylistId}/artworks/${artworkId}`, { method: 'DELETE' });
        await refreshData();
    } catch (error) { console.error('[Admin] Unlink failed:', error); }
}

async function deleteArtworkPermanently(id) {
    if (!confirm('PERMANENTLY delete this artwork from the library and all playlists? This wipes the file.')) return;
    try {
        await fetch(`${API_BASE}/artworks/${id}`, { method: 'DELETE' });
        await refreshData();
    } catch (error) { console.error('[Admin] Delete failed:', error); }
}

function openLibraryPicker() {
    const modal = document.getElementById('library-modal');
    const grid = document.getElementById('library-picker-grid');
    grid.innerHTML = '';
    
    const playlist = currentPlaylists.find(p => p.id === currentPlaylistId);
    const existingIds = new Set(playlist.artworks.map(a => a.id));

    fullLibrary.filter(art => !existingIds.has(art.id)).forEach(art => {
        const card = document.createElement('div');
        card.className = 'picker-card';
        card.onclick = () => addExistingToPlaylist(art.id);
        card.innerHTML = `
            <img src="${API_BASE}/artworks/${art.id}/thumbnail">
            <p>${art.title || art.filename}</p>
        `;
        grid.appendChild(card);
    });
    modal.style.display = 'flex';
}

async function addExistingToPlaylist(artworkId) {
    try {
        await fetch(`${API_BASE}/playlists/${currentPlaylistId}/artworks/${artworkId}`, { method: 'POST' });
        closeLibraryPicker();
        await refreshData();
    } catch (error) { console.error('[Admin] Link failed:', error); }
}

function closeLibraryPicker() { document.getElementById('library-modal').style.display = 'none'; }

function renderSidebar() {
    const list = document.getElementById('playlist-list');
    list.innerHTML = '';
    currentPlaylists.forEach(p => {
        const li = document.createElement('li');
        li.className = `playlist-item ${p.id === currentPlaylistId ? 'active' : ''}`;
        li.dataset.id = p.id;
        li.innerHTML = `
            <div style="display:flex; justify-content:space-between;">
                <strong>${p.name}</strong>
                <button onclick="event.stopPropagation(); deletePlaylist(${p.id}, '${p.name}')" style="background:none; border:none; color:#ef4444;">×</button>
            </div>
            <div style="font-size:0.75rem; color:#94a3b8; margin-top:5px;">${p.artworks?.length || 0} images</div>
            <div class="playlist-meta" onclick="event.stopPropagation()" style="display: grid; grid-template-columns: 1fr 1fr; gap: 5px; margin-top: 10px;">
                <div style="grid-column: span 2; margin-bottom: 5px;">
                    <label style="display:block;">Default Mode:</label>
                    <select onchange="updatePlaylistSetting(${p.id}, {default_mode: this.value})" style="width:100%; background:#0f172a; color:white; border:1px solid var(--border-color); border-radius:4px; font-size:0.7rem;">
                        <option value="ken-burns" ${p.default_mode === 'ken-burns' ? 'selected' : ''}>Ken Burns</option>
                        <option value="static-crop" ${p.default_mode === 'static-crop' ? 'selected' : ''}>Static Crop</option>
                        <option value="contain-matte" ${p.default_mode === 'contain-matte' ? 'selected' : ''}>Contain Matte</option>
                    </select>
                </div>
                <div>
                    <label>Cycle (s):</label>
                    <input type="number" value="${p.display_time}" min="1" onchange="updatePlaylistSetting(${p.id}, {display_time: parseInt(this.value)})" style="width:100%;">
                </div>
                <div>
                    <label>Wait (s):</label>
                    <input type="number" value="${p.placard_initial_wait_sec}" min="0" onchange="updatePlaylistSetting(${p.id}, {placard_initial_wait_sec: parseInt(this.value)})" style="width:100%;">
                </div>
                <div>
                    <label>Show (s):</label>
                    <input type="number" value="${p.placard_initial_show_sec}" min="0" onchange="updatePlaylistSetting(${p.id}, {placard_initial_show_sec: parseInt(this.value)})" style="width:100%;">
                </div>
                <div>
                    <label>Manual (s):</label>
                    <input type="number" value="${p.placard_interaction_show_sec}" min="0" onchange="updatePlaylistSetting(${p.id}, {placard_interaction_show_sec: parseInt(this.value)})" style="width:100%;">
                </div>
            </div>
        `;
        li.onclick = () => selectPlaylist(p.id);
        list.appendChild(li);
    });
}

async function deletePlaylist(id, name) {
    if (!confirm(`Delete playlist "${name}"? Library images will remain.`)) return;
    try {
        await fetch(`${API_BASE}/playlists/${id}`, { method: 'DELETE' });
        if (currentPlaylistId === id) currentPlaylistId = null;
        await refreshData();
    } catch (error) { console.error('[Admin] Delete failed:', error); }
}

function selectPlaylist(id) {
    currentPlaylistId = id;
    const playlist = currentPlaylists.find(p => p.id === id);
    if (!playlist) return;
    document.querySelectorAll('.playlist-item').forEach(el => el.classList.toggle('active', parseInt(el.dataset.id) === id));
    document.getElementById('target-playlist-name').textContent = playlist.name;
    renderArtworkGrid(playlist.artworks || []);
}

function setupUploadZone() {
    const zones = [document.getElementById('upload-zone'), document.getElementById('library-upload-zone')];
    const inputs = [document.getElementById('file-input'), document.getElementById('library-file-input')];

    zones.forEach((zone, idx) => {
        if (!zone) return;
        zone.ondragover = (e) => { e.preventDefault(); zone.style.borderColor = '#3b82f6'; };
        zone.ondragleave = () => { zone.style.borderColor = '#334155'; };
        zone.ondrop = (e) => {
            e.preventDefault();
            zone.style.borderColor = '#334155';
            const pid = (zone.id === 'upload-zone') ? currentPlaylistId : null;
            if (e.dataTransfer.files) uploadFiles(e.dataTransfer.files, pid);
        };
    });

    document.getElementById('upload-zone').onclick = () => document.getElementById('file-input').click();
    document.getElementById('file-input').onchange = (e) => { if (e.target.files) uploadFiles(e.target.files, currentPlaylistId); };
}

async function uploadFiles(files, playlistId) {
    for (let file of files) {
        const fd = new FormData();
        fd.append('file', file);
        if (playlistId) fd.append('playlist_id', playlistId);
        try { await fetch(`${API_BASE}/upload`, { method: 'POST', body: fd }); }
        catch (error) { console.error('[Admin] Upload failed:', error); }
    }
    // Immediate refresh after upload completes
    await refreshData();
}

async function updatePlaylistSetting(id, settings) {
    try {
        if (pollInterval) clearInterval(pollInterval);
        await fetch(`${API_BASE}/playlists/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        await refreshData();
        startPolling();
    } catch (error) { 
        console.error('[Admin] Update failed:', error); 
        startPolling();
    }
}

function setupSortable() {
    const grid = document.getElementById('artwork-grid');
    new Sortable(grid, {
        animation: 150, ghostClass: 'sortable-ghost',
        onEnd: async () => {
            const ids = Array.from(grid.children).map(el => parseInt(el.dataset.id));
            await saveOrder(ids);
        }
    });
}

async function saveOrder(ids) {
    if (!currentPlaylistId) return;
    try {
        await fetch(`${API_BASE}/playlists/${currentPlaylistId}/reorder`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artwork_ids: ids })
        });
        await refreshData();
    } catch (error) { console.error('[Admin] Reorder failed:', error); }
}

function renderReviewQueue(artworks) {
    const list = document.getElementById('review-list');
    list.innerHTML = artworks.length === 0 ? '<p style="text-align:center; color:#94a3b8; margin-top:40px;">Queue is empty.</p>' : '';
    artworks.forEach(art => {
        const card = document.createElement('div');
        card.className = 'review-card';
        card.innerHTML = `
            <div class="review-image"><img src="${API_BASE}/artworks/${art.id}/thumbnail"></div>
            <div class="review-form">
                <div class="form-group"><label>Title</label><input type="text" id="title-${art.id}" value="${art.title || ''}"></div>
                <div class="form-group"><label>Artist</label><input type="text" id="artist-${art.id}" value="${art.artist || ''}"></div>
                <div class="form-group"><label>Year</label><input type="text" id="year-${art.id}" value="${art.year || ''}"></div>
                <div class="form-group"><label>Tags</label><input type="text" id="tags-${art.id}" value="${art.tags || ''}"></div>
                <div class="form-group full"><label>Description</label><textarea id="desc-${art.id}" rows="3">${art.description || ''}</textarea></div>
                <div class="review-actions">
                    <button class="secondary" onclick="deleteArtworkPermanently(${art.id})">Delete</button>
                    <button class="success" onclick="approveArtwork(${art.id})">Approve & Publish</button>
                </div>
            </div>
        `;
        list.appendChild(card);
    });
}

async function approveArtwork(id) {
    const metadata = {
        title: document.getElementById(`title-${id}`).value,
        artist: document.getElementById(`artist-${id}`).value,
        year: document.getElementById(`year-${id}`).value,
        tags: document.getElementById(`tags-${id}`).value,
        description: document.getElementById(`desc-${id}`).value
    };
    try {
        await fetch(`${API_BASE}/artworks/${id}/approve`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(metadata)
        });
        await refreshData();
    } catch (error) { console.error('[Admin] Approval failed:', error); }
}

function openCropModal(id) {
    currentArtworkId = id;
    const modal = document.getElementById('crop-modal');
    const image = document.getElementById('cropper-image');
    let artwork = fullLibrary.find(a => a.id === id);
    if (!artwork) {
        for (let p of currentPlaylists) {
            artwork = p.artworks.find(a => a.id === id);
            if (artwork) break;
        }
    }
    image.src = `${API_BASE}/artworks/${id}/preview`;
    modal.style.display = 'flex';
    if (cropper) cropper.destroy();
    cropper = new Cropper(image, {
        viewMode: 1, dragMode: 'move', autoCropArea: 0.8,
        restore: false, guides: true, center: true, highlight: false,
        cropBoxMovable: true, cropBoxResizable: true,
        data: (artwork && artwork.crop_width > 1) ? {
            x: (artwork.crop_x / artwork.original_width) * 1920,
            y: (artwork.crop_y / artwork.original_height) * (1920 * (artwork.original_height / artwork.original_width)),
            width: (artwork.crop_width / artwork.original_width) * 1920,
            height: (artwork.crop_height / artwork.original_height) * (1920 * (artwork.original_height / artwork.original_width))
        } : null,
        ready() {
            const canvasData = cropper.getCanvasData();
            const ratio = canvasData.naturalWidth / artwork.original_width;
            if (artwork && artwork.crop_width > 1) {
                cropper.setData({
                    x: artwork.crop_x * ratio, y: artwork.crop_y * ratio,
                    width: artwork.crop_width * ratio, height: artwork.crop_height * ratio
                });
            }
        }
    });
}

function setRatio(ratio, btn) {
    if (!cropper) return;
    cropper.setAspectRatio(ratio);
    document.querySelectorAll('.ratio-buttons button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
}

async function saveCrop() {
    if (!cropper || !currentArtworkId) return;
    const data = cropper.getData();
    const canvasData = cropper.getCanvasData();
    let artwork = fullLibrary.find(a => a.id === currentArtworkId);
    if (!artwork) {
        for (let p of currentPlaylists) {
            artwork = p.artworks.find(a => a.id === currentArtworkId);
            if (artwork) break;
        }
    }
    const ratio = artwork.original_width / canvasData.naturalWidth;
    try {
        await fetch(`${API_BASE}/artworks/${currentArtworkId}/crop`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                crop_x: data.x * ratio, crop_y: data.y * ratio,
                crop_width: data.width * ratio, crop_height: data.height * ratio
            })
        });
        document.getElementById('crop-modal').style.display = 'none';
        if (cropper) cropper.destroy();
        await refreshData();
    } catch (error) { console.error('[Admin] Save crop failed:', error); }
}

function closeModal() {
    document.getElementById('crop-modal').style.display = 'none';
    if (cropper) cropper.destroy();
}

document.addEventListener('DOMContentLoaded', init);
