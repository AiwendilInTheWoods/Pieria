/**
 * app-core.js — shared front-end helpers for the non-admin pages (My Photos, Remote, Help).
 * These mirror the helpers in admin.js so every page gets the same API base, themed toasts,
 * and themed confirm/prompt modals. Pairs with the shared component CSS in app.css.
 * (admin.js still carries its own copies for now; deduping admin onto this is a follow-up.)
 */

const API_BASE = (window.location.origin === 'null' || window.location.protocol === 'file:')
    ? 'http://localhost:8000'
    : window.location.origin;

// Transient, themed feedback. type: '' | 'success' | 'error'. Self-bootstraps its container.
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

// Themed modal replacing native confirm()/prompt(). confirmModal -> Promise<bool>; promptModal -> Promise<string|null>.
function _buildModal({ message, input, placeholder = '', confirmText = 'OK', cancelText = 'Cancel', danger = false }) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.id = 'modal-overlay';
        const box = document.createElement('div');
        box.className = 'modal-box';
        const msg = document.createElement('p');
        msg.className = 'modal-msg';
        msg.textContent = message;
        box.appendChild(msg);
        let field = null;
        if (input) {
            field = document.createElement('input');
            field.placeholder = placeholder;
            box.appendChild(field);
        }
        const actions = document.createElement('div');
        actions.className = 'modal-actions';
        const cancel = document.createElement('button');
        cancel.textContent = cancelText;
        const ok = document.createElement('button');
        ok.className = 'btn-confirm' + (danger ? ' danger' : '');
        ok.textContent = confirmText;
        actions.append(cancel, ok);
        box.appendChild(actions);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        (field || ok).focus();
        const close = (val) => { overlay.remove(); resolve(val); };
        cancel.onclick = () => close(input ? null : false);
        ok.onclick = () => close(input ? (field ? field.value : '') : true);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(input ? null : false); });
        document.addEventListener('keydown', function esc(e) {
            if (e.key === 'Escape') { document.removeEventListener('keydown', esc); close(input ? null : false); }
        });
        if (field) field.addEventListener('keydown', (e) => { if (e.key === 'Enter') ok.click(); });
    });
}
function confirmModal(message, opts = {}) { return _buildModal({ message, input: false, ...opts }); }
function promptModal(message, opts = {}) { return _buildModal({ message, input: true, ...opts }); }
window.confirmModal = confirmModal;
window.promptModal = promptModal;

// HTML-escape for safe innerHTML interpolation.
function _esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
