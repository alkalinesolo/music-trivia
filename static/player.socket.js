socket.on('game_state', (data) => {
    if (data.state === 'guessing') {
        el.feedback.innerHTML = '';
        clearPlayerRevealTimers();
        stopAudio();
        unlockUiForGuessing();
        changeView('guessing');
        setHostControlMode('active');
    } else if (data.state === 'results') {
        if (!isGuessLocked && roundTimerDeadlineAt > 0 && Date.now() >= roundTimerDeadlineAt) {
            lockGuessOnTimeout();
        }
        clearRoundTimerHandles();
        changeView('results');
        setHostControlMode('active');
    } else if (data.state === 'lobby') {
        clearRoundTimerHandles();
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
    renderPlayerClues(data.clues, data.timing, mode, {
        roundNumber: data.round_number,
        totalRounds: data.rounds_per_game
    });

    if (mode === 'music_only') {
        loadRoundAudio(data.round_audio, {
            autoplay: isRoomHost,
            hideMetadata: true
        });
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
    answerHeader.innerHTML = `<strong>Song ${data.round_number || 0}/${data.rounds_per_game || 0} Answer:</strong> ${data.answer} by ${data.artist} (${data.year}, ${data.genre_label || data.genre})`;
    el.feedback.appendChild(answerHeader);

    if (data.game_over) {
        const endBanner = document.createElement('div');
        endBanner.className = 'status-banner';
        endBanner.style.marginTop = '8px';
        endBanner.innerText = 'Game complete. Start a new game from host controls.';
        el.feedback.appendChild(endBanner);
        setHostControlMode('lobby');
    } else {
        setHostControlMode('active');
    }

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
        const shouldAutoplayResultsPreview = isRoomHost && data.game_mode !== 'music_only';
        loadRoundAudio(previewSource, {
            autoplay: shouldAutoplayResultsPreview,
            hideMetadata: false
        });
        const audioHint = document.createElement('p');
        audioHint.className = 'muted';
        audioHint.style.margin = '10px 0 0 0';
        audioHint.innerText = shouldAutoplayResultsPreview
            ? 'Preview autoplayed for host controls.'
            : 'Preview ready. Tap Play to listen.';
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

    if (data.game_over && data.game_summary && Array.isArray(data.game_summary.players)) {
        const board = document.createElement('section');
        board.className = 'final-scoreboard';

        const boardTitle = document.createElement('h3');
        boardTitle.className = 'scoreboard-title';
        boardTitle.innerText = 'Final Scoreboard';
        board.appendChild(boardTitle);

        const boardSub = document.createElement('p');
        boardSub.className = 'scoreboard-subtitle';
        boardSub.innerText = 'Tap a player to expand song-by-song scoring.';
        board.appendChild(boardSub);

        data.game_summary.players.forEach((playerEntry, idx) => {
            const details = document.createElement('details');
            details.className = 'score-player';

            const summary = document.createElement('summary');
            summary.innerHTML = `
                <span class="rank-badge">#${idx + 1}</span>
                <span class="player-name">${playerEntry.player}</span>
                <span class="player-total">${playerEntry.score} pts</span>
            `;
            details.appendChild(summary);

            const songList = document.createElement('div');
            songList.className = 'score-song-list';

            playerEntry.songs.forEach(song => {
                const songItem = document.createElement('div');
                songItem.className = 'score-song-item';
                const noGuessBadge = song.no_guess ? '<span class="no-guess-pill">No Guess</span>' : '';

                songItem.innerHTML = `
                    <div class="song-row-head">
                        <strong>Song ${song.round_number}</strong>
                        <span class="song-round-points">+${song.round_score}</span>
                    </div>
                    <div class="song-row-meta">Answer: ${song.answer} by ${song.artist}</div>
                    <div class="song-row-meta">Guess: ${song.title_guess} | ${song.artist_guess} ${noGuessBadge}</div>
                    <div class="song-row-meta">Breakdown: title +${song.title_score}, artist +${song.artist_score}, both +${song.both_bonus}, early +${song.early_lock_bonus}</div>
                    <div class="song-row-total">Running Total: ${song.total_score} pts</div>
                `;
                songList.appendChild(songItem);
            });

            details.appendChild(songList);
            board.appendChild(details);
        });

        el.feedback.appendChild(board);
    }
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
