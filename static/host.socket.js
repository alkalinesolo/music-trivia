socket.on('host_error', (data) => {
    if (data && data.message) {
        alert(data.message);
    }
});

socket.on('host_room_joined', (data) => {
    activeRoomCode = data.room_code;
    document.getElementById('active-room-code').innerText = data.room_code;

    const list = document.getElementById('players');
    list.innerHTML = '';
    data.players.forEach(player => {
        const li = document.createElement('li');
        li.textContent = player;
        list.appendChild(li);
    });

    syncHostSettingsFromServer(data.settings);

    document.getElementById('btn-start').disabled = false;
    document.getElementById('btn-settings').disabled = false;
});

socket.on('settings_updated', (data) => {
    syncHostSettingsFromServer(data && data.settings);
    console.log('Settings updated on host screen');
});

socket.on('update_players', (data) => {
    const list = document.getElementById('players');
    list.innerHTML = '';

    data.players.forEach(player => {
        const li = document.createElement('li');
        li.textContent = player;
        list.appendChild(li);
    });

    document.getElementById('btn-start').disabled = false;
});

socket.on('host_round_started', (data) => {
    stopHostPreviewAudio();

    document.getElementById('btn-start').classList.add('hidden');
    document.getElementById('btn-results').classList.remove('hidden');
    document.getElementById('btn-results').disabled = false;

    clearHostRevealTimers();
    const remainingTime = data.timing.remaining_time;

    renderHostClues(data.clues, data.timing);
    if (data.game_mode === 'music_only') {
        playHostRoundAudio(data.round_audio);
    }
    document.getElementById('guessCount').innerText = `Guesses: 0/${data.total_players}`;
    startHostTimer(remainingTime);
});

socket.on('guess_count', (data) => {
    document.getElementById('guessCount').innerText = `Guesses: ${data.count}/${data.total}`;
});

socket.on('game_state', (data) => {
    if (data.state === 'guessing') {
        stopHostPreviewAudio();
    }

    if (data.state === 'results') {
        if (hostCountdownInterval) {
            clearInterval(hostCountdownInterval);
            hostCountdownInterval = null;
        }
        document.getElementById('host-timer').innerText = 'Time Left: 0s';
    }
});

socket.on('round_results', (data) => {
    stopHostPreviewAudio();

    if (hostCountdownInterval) {
        clearInterval(hostCountdownInterval);
        hostCountdownInterval = null;
    }
    document.getElementById('host-timer').innerText = 'Time Left: 0s';

    const resultsDiv = document.getElementById('results-view');
    resultsDiv.innerHTML = '';

    document.getElementById('btn-results').classList.add('hidden');
    const startBtn = document.getElementById('btn-start');
    startBtn.classList.remove('hidden');
    startBtn.disabled = false;
    startBtn.innerText = 'Start Round';

    showView('results-view');

    const title = document.createElement('h2');
    title.style.color = '#1db954';
    title.innerText = `Answer: ${data.answer} by ${data.artist} (${data.year}, ${data.genre_label || data.genre})`;
    resultsDiv.appendChild(title);

    if (data.preview && data.preview.preview_url) {
        const audioTitle = document.createElement('p');
        audioTitle.innerHTML = `<strong>iTunes Preview:</strong> ${data.preview.track_name} - ${data.preview.artist_name}`;
        resultsDiv.appendChild(audioTitle);

        const audio = document.createElement('audio');
        audio.controls = true;
        audio.autoplay = true;
        audio.preload = 'metadata';
        audio.src = data.preview.preview_url;
        audio.style.marginBottom = '16px';
        currentHostPreviewAudio = audio;
        resultsDiv.appendChild(audio);

        audio.play().catch(() => {
            console.log('Autoplay blocked; using native controls for preview playback');
        });

        if (data.preview.candidates && data.preview.candidates.length > 1) {
            const altWrap = document.createElement('div');
            altWrap.style.marginBottom = '14px';
            altWrap.innerHTML = '<strong>Alternate Previews:</strong>';
            data.preview.candidates.slice(1).forEach((candidate) => {
                const link = document.createElement('a');
                link.href = candidate.preview_url;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.style.display = 'block';
                link.style.color = '#1db954';
                link.innerText = `${candidate.track_name} - ${candidate.artist_name}`;
                altWrap.appendChild(link);
            });
            resultsDiv.appendChild(altWrap);
        }
    }

    data.results.forEach(result => {
        const div = document.createElement('div');
        div.className = 'result';
        div.innerHTML = `
            <h3>${result.player}</h3>
            <p><strong>Song Guess:</strong> ${result.title_guess || '[No Guess]'}</p>
            <p><strong>Artist Guess:</strong> ${result.artist_guess || '[No Guess]'}</p>
            <p><strong>Title Points:</strong> +${result.title_score}</p>
            <p><strong>Artist Points:</strong> +${result.artist_score} (${result.artist_match})</p>
            <p><strong>Both Correct Bonus:</strong> +${result.both_bonus}</p>
            <p><strong>Early Lock Bonus:</strong> +${result.early_lock_bonus}</p>
            <p><strong>Round Points:</strong> +${result.round_score}</p>
            <p><strong>Total Running Score:</strong> ${result.total_score}</p>
        `;
        resultsDiv.appendChild(div);
    });
});

window.addEventListener('beforeunload', () => {
    stopHostPreviewAudio();
});
