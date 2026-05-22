socket.on('game_state', (data) => {
    if (data.state === 'guessing') {
        el.feedback.innerHTML = '';
        clearPlayerRevealTimers();
        stopAudio();
        unlockUiForGuessing();
        changeView('guessing');
        setHostControlMode('active');
    } else if (data.state === 'results') {
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }
        changeView('results');
        setHostControlMode('active');
    } else if (data.state === 'lobby') {
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }
        clearPlayerRevealTimers();
        stopAudio();
        changeView('lobby');
        setHostControlMode('lobby');
    }
});

socket.on('player_role', (data) => {
    isRoomHost = !!(data && data.is_host);
    syncHostSettingsFromServer(data && data.settings);
    setHostControlsVisibility();
    setHostControlMode(hostControlMode);
});

socket.on('new_round', (data) => {
    clearPlayerRevealTimers();
    stopAudio();
    unlockUiForGuessing();

    el.titleInput.value = '';
    el.artistInput.value = '';

    const mode = data.game_mode || 'full';
    renderPlayerClues(data.clues, data.timing, mode);

    if (mode === 'music_only') {
        loadRoundAudio(data.round_audio, isRoomHost);
    }

    if (data.timing && data.timing.remaining_time !== undefined) {
        startUiTimer(data.timing.remaining_time);
    }
});

socket.on('round_results', (data) => {
    stopAudio();
    changeView('results');
    el.feedback.innerHTML = '';

    const answerHeader = document.createElement('div');
    answerHeader.className = 'status-banner';
    answerHeader.innerHTML = `<strong>Answer:</strong> ${data.answer} by ${data.artist} (${data.year}, ${data.genre_label || data.genre})`;
    el.feedback.appendChild(answerHeader);

    const myResult = data.results.find(result => result.player.trim().toLowerCase() === playerNameNormalized);

    if (myResult) {
        const mine = document.createElement('div');
        mine.className = 'guess-item';

        if (myResult.no_guess) {
            mine.innerHTML = `
                <div class="guess-player warning">Time ran out before you guessed.</div>
                <div class="score-display warning">+0</div>
                ${buildResultLine('Running Total', `<strong>${myResult.total_score} pts</strong>`) }
            `;
        } else {
            mine.innerHTML = `
                <div class="guess-player">Your Round</div>
                ${buildResultLine('Song Guess', `<strong>${myResult.title_guess || '[No Guess]'}</strong>`) }
                ${buildResultLine('Artist Guess', `<strong>${myResult.artist_guess || '[No Guess]'}</strong>`) }
                ${buildResultLine('Title Points', `+${myResult.title_score}`)}
                ${buildResultLine('Artist Points', `+${myResult.artist_score}`)}
                ${buildResultLine('Both Bonus', `+${myResult.both_bonus}`)}
                ${buildResultLine('Early Bonus', `+${myResult.early_lock_bonus}`)}
                <div class="score-display">+${myResult.round_score}</div>
                ${buildResultLine('Running Total', `<strong>${myResult.total_score} pts</strong>`) }
            `;
        }

        el.feedback.appendChild(mine);
    }

    const previewSource = data.preview && data.preview.preview_url ? data.preview : null;
    if (previewSource) {
        loadRoundAudio(previewSource, isRoomHost);
        const audioHint = document.createElement('p');
        audioHint.className = 'muted';
        audioHint.style.margin = '10px 0 0 0';
        audioHint.innerText = isRoomHost ? 'Preview autoplayed for host controls.' : 'Preview ready. Tap Play to listen.';
        el.feedback.appendChild(audioHint);
    }

    const listTitle = document.createElement('h3');
    listTitle.style.margin = '14px 0 8px 0';
    listTitle.innerText = 'All Player Guesses';
    el.feedback.appendChild(listTitle);

    const guessList = document.createElement('div');
    guessList.className = 'guess-list';
    data.results.forEach(result => {
        const card = document.createElement('div');
        card.className = 'guess-item';
        card.innerHTML = `
            <div class="guess-player">${result.player}</div>
            <div class="guess-meta">Song: <strong>${result.title_guess || '[No Guess]'}</strong></div>
            <div class="guess-meta">Artist: <strong>${result.artist_guess || '[No Guess]'}</strong></div>
            <div class="score-chip">Round +${result.round_score}</div>
        `;
        guessList.appendChild(card);
    });
    el.feedback.appendChild(guessList);
});

socket.on('guess_locked', () => {
    isGuessLocked = true;
});

socket.on('settings_updated', (data) => {
    syncHostSettingsFromServer(data && data.settings);
});

socket.on('host_error', (data) => {
    if (data && data.message) {
        alert(data.message);
    }
});

window.addEventListener('beforeunload', () => {
    stopAudio();
});
