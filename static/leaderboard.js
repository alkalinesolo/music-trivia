// Shared daily leaderboard modal logic
// Expects leaderboard-modal, leaderboard-date, leaderboard-tabs, leaderboard-body in the DOM
// and a global leaderboardRoomCode() function or variable supplied by the host page.

(function () {
    const MODE_LABELS = {
        quick: 'Quick',
        full: 'Full',
        music_only: 'Music Only',
    };
    const MODES = ['quick', 'full', 'music_only'];
    let activeLeaderboardMode = 'quick';

    function getRoomCode() {
        if (typeof leaderboardRoomCode === 'function') return leaderboardRoomCode();
        if (typeof leaderboardRoomCode === 'string') return leaderboardRoomCode;
        return '';
    }

    function openLeaderboard() {
        const roomCode = getRoomCode();
        if (!roomCode) {
            alert('No active room.');
            return;
        }

        document.getElementById('leaderboard-modal').style.display = 'flex';
        loadLeaderboard(roomCode, activeLeaderboardMode);
    }

    function closeLeaderboard() {
        document.getElementById('leaderboard-modal').style.display = 'none';
    }

    function switchLeaderboardTab(mode) {
        activeLeaderboardMode = mode;
        document.querySelectorAll('.lb-tab').forEach(btn => {
            btn.classList.toggle('lb-tab-active', btn.dataset.mode === mode);
        });
        const roomCode = getRoomCode();
        if (roomCode) loadLeaderboard(roomCode, mode);
    }

    function loadLeaderboard(roomCode, mode) {
        const body = document.getElementById('leaderboard-body');
        const dateEl = document.getElementById('leaderboard-date');
        body.innerHTML = '<p style="color:#888; text-align:center;">Loading…</p>';

        fetch(`/api/leaderboard/${encodeURIComponent(roomCode)}`)
            .then(r => r.json())
            .then(data => {
                if (dateEl) dateEl.innerText = `Today: ${data.date} (Pacific)`;
                renderLeaderboardEntries(body, data[mode] || []);
            })
            .catch(() => {
                body.innerHTML = '<p style="color:#f66; text-align:center;">Failed to load leaderboard.</p>';
            });
    }

    function renderLeaderboardEntries(container, entries) {
        container.innerHTML = '';
        if (!entries || entries.length === 0) {
            container.innerHTML = '<p style="color:#888; text-align:center;">No games recorded yet today.</p>';
            return;
        }

        entries.forEach((entry, idx) => {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex; align-items:center; gap:12px; padding:10px 14px; background:#1e1e1e; border-radius:8px; margin-bottom:8px;';

            const medal = idx === 0 ? '🥇' : idx === 1 ? '🥈' : idx === 2 ? '🥉' : `#${idx + 1}`;
            row.innerHTML = `
                <span style="font-size:1.25rem; min-width:2rem; text-align:center;">${medal}</span>
                <span style="flex:1; font-weight:600; color:#e8e8e8;">${escapeHtml(entry.player)}</span>
                <span style="font-size:1.1rem; font-weight:700; color:#1db954;">${entry.score} pts</span>
            `;
            container.appendChild(row);
        });
    }

    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // Inject modal HTML once the DOM is ready
    function injectLeaderboardModal() {
        const backdrop = document.createElement('div');
        backdrop.id = 'leaderboard-modal';
        backdrop.style.cssText = 'display:none; position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:9999; align-items:center; justify-content:center;';
        backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeLeaderboard(); });

        const tabButtons = MODES.map(m =>
            `<button class="lb-tab${m === 'quick' ? ' lb-tab-active' : ''}" data-mode="${m}" onclick="leaderboardSwitchTab('${m}')">${MODE_LABELS[m]}</button>`
        ).join('');

        backdrop.innerHTML = `
            <div style="background:#171717; border:1px solid #333; border-radius:14px; padding:20px 18px; width:min(420px,94vw); max-height:80vh; display:flex; flex-direction:column; gap:12px;">
                <div style="display:flex; align-items:center; justify-content:space-between;">
                    <h2 style="margin:0; color:#fff; font-size:1.15rem;">Daily Leaderboard</h2>
                    <button onclick="leaderboardClose()" style="background:none; border:none; color:#aaa; font-size:1.4rem; cursor:pointer; line-height:1; padding:0 4px;">&times;</button>
                </div>
                <p id="leaderboard-date" style="margin:0; color:#888; font-size:0.8rem;"></p>
                <div id="leaderboard-tabs" style="display:flex; gap:6px;">
                    ${tabButtons}
                </div>
                <div id="leaderboard-body" style="overflow-y:auto; flex:1;"></div>
            </div>
        `;

        document.body.appendChild(backdrop);

        // Inject tab styles
        const style = document.createElement('style');
        style.textContent = `
            .lb-tab {
                flex: 1;
                padding: 7px 10px;
                border-radius: 8px;
                border: 1px solid #444;
                background: #252525;
                color: #ccc;
                cursor: pointer;
                font-size: 0.85rem;
                transition: background 0.15s, color 0.15s;
            }
            .lb-tab:hover { background: #333; }
            .lb-tab-active {
                background: #1db954;
                color: #000;
                border-color: #1db954;
                font-weight: 700;
            }
        `;
        document.head.appendChild(style);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', injectLeaderboardModal);
    } else {
        injectLeaderboardModal();
    }

    // Expose globals
    window.leaderboardOpen = openLeaderboard;
    window.leaderboardClose = closeLeaderboard;
    window.leaderboardSwitchTab = switchLeaderboardTab;
}());
