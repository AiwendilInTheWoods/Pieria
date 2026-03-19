/**
 * Artwork Admin Console - Client Logic (admin.js)
 * Phase 2 Polish: Reactive UI, Ratios, Display Timings, Reordering, and Precision Scaling.
 */

const API_BASE = (window.location.origin === 'null' || window.location.protocol === 'file:') 
    ? 'http://localhost:8000' 
    : window.location.origin;

let currentPlaylistId = null;
let currentPlaylists = [];
let cropper = null;
let currentArtworkId = null;
let sortable = null;

/**
 * Initializes the dashboard.
 */
async function init() {
    console.log('[Admin] Initializing Admin Console...');
    setupUploadZone();
    setupSortable();
    await fetchPlaylists();
}

async function fetchPlaylists() {
    try {
        const response = await fetch(`${API_BASE}/playlists`);
        currentPlaylists = await response.json();
        renderSidebar();
        
        if (currentPlaylistId) {
            const active = currentPlaylists.find(p => p.id === currentPlaylistId);
            if (active) selectPlaylist(active.id);
        } else if (currentPlaylists.length > 0) {
            selectPlaylist(currentPlaylists[0].id);
        }
    } catch (error) { console.error('[Admin] Fetch failed:', error); }
}

function renderSidebar() {
    const list = document.getElementById('playlist-list');
    list.innerHTML = '';
    currentPlaylists.forEach(p => {
        const li = document.createElement('li');
        li.className = `playlist-item ${p.id === currentPlaylistId ? 'active' : ''}`;
        li.dataset.id = p.id; // Store ID for lookup
        li.innerHTML = `
            <div><strong>${p.name}</strong> (${p.artworks?.length || 0})</div>
            <div class="playlist-meta" onclick="event.stopPropagation()">
                <label>Cycle (s):</label>
                <input type="number" value="${p.display_time}" onchange="updatePlaylistTime(${p.id}, this.value)">
            </div>
        `;
        li.onclick = () => selectPlaylist(p.id);
        list.appendChild(li);
    });
}

async function updatePlaylistTime(id, seconds) {
    try {
        await fetch(`${API_BASE}/playlists/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ display_time: parseInt(seconds) })
        });
        await fetchPlaylists();
    } catch (error) { console.error('[Admin] Timing update failed:', error); }
}

function selectPlaylist(id) {
    currentPlaylistId = id;
    const playlist = currentPlaylists.find(p => p.id === id);
    if (!playlist) return;

    // Immediate UI Feedback: Update highlighting
    document.querySelectorAll('.playlist-item').forEach(el => {
        el.classList.toggle('active', parseInt(el.dataset.id) === id);
    });

    document.getElementById('target-playlist-name').textContent = playlist.name;
    renderArtworkGrid(playlist.artworks || []);
}

function renderArtworkGrid(artworks) {
    const grid = document.getElementById('artwork-grid');
    grid.innerHTML = '';
    artworks.forEach(art => {
        const card = document.createElement('div');
        card.className = 'artwork-card';
        card.dataset.id = art.id;
        const thumbUrl = `${API_BASE}/artworks/${art.id}/thumbnail`;
        card.innerHTML = `
            <img src="${thumbUrl}" alt="${art.filename}">
            <div class="info">
                <strong>${art.filename}</strong><br>
                <small>Res: ${art.original_width}x${art.original_height}</small><br>
                <small>Crop: ${Math.round(art.crop_width)}x${Math.round(art.crop_height)}</small>
            </div>
            <div class="actions">
                <button onclick="event.stopPropagation(); openCropModal(${art.id})">Crop</button>
                <button onclick="event.stopPropagation(); deleteArtwork(${art.id})" style="color: #ef4444;">Delete</button>
            </div>
        `;
        grid.appendChild(card);
    });
}

function setupSortable() {
    const grid = document.getElementById('artwork-grid');
    sortable = new Sortable(grid, {
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
        await fetchPlaylists();
    } catch (error) { console.error('[Admin] Reorder failed:', error); }
}

async function createPlaylist() {
    const input = document.getElementById('new-playlist-name');
    const name = input.value.trim();
    if (!name) return;
    try {
        const fd = new FormData(); fd.append('name', name);
        const res = await fetch(`${API_BASE}/playlists`, { method: 'POST', body: fd });
        if (res.ok) { input.value = ''; await fetchPlaylists(); }
    } catch (error) { console.error('[Admin] Playlist creation failed:', error); }
}

function setupUploadZone() {
    const zone = document.getElementById('upload-zone');
    const input = document.getElementById('file-input');
    zone.onclick = () => input.click();
    input.onchange = (e) => { if (e.target.files) uploadFiles(e.target.files); };
}

async function uploadFiles(files) {
    if (!currentPlaylistId) return alert('Select playlist.');
    for (let file of files) {
        const fd = new FormData(); fd.append('file', file);
        try { await fetch(`${API_BASE}/playlists/${currentPlaylistId}/upload`, { method: 'POST', body: fd }); }
        catch (error) { console.error('[Admin] Upload error:', error); }
    }
    await fetchPlaylists();
}

async function deleteArtwork(id) {
    if (!confirm('Delete this artwork?')) return;
    try {
        const res = await fetch(`${API_BASE}/artworks/${id}`, { method: 'DELETE' });
        if (res.ok) await fetchPlaylists();
    } catch (error) { console.error('[Admin] Delete error:', error); }
}

/**
 * Opens the Cropper modal and loads optimized preview.
 */
function openCropModal(id) {
    currentArtworkId = id;
    const modal = document.getElementById('crop-modal');
    const image = document.getElementById('cropper-image');
    
    const playlist = currentPlaylists.find(p => p.id === currentPlaylistId);
    const artwork = playlist.artworks.find(a => a.id === id);

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
    const playlist = currentPlaylists.find(p => p.id === currentPlaylistId);
    const artwork = playlist.artworks.find(a => a.id === currentArtworkId);
    const ratio = artwork.original_width / canvasData.naturalWidth;

    try {
        const response = await fetch(`${API_BASE}/artworks/${currentArtworkId}/crop`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                crop_x: data.x * ratio, crop_y: data.y * ratio,
                crop_width: data.width * ratio, crop_height: data.height * ratio
            })
        });
        if (response.ok) { closeModal(); await fetchPlaylists(); }
    } catch (error) { console.error('[Admin] Save crop failed:', error); }
}

function closeModal() {
    document.getElementById('crop-modal').style.display = 'none';
    if (cropper) cropper.destroy();
}

document.addEventListener('DOMContentLoaded', init);
