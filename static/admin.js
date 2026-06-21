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
let discoveryQueue = [];
let cropper = null;
let currentArtworkId = null;
let currentView = 'playlists';
let pollInterval = null;
let sortableInstance = null;
let currentSessionId = null;

// Serialization Queue for API Actions
let actionQueue = [];
let isProcessingQueue = false;

function enqueueAction(actionFn) {
    actionQueue.push(actionFn);
    processQueue();
}

async function processQueue() {
    if (isProcessingQueue || actionQueue.length === 0) return;
    isProcessingQueue = true;
    while (actionQueue.length > 0) {
        const action = actionQueue.shift();
        try {
            await action();
        } catch (e) {
            console.error("[Admin] Queue action failed:", e);
        }
    }
    isProcessingQueue = false;
    await refreshData();
}

async function init() {
    setupUploadZone();
    setupPlaylistInput(); // Add key listener
    initServerAddress();  // show the address to point displays/Pi/e-ink/Frame at
    await loadPremiumSettings();
    await handleOAuthCallback();   // catch an OpenRouter OAuth redirect (?code=…)
    await loadAiSettings();
    loadFrameSettings();   // non-blocking: populate the Frame TV panel

    // Restore the view the user was last on (survives a browser refresh).
    const savedView = (() => { try { return localStorage.getItem('sd_admin_view'); } catch (e) { return null; } })();
    const validViews = ['playlists', 'library', 'review', 'discover', 'catalog', 'settings'];
    if (savedView && validViews.includes(savedView)) {
        switchView(savedView);
    }

    // Restore the collection that was open, so refresh returns to it (not the first one).
    // fetchPlaylists() (inside refreshData) reads currentPlaylistId to re-select it.
    const savedPlaylist = (() => { try { return localStorage.getItem('sd_admin_playlist'); } catch (e) { return null; } })();
    if (savedPlaylist && !isNaN(parseInt(savedPlaylist, 10))) {
        currentPlaylistId = parseInt(savedPlaylist, 10);
    }

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
        fetchReviewQueue(),
        fetchDiscoveryQueue()
    ]);
}

function switchView(view) {
    currentView = view;
    // Remember the active view so a browser refresh returns here instead of
    // snapping back to the default Collections screen.
    try { localStorage.setItem('sd_admin_view', view); } catch (e) {}
    document.getElementById('nav-playlists').classList.toggle('active', view === 'playlists');
    document.getElementById('nav-library').classList.toggle('active', view === 'library');
    document.getElementById('nav-review').classList.toggle('active', view === 'review');
    document.getElementById('nav-discover').classList.toggle('active', view === 'discover');
    document.getElementById('nav-catalog').classList.toggle('active', view === 'catalog');
    document.getElementById('nav-settings').classList.toggle('active', view === 'settings');

    document.getElementById('view-playlists').classList.toggle('hidden', view !== 'playlists');
    document.getElementById('view-library').classList.toggle('hidden', view !== 'library');
    document.getElementById('view-review').classList.toggle('hidden', view !== 'review');
    document.getElementById('view-discover').classList.toggle('hidden', view !== 'discover');
    document.getElementById('view-catalog').classList.toggle('hidden', view !== 'catalog');
    document.getElementById('view-settings').classList.toggle('hidden', view !== 'settings');

    document.getElementById('sidebar-playlists').classList.toggle('hidden', view !== 'playlists');

    // On mobile, picking a view closes the slide-in drawer.
    document.body.classList.remove('sidebar-open');

    if (view === 'catalog') renderCatalog();
}

// Mobile-only: toggle the slide-in sidebar drawer (no-op visual on desktop).
function toggleSidebar() {
    document.body.classList.toggle('sidebar-open');
}
window.toggleSidebar = toggleSidebar;

// Transient, themed feedback. type: '' | 'success' | 'error'. Replaces native alert().
function showToast(message, type = '') {
    let c = document.getElementById('toast-container');
    if (!c) { c = document.createElement('div'); c.id = 'toast-container'; document.body.appendChild(c); }
    const t = document.createElement('div');
    t.className = 'toast' + (type ? ' ' + type : '');
    t.textContent = message;
    c.appendChild(t);
    requestAnimationFrame(() => t.classList.add('show'));
    setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 2600);
}
window.showToast = showToast;

// Surface the address other devices should use to reach this server. We echo the
// origin the admin was actually reached on (so LAN-IP / <host>.local just work), and
// warn when it's localhost (which other devices can't reach). This avoids reporting a
// bogus container IP — Docker bridging makes a server-side LAN-IP lookup unreliable.
function initServerAddress() {
    const el = document.getElementById('server-address');
    if (!el) return;
    el.textContent = window.location.origin;
    const host = window.location.hostname;
    if (host === 'localhost' || host === '127.0.0.1') {
        const note = document.getElementById('server-address-note');
        if (note) {
            note.style.display = 'block';
            note.innerHTML = "You're viewing this on the server itself, so this shows <code>localhost</code> — " +
                "other devices can't reach that. Point them at this machine's LAN address instead: find it with " +
                "<code>hostname -I</code>, or open this admin from another device via " +
                "<code>http://&lt;this-hostname&gt;.local:8000/admin</code> and this address will update to match.";
        }
    }
}

function copyServerAddress() {
    const txt = document.getElementById('server-address').textContent;
    if (navigator.clipboard) navigator.clipboard.writeText(txt);
    const c = document.getElementById('server-address-copied');
    if (c) { c.style.display = 'inline'; setTimeout(() => { c.style.display = 'none'; }, 1500); }
}
window.copyServerAddress = copyServerAddress;

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
        
        // Comprehensive optimization check to prevent 5-second blinking/wiping of the grid.
        // It checks the stringified deeply-nested data to ensure ANY external artwork addition triggers a fresh render.
        const newDataStr = JSON.stringify(data);
        if (window._lastPlaylistsJSON === newDataStr) return;
        window._lastPlaylistsJSON = newDataStr;
        
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
            if (active) {
                selectPlaylist(active.id);
            } else if (currentPlaylists.length > 0) {
                // Saved collection no longer exists (e.g. deleted) — fall back to the first.
                selectPlaylist(currentPlaylists[0].id);
            }
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

        // Re-render whenever the server data changes in ANY way — not just when the
        // count changes. Freshly-uploaded cards arrive blank and get their AI metadata
        // filled in by background enrichment a few seconds later; that's a content
        // change with no count change, so a length-only guard would leave the cards
        // blank until a manual page reload. renderReviewQueue reconciles by id and
        // preserves any field the user is actively editing.
        const dataStr = JSON.stringify(data);
        if (dataStr !== window._lastReviewJSON) {
            window._lastReviewJSON = dataStr;
            renderReviewQueue(data);
        }
    } catch (error) { console.error('[Admin] Fetch queue failed:', error); }
}

async function fetchDiscoveryQueue() {
    try {
        const response = await fetch(`${API_BASE}/api/discover/queue`);
        const data = await response.json();
        document.getElementById('discover-count').textContent = data.length;
        if (data.length !== discoveryQueue.length) {
            discoveryQueue = data;
            renderDiscoveryGrid();
        }
    } catch (error) { console.error('[Admin] Fetch discovery failed:', error); }
}

function renderDiscoveryGrid() {
    const grid = document.getElementById('discover-grid');
    
    // Prune obsolete cards BEFORE indexing to prevent 'leapfrog' detaching
    const newIds = new Set(discoveryQueue.map(item => String(item.id)));
    Array.from(grid.children).forEach(card => {
        if (card.dataset.id && !newIds.has(card.dataset.id)) card.remove();
    });

    const existingCards = {};
    Array.from(grid.children).forEach(card => {
        if (card.dataset.id) existingCards[card.dataset.id] = card;
    });

    let currentDOMIndex = 0;

    discoveryQueue.forEach(item => {
        const idStr = String(item.id);
        let card = existingCards[idStr];
        
        if (card) {
            delete existingCards[idStr];
            if (grid.children[currentDOMIndex] !== card) {
                grid.insertBefore(card, grid.children[currentDOMIndex]);
            }
        } else {
            card = document.createElement('div');
            card.className = 'artwork-card';
            card.dataset.id = item.id;
                const thumbUrl = item.thumbnail_url + (item.thumbnail_url.includes('?') ? '&' : '?') + '_cb=' + encodeURIComponent(item.source_url);
                card.innerHTML = `
                <img src="${thumbUrl}" alt="${item.proposed_title}">
                <div class="info">
                    <strong>${item.proposed_title}</strong><br>
                    <small>${item.proposed_artist}</small><br>
                    <small style="opacity:0.6">${item.source_api}</small>
                </div>
                <div class="actions" style="grid-template-columns: 1fr 1fr;">
                    <button onclick="approveDiscovery(${item.id}, this)" class="success" title="Send to the Review Queue to finalize and publish">Review →</button>
                    <button onclick="rejectDiscovery(${item.id}, this)" style="color: #ef4444;">Reject</button>
                </div>
            `;
            if (currentDOMIndex < grid.children.length) {
                grid.insertBefore(card, grid.children[currentDOMIndex]);
            } else {
                grid.appendChild(card);
            }
        }
        currentDOMIndex++;
    });
}

async function dispatchScouts() {
    const searchInput = document.getElementById('scout-search');
    const query = searchInput.value.trim();
    const btn = document.getElementById('deploy-scout-btn');
    const statusArea = document.getElementById('scout-status');
    const statusText = document.getElementById('scout-status-text');
    const limitSelect = document.getElementById('scout-limit');
    const limit = parseInt(limitSelect.value) || 10;
    
    // Get selected sources
    const selectedSources = Array.from(document.querySelectorAll('input[name="scout-source"]:checked'))
                                 .map(cb => cb.value);
    
    if (selectedSources.length === 0) return alert("Please select at least one source.");

    // UI Feedback
    btn.disabled = true;
    statusArea.style.display = 'block';
    statusText.textContent = `Scout is hunting in ${selectedSources.length} museum archives...`;

    try {
        const response = await fetch(`${API_BASE}/api/discover/dispatch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                search: query,
                sources: selectedSources,
                limit: limit
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            currentSessionId = data.session_id;
            
            // Show classification feedback
            const intentType = data.intent?.type || 'freetext';
            const intentName = data.intent?.canonical || query;
            const intentLabel = intentType === 'artist' ? `🎨 Artist: ${intentName}`
                              : intentType === 'genre' ? `🖼️ Genre: ${intentName}`
                              : intentType === 'subject' ? `🔍 Subject: ${intentName}`
                              : `📝 Search: ${intentName}`;
            
            statusText.textContent = `Scout deployed! Classified as ${intentLabel}. Results incoming...`;
            
            // Show Load More button
            document.getElementById('load-more-btn').style.display = 'block';
            
            // Don't clear input — user might want to tweak and re-search
            setTimeout(() => {
                statusArea.style.display = 'none';
                btn.disabled = false;
            }, 4000);
            await refreshData();
        } else {
            throw new Error("Dispatch failed");
        }
    } catch (error) { 
        console.error('[Admin] Scout dispatch failed:', error); 
        statusText.textContent = "Scout lost contact with the museums. Try again.";
        setTimeout(() => {
            statusArea.style.display = 'none';
            btn.disabled = false;
        }, 5000);
    }
}

async function loadMoreDiscoveries() {
    if (!currentSessionId) {
        alert('No active search session. Please run a new search first.');
        return;
    }
    
    const btn = document.getElementById('load-more-btn');
    const statusArea = document.getElementById('scout-status');
    const statusText = document.getElementById('scout-status-text');
    
    btn.disabled = true;
    btn.textContent = 'Loading...';
    statusArea.style.display = 'block';
    statusText.textContent = 'Fetching next batch of results...';
    
    try {
        const response = await fetch(`${API_BASE}/api/discover/more`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: currentSessionId })
        });
        
        if (response.ok) {
            statusText.textContent = 'More masterpieces loaded!';
            setTimeout(() => {
                statusArea.style.display = 'none';
            }, 3000);
            // Wait a moment for background task to complete, then refresh
            setTimeout(async () => {
                await refreshData();
            }, 2000);
        } else {
            const err = await response.json();
            statusText.textContent = err.detail || 'Failed to load more results.';
            currentSessionId = null;
            document.getElementById('load-more-btn').style.display = 'none';
        }
    } catch (error) {
        console.error('[Admin] Load more failed:', error);
        statusText.textContent = 'Failed to load more. Try a new search.';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Load More Results';
    }
}

function approveDiscovery(id, btn) {
    btn.disabled = true;
    btn.textContent = "Queued...";
    btn.style.opacity = "0.7";
    enqueueAction(async () => {
        btn.textContent = "Sending...";
        const res = await fetch(`${API_BASE}/api/discover/approve/${id}`, { method: 'POST' });
        if (!res.ok) {
            showToast("Couldn't fetch that artwork — the museum server may be busy.", "error");
        } else {
            showToast("Sent to the Review Queue →", "success");
        }
    });
}

function rejectDiscovery(id, btn) {
    btn.disabled = true;
    btn.textContent = "Queued...";
    btn.style.opacity = "0.7";
    enqueueAction(async () => {
        await fetch(`${API_BASE}/api/discover/reject/${id}`, { method: 'POST' });
    });
}

function clearRejectedHistory() {
    if (!confirm('Are you sure you want to clear your rejected history? Scouts will be able to recommend previously denied artwork again.')) return;
    
    enqueueAction(async () => {
        try {
            await fetch(`${API_BASE}/api/discover/history`, { method: 'DELETE' });
            alert("Rejected history successfully cleared! Scouts will now rediscover previously skipped artwork.");
        } catch (error) { 
            console.error('[Admin] Clear history failed:', error); 
            alert("Failed to clear history. Check console.");
        }
    });
}

function clearOrphanedHistory() {
    if (!confirm('Clear history for artworks you approved but later deleted? This allows scouts to recommend them again.')) return;
    
    enqueueAction(async () => {
        try {
            const res = await fetch(`${API_BASE}/api/discover/orphans`, { method: 'DELETE' });
            const data = await res.json();
            alert(data.status + ". Scouts will now rediscover them!");
        } catch (error) { 
            console.error('[Admin] Clear orphans failed:', error); 
            alert("Failed to clear orphaned history. Check console.");
        }
    });
}

function clearPendingDiscoveries() {
    if (!confirm('Clear ALL pending discover items? This gives you a clean slate for testing.')) return;
    
    enqueueAction(async () => {
        try {
            const res = await fetch(`${API_BASE}/api/discover/clear-pending`, { method: 'DELETE' });
            const data = await res.json();
            alert(data.status);
            // Clear the discover grid UI
            document.getElementById('discover-grid').innerHTML = '';
            document.getElementById('load-more-btn').style.display = 'none';
            currentSessionId = null;
            loadDiscoverQueueThrottled();
        } catch (error) { 
            console.error('[Admin] Clear pending failed:', error); 
            alert("Failed to clear pending items. Check console.");
        }
    });
}

function factoryReset() {
    if (!confirm('⚠️ FACTORY RESET: This will delete ALL artwork except the original seed masterpieces, clear the entire discover queue, and remove playlist associations. This CANNOT be undone. Are you sure?')) return;
    
    const typed = prompt('Type RESET to confirm factory reset:');
    if (typed !== 'RESET') {
        alert('Factory reset cancelled.');
        return;
    }
    
    enqueueAction(async () => {
        try {
            const res = await fetch(`${API_BASE}/api/admin/factory-reset`, { method: 'POST' });
            const data = await res.json();
            alert(`${data.status}\n\nArtworks removed: ${data.artworks_removed}\nFiles deleted: ${data.files_deleted}\nQueue items cleared: ${data.queue_items_cleared}\nSeed artworks preserved: ${data.seed_artworks_preserved}`);
            // Full page reload to reflect the reset state
            window.location.reload();
        } catch (error) { 
            console.error('[Admin] Factory reset failed:', error); 
            alert("Factory reset failed. Check console.");
        }
    });
}

async function batchEnrich() {
    if (!aiConfigured) { nudgeConnectModel(); return; }
    if (!confirm("Run RAG enrichment on the entire approved library? This uses AI and takes time.")) return;
    try {
        await fetch(`${API_BASE}/api/curate/batch-enrich`, { method: 'POST' });
        alert("Batch enrichment started in the background.");
    } catch (error) { console.error('[Admin] Batch enrich failed:', error); }
}

async function reenrichArtwork(id) {
    if (!aiConfigured) { nudgeConnectModel(); return; }
    const hint = prompt("AI Guidance (Optional):", "");
    if (hint === null) return; // Cancelled
    
    try {
        await fetch(`${API_BASE}/api/curate/reenrich/${id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hint: hint })
        });
        alert("Artwork sent back to Review Queue for re-enrichment.");
        await refreshData();
    } catch (error) { console.error('[Admin] Re-enrich failed:', error); }
}

function renderLibraryGrid() {
    const grid = document.getElementById('library-grid');
    
    // Prune obsolete cards BEFORE indexing to prevent 'leapfrog' detaching
    const newIds = new Set(fullLibrary.map(art => String(art.id)));
    Array.from(grid.children).forEach(card => {
        if (card.dataset.id && !newIds.has(card.dataset.id)) card.remove();
    });

    const existingCards = {};
    Array.from(grid.children).forEach(card => {
        if (card.dataset.id) existingCards[card.dataset.id] = card;
    });

    let currentDOMIndex = 0;

    fullLibrary.forEach(art => {
        const idStr = String(art.id);
        let card = existingCards[idStr];
        
        if (card) {
            delete existingCards[idStr];
            if (grid.children[currentDOMIndex] !== card) {
                grid.insertBefore(card, grid.children[currentDOMIndex]);
            }
        } else {
            card = document.createElement('div');
            card.className = 'artwork-card';
            card.dataset.id = art.id;
            card.innerHTML = `
                <img src="${API_BASE}/artworks/${art.id}/thumbnail?f=${encodeURIComponent(art.filename)}" alt="${art.filename}">
                <div class="info">
                    <strong>${art.title || art.filename}</strong><br>
                    <small>${art.agent_name || 'Unknown'}</small>${art.is_seed ? '<br><span style="color: #10b981; font-weight: bold; font-size: 0.75rem;">🌱 Built-In</span>' : ''}
                </div>
                <div class="actions" style="grid-template-columns: 1fr 1fr 1fr;">
                    <button onclick="openCropModal(${art.id})">Crop</button>
                    <button data-ai-action="1" onclick="reenrichArtwork(${art.id})" style="color: #3b82f6;">Enrich</button>
                    <button onclick="deleteArtworkPermanently(${art.id})" style="color: #ef4444;">Delete</button>
                </div>
            `;
            if (currentDOMIndex < grid.children.length) {
                grid.insertBefore(card, grid.children[currentDOMIndex]);
            } else {
                grid.appendChild(card);
            }
        }
        currentDOMIndex++;
    });
}

function renderArtworkGrid(artworks) {
    const grid = document.getElementById('artwork-grid');
    
    // Prune obsolete cards BEFORE indexing to prevent 'leapfrog' detaching
    const newIds = new Set(artworks.map(art => String(art.id)));
    Array.from(grid.children).forEach(card => {
        if (card.dataset.id && !newIds.has(card.dataset.id)) card.remove();
    });

    const existingCards = {};
    Array.from(grid.children).forEach(card => {
        if (card.dataset.id) existingCards[card.dataset.id] = card;
    });

    let currentDOMIndex = 0;

    artworks.forEach(art => {
        const idStr = String(art.id);
        let card = existingCards[idStr];
        
        if (card) {
            delete existingCards[idStr];
            if (grid.children[currentDOMIndex] !== card) {
                grid.insertBefore(card, grid.children[currentDOMIndex]);
            }
        } else {
            card = document.createElement('div');
            card.className = 'artwork-card';
            card.dataset.id = art.id;
            card.innerHTML = `
                <img src="${API_BASE}/artworks/${art.id}/thumbnail?f=${encodeURIComponent(art.filename)}" alt="${art.filename}">
                <div class="info">
                    <strong>${art.title || art.filename}</strong><br>
                    <small>${art.agent_name || 'Unknown'}</small>${art.is_seed ? '<br><span style="color: #10b981; font-weight: bold; font-size: 0.75rem;">🌱 Built-In</span>' : ''}
                </div>
                <div class="actions" style="grid-template-columns: 1fr 1fr 1fr;">
                    <button onclick="openCropModal(${art.id})">Crop</button>
                    <button data-ai-action="1" onclick="reenrichArtwork(${art.id})" style="color: #3b82f6;">Enrich</button>
                    <button onclick="removeArtworkFromPlaylist(${art.id})" style="color: #f59e0b;">Remove</button>
                </div>
            `;
            if (currentDOMIndex < grid.children.length) {
                grid.insertBefore(card, grid.children[currentDOMIndex]);
            } else {
                grid.appendChild(card);
            }
        }
        currentDOMIndex++;
    });

    setupSortable();
}

async function removeArtworkFromPlaylist(artworkId) {
    if (!currentPlaylistId) return;
    try {
        await fetch(`${API_BASE}/playlists/${currentPlaylistId}/artworks/${artworkId}`, { method: 'DELETE' });
        await refreshData();
    } catch (error) { console.error('[Admin] Unlink failed:', error); }
}

function deleteArtworkPermanently(id) {
    if (!confirm('PERMANENTLY delete this artwork from the library and all playlists? This wipes the file.')) return;
    enqueueAction(async () => {
        try {
            await fetch(`${API_BASE}/artworks/${id}`, { method: 'DELETE' });
        } catch (error) { console.error('[Admin] Delete failed:', error); }
    });
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
            <img src="${API_BASE}/artworks/${art.id}/thumbnail?f=${encodeURIComponent(art.filename)}">
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
                <div style="grid-column: span 2; margin-bottom: 5px; display: flex; align-items: center; gap: 10px;">
                    <label style="display:flex; align-items:center; gap:5px; cursor:pointer;">
                        <input type="checkbox" ${p.shuffle ? 'checked' : ''} onchange="updatePlaylistSetting(${p.id}, {shuffle: this.checked})" style="width:auto; margin:0;">
                        Randomize Order
                    </label>
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
    // Remember the open collection so a browser refresh returns to it instead of
    // resetting to the first collection in the list.
    try { localStorage.setItem('sd_admin_playlist', String(id)); } catch (e) {}
    const playlist = currentPlaylists.find(p => p.id === id);
    if (!playlist) return;
    document.querySelectorAll('.playlist-item').forEach(el => el.classList.toggle('active', parseInt(el.dataset.id) === id));
    document.getElementById('target-playlist-name').textContent = 'to ' + playlist.name;
    renderArtworkGrid(playlist.artworks || []);
    document.body.classList.remove('sidebar-open');  // close the mobile drawer on selection
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

    // No model connected → auto-analysis didn't run. Tell the user (no silent empty metadata),
    // and un-dismiss the Review Queue banner so the manual path is obvious.
    if (!aiConfigured) {
        const banner = document.getElementById('ai-offline-banner');
        if (banner) delete banner.dataset.dismissed;
        applyAiGating();
        showTransientNotice('Uploaded to the Review Queue. Auto-analysis is off — add details there, or connect a model.', nudgeConnectModel);
    }
}

// Minimal non-blocking toast (no framework). Optional action label jumps to the AI Engine panel.
function showTransientNotice(message, onAction) {
    const t = document.createElement('div');
    t.style.cssText = 'position:fixed; bottom:24px; left:50%; transform:translateX(-50%); z-index:9999; ' +
        'background:#1e293b; border:1px solid #f59e0b; color:#fcd34d; padding:12px 18px; border-radius:10px; ' +
        'font-size:0.82rem; max-width:520px; box-shadow:0 8px 24px rgba(0,0,0,0.4); display:flex; gap:14px; align-items:center;';
    const span = document.createElement('span');
    span.textContent = message;
    span.style.flexGrow = '1';
    t.appendChild(span);
    if (onAction) {
        const a = document.createElement('button');
        a.className = 'secondary';
        a.textContent = 'Connect a model';
        a.style.cssText = 'padding:5px 12px; font-size:0.75rem; white-space:nowrap;';
        a.onclick = () => { t.remove(); onAction(); };
        t.appendChild(a);
    }
    document.body.appendChild(t);
    setTimeout(() => { t.style.transition = 'opacity 0.5s'; t.style.opacity = '0'; setTimeout(() => t.remove(), 500); }, 6000);
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
    if (sortableInstance) sortableInstance.destroy();
    sortableInstance = new Sortable(grid, {
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

// Review-card input id prefix -> artwork property. Used to live-sync enrichment
// data onto already-rendered cards without clobbering manual edits.
const REVIEW_FIELDS = [
    ['title', 'title'],
    ['agent', 'agent_name'],
    ['role', 'agent_role'],
    ['date', 'creation_date'],
    ['context', 'cultural_context'],
    ['medium', 'medium'],
    ['date-display', 'date_display'],
    ['tags', 'tags'],
    ['desc', 'description_narrative'],
];

/**
 * Pushes the latest server values onto a card's inputs, but only where the user
 * hasn't diverged. Each input remembers the last server value in data-server; if
 * the current value still matches it (and the field isn't focused), we overwrite
 * with the new server value. This fills in blank cards as enrichment lands while
 * leaving any field the user has typed into untouched.
 */
function syncReviewCardFields(art) {
    REVIEW_FIELDS.forEach(([prefix, key]) => {
        const el = document.getElementById(`${prefix}-${art.id}`);
        if (!el) return;
        const serverVal = art[key] || '';
        if (document.activeElement === el) return; // never yank text from under the cursor
        if (el.value === (el.dataset.server || '')) {
            el.value = serverVal;
        }
        el.dataset.server = serverVal;
    });
}

function renderReviewQueue(artworks) {
    const list = document.getElementById('review-list');
    
    // Clear any empty message if artworks exist
    if (artworks.length > 0 && list.innerHTML.includes('Queue is empty')) {
        list.innerHTML = '';
    } else if (artworks.length === 0) {
        list.innerHTML = '<p style="text-align:center; color:#94a3b8; margin-top:40px;">Queue is empty.</p>';
        return;
    }
    
    // Prune obsolete cards BEFORE indexing to prevent 'leapfrog' detaching
    const newIds = new Set(artworks.map(a => String(a.id)));
    Array.from(list.children).forEach(card => {
        if (card.dataset.id && !newIds.has(card.dataset.id)) card.remove();
    });

    const existingCards = {};
    Array.from(list.children).forEach(card => {
        if (card.dataset.id) existingCards[card.dataset.id] = card;
    });

    let currentDOMIndex = 0;

    artworks.forEach(art => {
        const idStr = String(art.id);
        let card = existingCards[idStr];
        
        if (card) {
            delete existingCards[idStr];
            // Since obsolete siblings are already removed, if the index diverges, it's a genuine reorder
            if (list.children[currentDOMIndex] !== card) {
                list.insertBefore(card, list.children[currentDOMIndex]);
            }
        } else {
            card = document.createElement('div');
            card.className = 'review-card';
            card.dataset.id = art.id;
            card.innerHTML = `
                <div class="review-image"><img src="${API_BASE}/artworks/${art.id}/thumbnail?f=${encodeURIComponent(art.filename)}"></div>
                <div class="review-form">
                    <div class="form-group"><label>Title</label><input type="text" id="title-${art.id}" value="${art.title || ''}"></div>
                    <div class="form-group"><label>Agent/Artist</label><input type="text" id="agent-${art.id}" value="${art.agent_name || ''}"></div>
                    <div class="form-group"><label>Role</label><input type="text" id="role-${art.id}" value="${art.agent_role || ''}"></div>
                    <div class="form-group"><label>Date/Year</label><input type="text" id="date-${art.id}" value="${art.creation_date || ''}"></div>
                    <div class="form-group"><label>Context</label><input type="text" id="context-${art.id}" value="${art.cultural_context || ''}"></div>
                    <div class="form-group"><label>Medium</label><input type="text" id="medium-${art.id}" value="${art.medium || ''}"></div>
                    <div class="form-group"><label>Display Date</label><input type="text" id="date-display-${art.id}" value="${art.date_display || ''}"></div>
                    <div class="form-group"><label>Tags</label><input type="text" id="tags-${art.id}" value="${art.tags || ''}"></div>
                    <div class="form-group full"><label>Narrative Description</label><textarea id="desc-${art.id}" rows="3">${art.description_narrative || ''}</textarea></div>
                    <div class="form-group full" style="border-top: 1px solid var(--border-color); padding-top: 15px; margin-top: 5px;">
                        <label>AI Guidance (Optional)</label>
                        <div style="display: flex; gap: 10px;">
                            <input type="text" id="hint-${art.id}" placeholder="e.g., 'This is my dog Buster in 2021'" style="flex-grow: 1;">
                            <button class="primary" data-ai-action="1" id="regen-btn-${art.id}" onclick="regenerateArtworkMetadata(${art.id})" style="padding: 10px 20px;">
                                <span id="regen-text-${art.id}">Regenerate</span>
                            </button>
                        </div>
                    </div>
                    <div class="review-actions">
                        <button class="secondary" onclick="deleteArtworkPermanently(${art.id})">Delete</button>
                        <button class="success" onclick="approveArtwork(${art.id})">Approve & Publish</button>
                    </div>
                </div>
            `;
            if (currentDOMIndex < list.children.length) {
                list.insertBefore(card, list.children[currentDOMIndex]);
            } else {
                list.appendChild(card);
            }
        }
        // For new cards this just records the server baseline; for existing cards it
        // fills in any enrichment that has arrived since the card was first rendered.
        syncReviewCardFields(art);
        currentDOMIndex++;
    });

    applyAiGating(); // re-gate freshly rendered Regenerate buttons + toggle the no-AI banner
}

function regenerateArtworkMetadata(id) {
    if (!aiConfigured) { nudgeConnectModel(); return; }
    const hint = document.getElementById(`hint-${id}`).value;
    const btn = document.getElementById(`regen-btn-${id}`);
    const textSpan = document.getElementById(`regen-text-${id}`);
    
    // UI Feedback
    btn.disabled = true;
    btn.style.opacity = "0.7";
    const originalText = textSpan.textContent;
    textSpan.textContent = "Queued...";
    
    enqueueAction(async () => {
        textSpan.textContent = "Processing...";
        try {
            const response = await fetch(`${API_BASE}/api/curate/regenerate/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hint: hint })
            });
            
            if (!response.ok) throw new Error("Regeneration failed");
            
            const updatedArt = await response.json();
            
            // Dynamically update fields
            document.getElementById(`title-${id}`).value = updatedArt.title || '';
            document.getElementById(`agent-${id}`).value = updatedArt.agent_name || '';
            document.getElementById(`role-${id}`).value = updatedArt.agent_role || '';
            document.getElementById(`date-${id}`).value = updatedArt.creation_date || '';
            document.getElementById(`context-${id}`).value = updatedArt.cultural_context || '';
            document.getElementById(`medium-${id}`).value = updatedArt.medium || '';
            document.getElementById(`date-display-${id}`).value = updatedArt.date_display || '';
            document.getElementById(`tags-${id}`).value = updatedArt.tags || '';
            document.getElementById(`desc-${id}`).value = updatedArt.description_narrative || '';
            
            // Clear hint
            document.getElementById(`hint-${id}`).value = '';
            
        } catch (error) {
            console.error('[Admin] Regen failed:', error);
            alert("AI Regeneration failed. Check logs.");
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.style.opacity = "1";
                if(textSpan) textSpan.textContent = originalText;
            }
        }
    });
}

function approveArtwork(id) {
    const btn = document.querySelector(`#title-${id}`).closest('.review-form').querySelector('.success');
    btn.disabled = true;
    btn.textContent = "Queued...";
    btn.style.opacity = "0.7";
    
    const metadata = {
        title: document.getElementById(`title-${id}`).value,
        agent_name: document.getElementById(`agent-${id}`).value,
        agent_role: document.getElementById(`role-${id}`).value,
        creation_date: document.getElementById(`date-${id}`).value,
        cultural_context: document.getElementById(`context-${id}`).value,
        medium: document.getElementById(`medium-${id}`).value,
        date_display: document.getElementById(`date-display-${id}`).value,
        tags: document.getElementById(`tags-${id}`).value,
        description_narrative: document.getElementById(`desc-${id}`).value
    };
    
    enqueueAction(async () => {
        btn.textContent = "Approving...";
        try {
            await fetch(`${API_BASE}/artworks/${id}/approve`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(metadata)
            });
        } catch (error) { console.error('[Admin] Approval failed:', error); }
    });
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
    image.src = `${API_BASE}/artworks/${id}/preview?f=${encodeURIComponent(artwork.filename)}`;
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

// -----------------------------------------------------------------------------
// Freemium API Configuration (Tier-2 Scouts)
// -----------------------------------------------------------------------------
async function loadPremiumSettings() {
    try {
        const response = await fetch(`${API_BASE}/api/settings/keys`);
        const keys = await response.json();
        
        if (keys.harvard) unlockPremiumScout('harvard', 'Harvard Art Museums');
        if (keys.smithsonian) unlockPremiumScout('smithsonian', 'Smithsonian');
        if (keys.europeana) unlockPremiumScout('europeana', 'Europeana');

    } catch (e) {
        console.error("Failed to load settings:", e);
    }
}

async function promptApiKey(source, name, registerUrl) {
    const key = prompt(`Unlock ${name}?\n\nPlease enter your free developer API key.\nYou can generate one instantly at:\n${registerUrl}`);
    if (!key) return; // User cancelled

    const label = document.getElementById(`premium-${source}`);
    const originalContent = label.innerHTML;
    label.innerHTML = "⏳ Validating...";
    label.style.pointerEvents = "none";

    try {
        const response = await fetch(`${API_BASE}/api/settings/keys/${source}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: key })
        });
        
        const data = await response.json();
        if (!response.ok) {
            alert(data.detail || "Invalid Key");
            label.innerHTML = originalContent;
            label.style.pointerEvents = "auto";
            return;
        }

        // Success! Convert to standard checkbox
        alert(`Success! ${name} is now unlocked and available for scouting!`);
        unlockPremiumScout(source, name);
    } catch (e) {
        alert("Network error occurred validating API key.");
        label.innerHTML = originalContent;
        label.style.pointerEvents = "auto";
    }
}

function unlockPremiumScout(source, name) {
    const label = document.getElementById(`premium-${source}`);
    if (label) label.remove();
    
    // Add to standard checkboxes if it doesn't already exist
    const scoutsContainer = document.getElementById('scout-sources');
    if (!scoutsContainer) return;

    // Check if exists
    if (scoutsContainer.querySelector(`input[value="${source}"]`)) return;

    const newCb = document.createElement('label');
    newCb.style.cssText = "display: flex; align-items: center; gap: 6px; font-size: 0.8rem; cursor: pointer;";
    newCb.innerHTML = `<input type="checkbox" name="scout-source" value="${source}" checked style="width: auto; margin: 0;"> ${name}`;
    scoutsContainer.appendChild(newCb);
}

// -----------------------------------------------------------------------------
// AI Engine (model provider configuration)
// -----------------------------------------------------------------------------
let aiPresets = {};
let aiConfigured = false;   // mirrors /api/settings/ai has_key; gates AI-only controls

function _setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v || ''; }

// Soft-disable AI-only controls (Batch Enrich / Enrich / Regenerate) when no model is connected.
// Visual cue only; the handlers themselves enforce the block via nudgeConnectModel().
function applyAiGating() {
    document.querySelectorAll('[data-ai-action]').forEach(el => {
        el.classList.toggle('ai-gated', !aiConfigured);
        el.title = aiConfigured ? '' : 'Connect a model in Settings → AI Engine to use this';
    });
    // Review Queue banner: show only when there are pending items and no model is connected.
    const banner = document.getElementById('ai-offline-banner');
    if (banner && !banner.dataset.dismissed) {
        const hasPending = !!document.querySelector('#review-list .review-card');
        banner.style.display = (!aiConfigured && hasPending) ? 'flex' : 'none';
    }
}

// Send the user to the AI Engine panel and flash it (used when they trigger a gated action).
function nudgeConnectModel() {
    switchView('settings');
    const card = document.getElementById('ai-engine-card');
    if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.classList.remove('ai-flash');
        void card.offsetWidth; // restart the animation
        card.classList.add('ai-flash');
    }
}

async function loadAiSettings() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/ai`);
        const cfg = await resp.json();
        aiPresets = cfg.presets || {};
        aiConfigured = !!cfg.has_key;
        applyAiGating();

        const provSel = document.getElementById('ai-provider');
        if (!provSel) return; // panel not present
        provSel.innerHTML = '';
        Object.entries(aiPresets).forEach(([key, p]) => {
            const opt = document.createElement('option');
            opt.value = key; opt.textContent = p.label;
            provSel.appendChild(opt);
        });
        provSel.value = (cfg.provider in aiPresets) ? cfg.provider : 'gemini';

        renderAiModels(provSel.value, cfg.model);
        _setVal('ai-base-url', cfg.base_url);
        _setVal('ai-model-fast', cfg.model_fast);
        _setVal('ai-temp', cfg.temperature);
        applyProviderUI(provSel.value);

        const status = document.getElementById('ai-status');
        if (status) {
            if (cfg.has_key) {
                const src = cfg.key_source === 'env' ? ' (key from environment)' : '';
                const label = aiPresets[cfg.provider] ? aiPresets[cfg.provider].label : cfg.provider;
                status.innerHTML = `✓ Connected — <strong>${label}</strong> · ${cfg.model || '(default model)'}${src}`;
                status.style.color = '#34d399';
            } else {
                status.textContent = '⚠ No model configured yet — enrichment & smart search are disabled until you set one.';
                status.style.color = '#f59e0b';
            }
        }
    } catch (e) {
        console.error('Failed to load AI settings:', e);
    }
}

function renderAiModels(provider, selected) {
    const sel = document.getElementById('ai-model');
    if (!sel) return;
    const models = (aiPresets[provider] && aiPresets[provider].models) || [];
    sel.innerHTML = '';
    models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        sel.appendChild(opt);
    });
    const customOpt = document.createElement('option');
    customOpt.value = '__custom__'; customOpt.textContent = 'Custom…';
    sel.appendChild(customOpt);

    if (selected && models.includes(selected)) {
        sel.value = selected;
        toggleCustomModel(false);
    } else if (selected) {
        sel.value = '__custom__';
        toggleCustomModel(true, selected);
    } else {
        sel.selectedIndex = 0;
        toggleCustomModel(sel.value === '__custom__');
    }
}

function toggleCustomModel(show, val) {
    const inp = document.getElementById('ai-model-custom');
    if (!inp) return;
    inp.style.display = show ? 'block' : 'none';
    if (val !== undefined) inp.value = val || '';
}

function onAiModelChange() {
    const sel = document.getElementById('ai-model');
    toggleCustomModel(sel.value === '__custom__');
}

function applyProviderUI(provider) {
    const p = aiPresets[provider] || {};
    const keyRow = document.getElementById('ai-key-row');
    const oauthRow = document.getElementById('ai-oauth-row');
    const note = document.getElementById('ai-oauth-note');
    const link = document.getElementById('ai-key-link');
    // crypto.subtle (used by the OAuth PKCE flow) only works in a secure context
    // (HTTPS or localhost). Over http://<LAN-IP> it's unavailable, so fall back to paste-a-key.
    const oauthUsable = p.oauth && window.isSecureContext;
    if (keyRow) keyRow.style.display = oauthUsable ? 'none' : 'block';
    if (oauthRow) oauthRow.style.display = oauthUsable ? 'block' : 'none';
    if (note) note.style.display = (p.oauth && !window.isSecureContext) ? 'block' : 'none';
    if (link) {
        if (p.key_url) { link.style.display = 'inline'; link.href = p.key_url; }
        else { link.style.display = 'none'; }
    }
}

function onAiProviderChange() {
    const provider = document.getElementById('ai-provider').value;
    renderAiModels(provider, null);
    const base = document.getElementById('ai-base-url');
    if (base) base.value = (aiPresets[provider] && aiPresets[provider].base_url) || '';
    applyProviderUI(provider);
}

function currentAiModel() {
    const sel = document.getElementById('ai-model');
    if (sel.value === '__custom__') return document.getElementById('ai-model-custom').value.trim();
    return sel.value;
}

async function saveAiSettings() {
    const provider = document.getElementById('ai-provider').value;
    const model = currentAiModel();
    const result = document.getElementById('ai-save-result');
    const btn = document.getElementById('ai-save-btn');
    if (!model) { result.textContent = 'Please choose or enter a model.'; result.style.color = '#f59e0b'; return; }

    const payload = {
        provider,
        api_key: document.getElementById('ai-key').value.trim(),
        model,
        model_fast: document.getElementById('ai-model-fast').value.trim(),
        base_url: document.getElementById('ai-base-url').value.trim(),
        temperature: document.getElementById('ai-temp').value.trim()
    };

    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '⏳ Testing…'; result.textContent = '';
    try {
        const resp = await fetch(`${API_BASE}/api/settings/ai`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (!resp.ok) {
            result.textContent = '✗ ' + (data.detail || 'Save failed');
            result.style.color = '#ef4444';
        } else {
            result.textContent = '✓ Saved & verified';
            result.style.color = '#34d399';
            document.getElementById('ai-key').value = '';
            await loadAiSettings();
        }
    } catch (e) {
        result.textContent = '✗ Network error';
        result.style.color = '#ef4444';
    } finally {
        btn.disabled = false; btn.textContent = orig;
    }
}

// --- Samsung Frame TV ---
async function loadFrameSettings() {
    try {
        const [cfgResp, plResp] = await Promise.all([
            fetch(`${API_BASE}/api/settings/frame`),
            fetch(`${API_BASE}/playlists`)
        ]);
        const cfg = await cfgResp.json();
        const playlists = plResp.ok ? await plResp.json() : [];
        const sel = document.getElementById('frame-playlist');
        sel.innerHTML = '<option value="">First playlist (default)</option>' +
            playlists.map(p => `<option value="${_esc(p.name)}">${_esc(p.name)}</option>`).join('');
        document.getElementById('frame-enabled').checked = !!cfg.enabled;
        document.getElementById('frame-host').value = cfg.host || '';
        sel.value = cfg.playlist || '';
        const mins = Math.round((cfg.interval_sec || 900) / 60);
        const intSel = document.getElementById('frame-interval-min');
        if ([...intSel.options].some(o => o.value == mins)) intSel.value = String(mins);
        document.getElementById('frame-resolution').value = `${cfg.width || 3840}x${cfg.height || 2160}`;
        document.getElementById('frame-matte').value = cfg.matte || 'none';
        const status = document.getElementById('frame-status');
        status.textContent = cfg.last_push_at
            ? `Last pushed artwork #${cfg.last_artwork_id} at ${new Date(cfg.last_push_at * 1000).toLocaleString()}.`
            : '';
    } catch (e) { /* non-fatal: panel just shows defaults */ }
}

async function saveFrameSettings() {
    const result = document.getElementById('frame-save-result');
    const btn = document.getElementById('frame-save-btn');
    const [w, h] = document.getElementById('frame-resolution').value.split('x').map(Number);
    const payload = {
        enabled: document.getElementById('frame-enabled').checked,
        host: document.getElementById('frame-host').value.trim(),
        playlist: document.getElementById('frame-playlist').value,
        interval_sec: parseInt(document.getElementById('frame-interval-min').value, 10) * 60,
        width: w, height: h,
        matte: document.getElementById('frame-matte').value
    };
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '⏳ Saving…'; result.textContent = '';
    try {
        const resp = await fetch(`${API_BASE}/api/settings/frame`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (!resp.ok) { result.textContent = '✗ ' + (data.detail || 'Save failed'); result.style.color = '#ef4444'; }
        else { result.textContent = '✓ Saved'; result.style.color = '#34d399'; await loadFrameSettings(); }
    } catch (e) { result.textContent = '✗ Network error'; result.style.color = '#ef4444'; }
    finally { btn.disabled = false; btn.textContent = orig; }
}

async function testFramePush() {
    const result = document.getElementById('frame-save-result');
    const btn = document.getElementById('frame-test-btn');
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '⏳ Pushing…'; result.textContent = ' (uses saved settings)';
    try {
        const resp = await fetch(`${API_BASE}/api/settings/frame/test`, { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'pushed') { result.textContent = `✓ Pushed to Frame (artwork #${data.artwork_id})`; result.style.color = '#34d399'; }
        else if (data.status === 'unchanged') { result.textContent = '✓ Frame already shows the current artwork'; result.style.color = '#34d399'; }
        else if (data.status === 'skipped') { result.textContent = '⚠ ' + (data.reason || 'nothing to push'); result.style.color = '#f59e0b'; }
        else { result.textContent = '✗ ' + (data.reason || 'push failed'); result.style.color = '#ef4444'; }
    } catch (e) { result.textContent = '✗ Network error'; result.style.color = '#ef4444'; }
    finally { btn.disabled = false; btn.textContent = orig; }
}

// --- OpenRouter OAuth (PKCE) ---
function _b64url(bytes) {
    return btoa(String.fromCharCode(...new Uint8Array(bytes)))
        .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function _randomVerifier() {
    const arr = new Uint8Array(48);
    crypto.getRandomValues(arr);
    return _b64url(arr);
}
async function _pkceChallenge(verifier) {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
    return _b64url(digest);
}
async function startOpenRouterOAuth() {
    if (!window.isSecureContext) {
        alert('One-click sign-in needs HTTPS or localhost (the browser blocks the required crypto on plain http://IP). Paste an OpenRouter key instead, or open this admin page at http://localhost:8000/admin or over HTTPS.');
        return;
    }
    try {
        const verifier = _randomVerifier();
        const challenge = await _pkceChallenge(verifier);
        sessionStorage.setItem('sd_or_verifier', verifier);
        try { localStorage.setItem('sd_admin_view', 'settings'); } catch (e) {}
        const callback = window.location.origin + window.location.pathname;
        const resp = await fetch(`${API_BASE}/api/settings/ai/oauth/start?callback_url=${encodeURIComponent(callback)}&challenge=${encodeURIComponent(challenge)}`);
        const data = await resp.json();
        window.location.href = data.auth_url;
    } catch (e) {
        alert('Could not start OpenRouter sign-in: ' + e);
    }
}
async function handleOAuthCallback() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    if (!code) return;
    const verifier = sessionStorage.getItem('sd_or_verifier');
    history.replaceState({}, document.title, window.location.pathname); // strip ?code= from URL
    if (!verifier) return;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/ai/oauth/exchange`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, verifier })
        });
        const data = await resp.json();
        if (resp.ok) alert('✓ Connected to OpenRouter!');
        else alert('OpenRouter sign-in failed: ' + (data.detail || 'unknown error'));
    } catch (e) {
        alert('OpenRouter exchange error: ' + e);
    } finally {
        sessionStorage.removeItem('sd_or_verifier');
    }
}

// -----------------------------------------------------------------------------
// Browse Catalog (curated public-domain art; lazy high-res on add)
// -----------------------------------------------------------------------------
function _esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Level 1: the collections grid (covers + counts). Items load only when a collection is opened.
async function renderCatalog() {
    const container = document.getElementById('catalog-container');
    if (!container) return;
    container.innerHTML = '<p style="color:#94a3b8;">Loading catalog…</p>';
    try {
        const index = await (await fetch(`${API_BASE}/api/catalog`)).json();
        const collections = index.collections || [];
        const total = collections.reduce((n, c) => n + (c.count || 0), 0);
        const countEl = document.getElementById('catalog-count');
        if (countEl) countEl.textContent = total;

        if (!collections.length) {
            container.innerHTML = '<p style="color:#94a3b8;">No catalog collections are loaded. Screen Docent normally ships with a curated catalog — meanwhile, the <strong>Discover</strong> tab can search the world\'s museums directly.</p>';
            return;
        }
        const grid = document.createElement('div');
        grid.className = 'artwork-grid';
        collections.forEach(col => {
            const card = document.createElement('div');
            card.className = 'artwork-card';
            card.style.cursor = 'pointer';
            card.onclick = () => openCatalogCollection(col.id);
            card.innerHTML = `
                <img loading="lazy" src="${_esc(col.cover_thumbnail)}" alt="${_esc(col.title)}" style="background:#0f172a;">
                <div class="info">
                    <strong>${_esc(col.title)}</strong><br>
                    <small style="opacity:0.7">${_esc(col.description || '')}</small><br>
                    <small style="color:var(--accent-color)">${col.count} works →</small>
                </div>`;
            grid.appendChild(card);
        });
        container.innerHTML = '';
        container.appendChild(grid);
    } catch (e) {
        console.error('[Catalog] index load failed:', e);
        container.innerHTML = '<p style="color:#ef4444;">Failed to load catalog.</p>';
    }
}

// Level 2: one collection's items (lazy — only this collection's thumbnails load).
async function openCatalogCollection(collectionId) {
    const container = document.getElementById('catalog-container');
    if (!container) return;
    container.innerHTML = '<p style="color:#94a3b8;">Loading…</p>';
    try {
        const resp = await fetch(`${API_BASE}/api/catalog/${encodeURIComponent(collectionId)}`);
        if (!resp.ok) { container.innerHTML = '<p style="color:#ef4444;">Collection not found.</p>'; return; }
        const col = await resp.json();
        const items = col.items || [];

        const head = document.createElement('div');
        head.style.cssText = 'margin-bottom:16px;';
        head.innerHTML = `
            <button class="secondary" onclick="renderCatalog()" style="font-size:0.75rem; padding:6px 12px; margin-bottom:10px;">← All collections</button>
            <h3 style="margin:6px 0 2px; color:var(--text-color); font-size:1.05rem;">${_esc(col.title)}</h3>
            <span style="font-size:0.78rem; color:#94a3b8;">${_esc(col.description || '')}</span>
            <span style="display:block; font-size:0.68rem; color:#64748b; margin-top:4px;">${_esc(col.source || '')}${col.license ? ' · ' + _esc(col.license) : ''}</span>`;

        const grid = document.createElement('div');
        grid.className = 'artwork-grid';
        items.forEach((it, idx) => {
            const card = document.createElement('div');
            card.className = 'artwork-card';
            const added = !!it.added;
            card.innerHTML = `
                <img loading="lazy" src="${_esc(it.thumbnail_url)}" alt="${_esc(it.title)}" style="background:#0f172a;">
                <div class="info">
                    <strong>${_esc(it.title || 'Untitled')}</strong><br>
                    <small>${_esc(it.agent_name || 'Unknown')}</small><br>
                    <small style="opacity:0.6">${_esc(it.date_display || '')}</small>
                </div>
                <div class="actions">
                    <button class="success" ${added ? 'disabled' : ''} onclick="addCatalogItem('${_esc(col.id)}', ${idx}, this)">${added ? 'Added ✓' : 'Add to Library'}</button>
                </div>`;
            grid.appendChild(card);
        });
        container.innerHTML = '';
        container.appendChild(head);
        container.appendChild(grid);
    } catch (e) {
        console.error('[Catalog] collection load failed:', e);
        container.innerHTML = '<p style="color:#ef4444;">Failed to load collection.</p>';
    }
}

async function addCatalogItem(collectionId, itemIndex, btn) {
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '⏳ Adding…';
    try {
        const resp = await fetch(`${API_BASE}/api/catalog/add`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ collection_id: collectionId, item_index: itemIndex })
        });
        const data = await resp.json();
        if (!resp.ok) {
            alert(data.detail || 'Add failed');
            btn.disabled = false; btn.textContent = orig;
            return;
        }
        btn.textContent = 'Added ✓';
        fetchLibrary(); // refresh the Full Library count in the background
    } catch (e) {
        alert('Network error adding artwork.');
        btn.disabled = false; btn.textContent = orig;
    }
}
