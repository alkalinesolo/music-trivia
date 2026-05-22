const socket = io();
const baseJoinURL = document.body.dataset.joinUrl || window.location.origin;
let activeRoomCode = "";
let hostCountdownInterval = null;
let hostRevealTimeouts = [];
let currentHostPreviewAudio = null;

const DECADES = ['1960s', '1970s', '1980s', '1990s', '2000s', '2010s', '2020s'];
const GENRES = ['rock', 'pop', 'country', 'hip-hop/r&b', 'reggae'];

let hostSettings = {
    rounds: 5,
    gameMode: 'full',
    decades: new Set(DECADES),
    genres: new Set(GENRES)
};

const qrCode = new QRCode(document.getElementById("qrcode"), {
    text: baseJoinURL,
    width: 200,
    height: 200
});

function initializeSettingsUI() {
    const decadesContainer = document.getElementById('decades-container');
    DECADES.forEach(decade => {
        const label = document.createElement('label');
        label.style.display = 'flex';
        label.style.alignItems = 'center';
        label.style.cursor = 'pointer';
        label.innerHTML = `<input type="checkbox" class="decade-checkbox" value="${decade}" checked style="margin-right: 8px;"> ${decade}`;
        decadesContainer.appendChild(label);
    });

    const genresContainer = document.getElementById('genres-container');
    GENRES.forEach(genre => {
        const label = document.createElement('label');
        label.style.display = 'flex';
        label.style.alignItems = 'center';
        label.style.cursor = 'pointer';
        label.innerHTML = `<input type="checkbox" class="genre-checkbox" value="${genre}" checked style="margin-right: 8px;"> ${genre}`;
        genresContainer.appendChild(label);
    });
}

function openSettings() {
    document.getElementById('settings-modal').classList.remove('hidden');
    document.getElementById('rounds-input').value = hostSettings.rounds;
    const fullModeEl = document.getElementById('game-mode-full');
    const quickModeEl = document.getElementById('game-mode-quick');
    const musicOnlyModeEl = document.getElementById('game-mode-music-only');
    fullModeEl.checked = hostSettings.gameMode === 'full';
    quickModeEl.checked = hostSettings.gameMode === 'quick';
    musicOnlyModeEl.checked = hostSettings.gameMode === 'music_only';
    document.querySelectorAll('.decade-checkbox').forEach(cb => {
        cb.checked = hostSettings.decades.has(cb.value);
    });
    document.querySelectorAll('.genre-checkbox').forEach(cb => {
        cb.checked = hostSettings.genres.has(cb.value);
    });
}

function closeSettings() {
    document.getElementById('settings-modal').classList.add('hidden');
}

function saveSettings() {
    hostSettings.rounds = parseInt(document.getElementById('rounds-input').value, 10) || 5;
    hostSettings.gameMode = document.querySelector('input[name="game-mode"]:checked')?.value || 'full';

    hostSettings.decades.clear();
    document.querySelectorAll('.decade-checkbox:checked').forEach(cb => {
        hostSettings.decades.add(cb.value);
    });

    hostSettings.genres.clear();
    document.querySelectorAll('.genre-checkbox:checked').forEach(cb => {
        hostSettings.genres.add(cb.value);
    });

    if (activeRoomCode) {
        socket.emit('update_game_settings', {
            room_code: activeRoomCode,
            rounds: hostSettings.rounds,
            game_mode: hostSettings.gameMode,
            decades: Array.from(hostSettings.decades),
            genres: Array.from(hostSettings.genres)
        });
    }

    closeSettings();
    alert('Settings saved!');
}

function syncHostSettingsFromServer(settings) {
    if (!settings) return;

    if (typeof settings.rounds === 'number') {
        hostSettings.rounds = settings.rounds;
    }

    if (settings.game_mode === 'quick' || settings.game_mode === 'music_only') {
        hostSettings.gameMode = settings.game_mode;
    } else {
        hostSettings.gameMode = 'full';
    }

    if (Array.isArray(settings.decades)) {
        hostSettings.decades = new Set(settings.decades);
    }

    if (Array.isArray(settings.genres)) {
        hostSettings.genres = new Set(settings.genres);
    }
}

function stopHostPreviewAudio() {
    if (!currentHostPreviewAudio) {
        return;
    }

    currentHostPreviewAudio.pause();
    currentHostPreviewAudio.currentTime = 0;
    currentHostPreviewAudio = null;
}

function playHostRoundAudio(roundAudio) {
    if (!roundAudio || !roundAudio.preview_url) {
        return;
    }

    const audio = new Audio(roundAudio.preview_url);
    audio.preload = 'auto';
    currentHostPreviewAudio = audio;
    audio.play().catch(() => {
        console.log('Host autoplay blocked for round preview');
    });
}

function sanitizeRoomCode(rawValue) {
    return (rawValue || '').trim().toUpperCase().replace(/[^A-Z0-9_-]/g, '').slice(0, 20);
}

function setHostRoom() {
    const input = document.getElementById('room-code-input');
    const cleanedRoomCode = sanitizeRoomCode(input.value);

    if (!cleanedRoomCode) {
        alert('Please enter a valid room code.');
        return;
    }

    activeRoomCode = cleanedRoomCode;
    input.value = cleanedRoomCode;

    const joinLink = `${baseJoinURL}/?room=${encodeURIComponent(activeRoomCode)}`;
    const joinLinkElement = document.getElementById('join-link');

    document.getElementById('active-room-code').innerText = activeRoomCode;
    joinLinkElement.href = joinLink;
    joinLinkElement.innerText = joinLink;

    qrCode.clear();
    qrCode.makeCode(joinLink);

    socket.emit('host_join', { room_code: activeRoomCode });
}

function startHostTimer(seconds) {
    if (hostCountdownInterval) {
        clearInterval(hostCountdownInterval);
    }

    let timeLeft = Number(seconds) || 0;
    const timerEl = document.getElementById('host-timer');
    timerEl.innerText = `Time Left: ${timeLeft}s`;

    hostCountdownInterval = setInterval(() => {
        timeLeft -= 1;
        timerEl.innerText = `Time Left: ${Math.max(0, timeLeft)}s`;

        if (timeLeft <= 0) {
            clearInterval(hostCountdownInterval);
            hostCountdownInterval = null;
        }
    }, 1000);
}

function clearHostRevealTimers() {
    hostRevealTimeouts.forEach(timerId => clearTimeout(timerId));
    hostRevealTimeouts = [];
}

function renderHostClues(clues, timing, roundInfo = {}) {
    const totalTime = Number((timing || {}).total_time) || 60;
    const remainingTime = Number((timing || {}).remaining_time) || totalTime;
    const elapsed = Math.max(0, totalTime - remainingTime);
    const lyric2At = Number((timing || {}).lyric2_reveal_at);
    const lyric1At = Number((timing || {}).lyric1_reveal_at);
    const quickLabel = clues.quick_label || 'Lyric';
    const roundNumber = Number(roundInfo.roundNumber) || 0;
    const totalRounds = Number(roundInfo.totalRounds) || 0;
    const songProgress = roundNumber > 0 && totalRounds > 0 ? `Song ${roundNumber}/${totalRounds} | ` : '';

    document.getElementById('host-meta').innerText = `${songProgress}Genre: ${clues.genre_label || clues.genre} | Year: ${clues.year}`;

    if (!Number.isFinite(lyric2At) && !Number.isFinite(lyric1At)) {
        document.getElementById('lyric3').innerText = clues.lyric3 ? `${quickLabel}: "${clues.lyric3}"` : '';
        document.getElementById('lyric2').innerText = '';
        document.getElementById('lyric1').innerText = '';
        return;
    }

    document.getElementById('lyric3').innerText = clues.lyric3 ? `Lyric 3: "${clues.lyric3}"` : '';

    if (elapsed >= lyric2At) {
        document.getElementById('lyric2').innerText = clues.lyric2 ? `Lyric 2: "${clues.lyric2}"` : '';
    } else {
        document.getElementById('lyric2').innerText = '';
        hostRevealTimeouts.push(setTimeout(() => {
            document.getElementById('lyric2').innerText = clues.lyric2 ? `Lyric 2: "${clues.lyric2}"` : '';
        }, (lyric2At - elapsed) * 1000));
    }

    if (elapsed >= lyric1At) {
        document.getElementById('lyric1').innerText = clues.lyric1 ? `Lyric 1: "${clues.lyric1}"` : '';
    } else {
        document.getElementById('lyric1').innerText = '';
        hostRevealTimeouts.push(setTimeout(() => {
            document.getElementById('lyric1').innerText = clues.lyric1 ? `Lyric 1: "${clues.lyric1}"` : '';
        }, (lyric1At - elapsed) * 1000));
    }
}

function showView(activeViewId) {
    document.querySelectorAll('.game-phase').forEach(view => {
        view.classList.add('hidden');
    });
    document.getElementById(activeViewId).classList.remove('hidden');
}

function startRound() {
    if (!activeRoomCode) {
        alert('Set a room code first.');
        return;
    }

    document.getElementById('results-view').innerHTML = '';
    document.getElementById('guessCount').innerText = 'Guesses: 0/0';
    document.getElementById('host-meta').innerText = '';
    document.getElementById('lyric3').innerText = '';
    document.getElementById('lyric2').innerText = '';
    document.getElementById('lyric1').innerText = '';
    clearHostRevealTimers();
    stopHostPreviewAudio();

    showView('game-view');

    document.getElementById('btn-start').innerText = 'Next Round';
    document.getElementById('btn-start').classList.add('hidden');
    document.getElementById('btn-results').classList.remove('hidden');

    socket.emit('start_round', { room_code: activeRoomCode });
}

function showResults() {
    document.getElementById('btn-results').classList.add('hidden');
    document.getElementById('btn-start').classList.remove('hidden');
    document.getElementById('btn-start').disabled = false;
    socket.emit('show_results', { room_code: activeRoomCode });
}

initializeSettingsUI();
