const socket = io();

const playerNameRaw = document.body.dataset.playerName || '';
const playerNameNormalized = playerNameRaw.trim().toLowerCase();
const playerRoomCode = document.body.dataset.roomCode || '';

const DECADES = ['1960s', '1970s', '1980s', '1990s', '2000s', '2010s', '2020s'];
const GENRES = ['rock', 'pop', 'country', 'hip-hop/r&b', 'motown', 'reggae', 'grunge'];

let countdownInterval = null;
let playerRevealTimeouts = [];
let isRoomHost = false;
let activeAudio = null;
let isGuessLocked = false;
let currentRoundTotalTime = 0;
let hostControlMode = 'lobby';
let hostSettings = {
    rounds: 5,
    gameMode: 'full',
    decades: new Set(DECADES),
    genres: new Set(GENRES)
};

const el = {
    views: {
        lobby: document.getElementById('lobby-view'),
        guessing: document.getElementById('guessing-view'),
        results: document.getElementById('results-view')
    },
    hostControls: document.getElementById('host-controls'),
    hostLobbyControls: document.getElementById('host-controls-lobby'),
    hostActiveControls: document.getElementById('host-controls-active'),
    playerMeta: document.getElementById('player-meta'),
    lyric3: document.getElementById('player-lyric3'),
    lyric2: document.getElementById('player-lyric2'),
    lyric1: document.getElementById('player-lyric1'),
    titleInput: document.getElementById('player-title-guess'),
    artistInput: document.getElementById('player-artist-guess'),
    submitBtn: document.getElementById('btn-submit'),
    inputArea: document.getElementById('input-area'),
    lockStatus: document.getElementById('lock-status'),
    timerDisplay: document.getElementById('timer-display'),
    timerProgress: document.getElementById('timer-progress'),
    feedback: document.getElementById('personal-feedback'),
    settingsModal: document.getElementById('player-settings-modal'),
    roundsInput: document.getElementById('player-rounds-input'),
    audioWrap: document.getElementById('round-audio-player'),
    audioTrack: document.getElementById('audio-track'),
    audioPlayBtn: document.getElementById('audio-play-btn'),
    audioProgress: document.getElementById('audio-progress'),
    audioCurrent: document.getElementById('audio-current'),
    audioTotal: document.getElementById('audio-total')
};

socket.emit('player_join', { name: playerNameRaw, room_code: playerRoomCode });

function formatSeconds(totalSeconds) {
    const safe = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const minutes = Math.floor(safe / 60);
    const seconds = safe % 60;
    return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function clearPlayerRevealTimers() {
    playerRevealTimeouts.forEach(timerId => clearTimeout(timerId));
    playerRevealTimeouts = [];
}

function changeView(activeViewId) {
    Object.values(el.views).forEach(view => {
        view.classList.remove('active');
    });
    el.views[activeViewId].classList.add('active');
}

function setHostControlsVisibility() {
    if (isRoomHost) {
        el.hostControls.classList.add('visible');
    } else {
        el.hostControls.classList.remove('visible');
    }
}

function setHostControlMode(mode) {
    hostControlMode = mode;
    if (!isRoomHost) {
        return;
    }

    if (mode === 'active') {
        el.hostLobbyControls.classList.add('hidden');
        el.hostActiveControls.classList.remove('hidden');
    } else {
        el.hostLobbyControls.classList.remove('hidden');
        el.hostActiveControls.classList.add('hidden');
    }
}

function stopAudio() {
    if (!activeAudio) {
        return;
    }

    activeAudio.pause();
    activeAudio.currentTime = 0;
    activeAudio.src = '';
    activeAudio = null;
    el.audioWrap.classList.add('hidden');
    el.audioPlayBtn.innerText = 'Play';
    el.audioProgress.value = 0;
    el.audioCurrent.innerText = '0:00';
    el.audioTotal.innerText = '0:00';
}

function syncAudioUi() {
    if (!activeAudio) {
        return;
    }

    const duration = Number(activeAudio.duration) || 0;
    const current = Number(activeAudio.currentTime) || 0;
    const percent = duration > 0 ? Math.min(100, (current / duration) * 100) : 0;
    el.audioProgress.value = String(percent);
    el.audioCurrent.innerText = formatSeconds(current);
    el.audioTotal.innerText = formatSeconds(duration);
    el.audioPlayBtn.innerText = activeAudio.paused ? 'Play' : 'Pause';
}

function loadRoundAudio(audioData, autoplay) {
    if (!audioData || !audioData.preview_url) {
        el.audioWrap.classList.add('hidden');
        return;
    }

    stopAudio();
    activeAudio = new Audio(audioData.preview_url);
    activeAudio.preload = 'auto';
    activeAudio.addEventListener('timeupdate', syncAudioUi);
    activeAudio.addEventListener('loadedmetadata', syncAudioUi);
    activeAudio.addEventListener('ended', syncAudioUi);

    el.audioTrack.innerText = `${audioData.track_name || 'Preview'} - ${audioData.artist_name || ''}`;
    el.audioWrap.classList.remove('hidden');
    syncAudioUi();

    if (autoplay) {
        activeAudio.play().then(syncAudioUi).catch(() => {
            console.log('Autoplay blocked');
            syncAudioUi();
        });
    }
}

el.audioPlayBtn.addEventListener('click', () => {
    if (!activeAudio) {
        return;
    }

    if (activeAudio.paused) {
        activeAudio.play().catch(() => {
            console.log('Play blocked');
        });
    } else {
        activeAudio.pause();
    }
    syncAudioUi();
});

el.audioProgress.addEventListener('input', (event) => {
    if (!activeAudio || !Number(activeAudio.duration)) {
        return;
    }

    const percent = Number(event.target.value) / 100;
    activeAudio.currentTime = activeAudio.duration * percent;
    syncAudioUi();
});

function initializePlayerSettingsUI() {
    const decadesContainer = document.getElementById('player-decades-container');
    DECADES.forEach(decade => {
        const label = document.createElement('label');
        label.className = 'option-item';
        label.innerHTML = `<input type="checkbox" class="player-decade-checkbox" value="${decade}" checked> <span>${decade}</span>`;
        decadesContainer.appendChild(label);
    });

    const genresContainer = document.getElementById('player-genres-container');
    GENRES.forEach(genre => {
        const label = document.createElement('label');
        label.className = 'option-item';
        label.innerHTML = `<input type="checkbox" class="player-genre-checkbox" value="${genre}" checked> <span>${genre}</span>`;
        genresContainer.appendChild(label);
    });
}

function syncHostSettingsFromServer(settings) {
    if (!settings) {
        return;
    }

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

function openHostSettings() {
    if (!isRoomHost) {
        return;
    }

    el.roundsInput.value = hostSettings.rounds;
    document.getElementById('player-game-mode-full').checked = hostSettings.gameMode === 'full';
    document.getElementById('player-game-mode-quick').checked = hostSettings.gameMode === 'quick';
    document.getElementById('player-game-mode-music-only').checked = hostSettings.gameMode === 'music_only';

    document.querySelectorAll('.player-decade-checkbox').forEach(cb => {
        cb.checked = hostSettings.decades.has(cb.value);
    });
    document.querySelectorAll('.player-genre-checkbox').forEach(cb => {
        cb.checked = hostSettings.genres.has(cb.value);
    });

    el.settingsModal.classList.add('open');
}

function closeHostSettings() {
    el.settingsModal.classList.remove('open');
}

function saveHostSettings() {
    if (!isRoomHost) {
        return;
    }

    const roundsValue = parseInt(el.roundsInput.value, 10);
    hostSettings.rounds = Number.isFinite(roundsValue) ? roundsValue : 5;
    hostSettings.gameMode = document.querySelector('input[name="player-game-mode"]:checked')?.value || 'full';

    hostSettings.decades.clear();
    hostSettings.genres.clear();
    document.querySelectorAll('.player-decade-checkbox:checked').forEach(cb => hostSettings.decades.add(cb.value));
    document.querySelectorAll('.player-genre-checkbox:checked').forEach(cb => hostSettings.genres.add(cb.value));

    socket.emit('update_game_settings', {
        room_code: playerRoomCode,
        rounds: hostSettings.rounds,
        game_mode: hostSettings.gameMode,
        decades: Array.from(hostSettings.decades),
        genres: Array.from(hostSettings.genres)
    });

    closeHostSettings();
}

function startRoundAsHost() {
    if (!isRoomHost) {
        return;
    }
    socket.emit('start_round', { room_code: playerRoomCode });
}

function nextSongAsHost() {
    startRoundAsHost();
}

function showResultsAsHost() {
    if (!isRoomHost) {
        return;
    }
    socket.emit('show_results', { room_code: playerRoomCode });
}

function exitGameAsHost() {
    stopAudio();
    window.location.href = '/';
}

function lockUiForGuess(messageText) {
    el.titleInput.disabled = true;
    el.artistInput.disabled = true;
    el.submitBtn.disabled = true;
    el.inputArea.classList.add('hidden');
    el.lockStatus.innerText = messageText;
    el.lockStatus.classList.remove('hidden');
}

function unlockUiForGuessing() {
    isGuessLocked = false;
    el.titleInput.disabled = false;
    el.artistInput.disabled = false;
    el.submitBtn.disabled = false;
    el.inputArea.classList.remove('hidden');
    el.lockStatus.classList.add('hidden');
}

function submitGuess(options = {}) {
    const autoLock = !!options.autoLock;

    if (isGuessLocked) {
        return;
    }

    const titleGuessValue = el.titleInput.value.trim();
    const artistGuessValue = el.artistInput.value.trim();
    if (!titleGuessValue && !artistGuessValue && !autoLock) {
        return;
    }

    isGuessLocked = true;
    if (autoLock) {
        lockUiForGuess('Time expired. Guess auto-locked.');
    } else {
        lockUiForGuess('Guess locked. Waiting for round to end.');
    }

    socket.emit('submit_guess', {
        name: playerNameRaw,
        room_code: playerRoomCode,
        title_guess: titleGuessValue,
        artist_guess: artistGuessValue,
        auto_lock: autoLock
    });
}

function updateTimerVisual(timeLeft, totalTime) {
    const safeLeft = Math.max(0, timeLeft);
    el.timerDisplay.innerText = `${safeLeft}s`;

    const pct = totalTime > 0 ? (safeLeft / totalTime) * 100 : 0;
    el.timerProgress.style.width = `${Math.max(0, Math.min(100, pct))}%`;

    if (safeLeft <= 5) {
        el.timerDisplay.classList.add('warning');
    } else {
        el.timerDisplay.classList.remove('warning');
    }
}

function lockGuessOnTimeout() {
    if (isGuessLocked) {
        return;
    }
    submitGuess({ autoLock: true });
}

function startUiTimer(seconds) {
    if (countdownInterval) {
        clearInterval(countdownInterval);
    }

    let timeLeft = Math.max(0, Number(seconds) || 0);
    currentRoundTotalTime = Math.max(timeLeft, 1);
    updateTimerVisual(timeLeft, currentRoundTotalTime);

    countdownInterval = setInterval(() => {
        timeLeft -= 1;
        updateTimerVisual(timeLeft, currentRoundTotalTime);

        if (timeLeft <= 0) {
            clearInterval(countdownInterval);
            countdownInterval = null;
            lockGuessOnTimeout();
        }
    }, 1000);
}

function setClue(element, text) {
    if (text) {
        element.innerText = text;
        element.classList.remove('hidden');
    } else {
        element.innerText = '';
        element.classList.add('hidden');
    }
}

function renderPlayerClues(clues, timing, gameMode) {
    const totalTime = Number((timing || {}).total_time) || 60;
    const remainingTime = Number((timing || {}).remaining_time) || totalTime;
    const elapsed = Math.max(0, totalTime - remainingTime);
    const lyric2At = Number((timing || {}).lyric2_reveal_at);
    const lyric1At = Number((timing || {}).lyric1_reveal_at);

    el.playerMeta.innerText = `Genre: ${clues.genre_label || clues.genre} | Year: ${clues.year}`;

    if (gameMode === 'music_only') {
        setClue(el.lyric3, 'Music Only round. Use the preview player to identify the track.');
        setClue(el.lyric2, '');
        setClue(el.lyric1, '');
        return;
    }

    if (!Number.isFinite(lyric2At) && !Number.isFinite(lyric1At)) {
        const quickLabel = clues.quick_label || 'Lyric';
        setClue(el.lyric3, clues.lyric3 ? `${quickLabel}: "${clues.lyric3}"` : '');
        setClue(el.lyric2, '');
        setClue(el.lyric1, '');
        return;
    }

    setClue(el.lyric3, clues.lyric3 ? `Lyric 3: "${clues.lyric3}"` : '');

    if (elapsed >= lyric2At) {
        setClue(el.lyric2, clues.lyric2 ? `Lyric 2: "${clues.lyric2}"` : '');
    } else {
        setClue(el.lyric2, '');
        playerRevealTimeouts.push(setTimeout(() => {
            setClue(el.lyric2, clues.lyric2 ? `Lyric 2: "${clues.lyric2}"` : '');
        }, (lyric2At - elapsed) * 1000));
    }

    if (elapsed >= lyric1At) {
        setClue(el.lyric1, clues.lyric1 ? `Lyric 1: "${clues.lyric1}"` : '');
    } else {
        setClue(el.lyric1, '');
        playerRevealTimeouts.push(setTimeout(() => {
            setClue(el.lyric1, clues.lyric1 ? `Lyric 1: "${clues.lyric1}"` : '');
        }, (lyric1At - elapsed) * 1000));
    }
}

function buildResultLine(label, value) {
    return `<div class="guess-meta"><strong>${label}:</strong> ${value}</div>`;
}

initializePlayerSettingsUI();
setHostControlMode('lobby');
