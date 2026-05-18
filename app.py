from flask import Flask, render_template, request, redirect
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import json
import threading
import os
import time

from game_core import (
    BOTH_BONUS_POINTS,
    DEFAULT_ROOM_CODE,
    EARLY_LOCK_BEFORE_LYRIC1_POINTS,
    EARLY_LOCK_BEFORE_LYRIC2_POINTS,
    ARTIST_POINTS,
    TITLE_POINTS,
    TOTAL_ROUND_TIME,
    build_round_payload,
    calculate_artist_score,
    calculate_early_lock_bonus,
    calculate_title_score,
    claim_room_owner,
    fetch_itunes_preview,
    format_genre_label,
    get_host_room,
    get_player_room,
    get_remaining_time,
    get_room_state,
    get_song_artist,
    get_song_title,
    get_song_year,
    host_room_by_sid,
    normalize_room_code,
    room_has_host_control,
    rooms,
)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret'
socketio = SocketIO(app, cors_allowed_origins="*")

with open('data/songs.json', 'r', encoding='utf-8') as f:
    songs = json.load(f)

# Helper function to map year to decade
def get_decade(year):
    try:
        year_value = int(year)
    except (TypeError, ValueError):
        return None

    decade = (year_value // 10) * 10
    return f"{decade}s"

# Helper function to filter songs by decades and genres
def filter_songs_by_settings(song_list, decades_filter, genres_filter):
    decades_filter = {str(decade) for decade in (decades_filter or []) if str(decade).strip()}
    genres_filter = {str(genre).strip().lower() for genre in (genres_filter or []) if str(genre).strip()}

    if not decades_filter and not genres_filter:
        return song_list
    
    filtered = []
    for song in song_list:
        song_decade = get_decade(song.get('year'))
        song_genre = song.get('genre', '').lower()

        decade_matches = not decades_filter or song_decade in decades_filter
        genre_matches = not genres_filter or song_genre in genres_filter

        if decade_matches and genre_matches:
            filtered.append(song)
    
    return filtered

# =========================
# ROUTES
# =========================

@app.route('/')
def index():
    prefill_room = normalize_room_code(request.args.get('room'))
    return render_template('index.html', prefill_room=prefill_room)


@app.route('/host')
def host():
    join_url = request.host_url.rstrip('/')
    return render_template(
        'host.html',
        join_url=join_url
    )


@app.route('/join', methods=['POST'])
def join():
    name = (request.form.get('name') or '').strip()
    room_code = normalize_room_code(request.form.get('room_code')) or DEFAULT_ROOM_CODE

    if not name:
        return redirect('/')

    return redirect(f'/room/{room_code}?name={name}')


@app.route('/room/<room_code>')
def room(room_code):
    name = request.args.get('name')
    normalized_room_code = normalize_room_code(room_code) or DEFAULT_ROOM_CODE
    return render_template(
        'player.html',
        name=name,
        room_code=normalized_room_code
    )

# =========================
# SOCKET EVENTS
# =========================

@socketio.on('host_join')
def handle_host_join(data):
    room_code = normalize_room_code((data or {}).get('room_code'))
    if not room_code:
        emit('host_error', {'message': 'Please enter a valid room code.'}, to=request.sid)
        return

    previous_room = host_room_by_sid.get(request.sid)
    if previous_room and previous_room != room_code:
        leave_room(get_host_room(previous_room))

    join_room(get_host_room(room_code))
    host_room_by_sid[request.sid] = room_code
    room_state = get_room_state(room_code)

    emit('host_room_joined', {
        'room_code': room_code,
        'players': room_state['players'],
        'leaderboard': room_state['leaderboard']
    }, to=request.sid)

    print(f"Host connected to room {room_code}")

    if room_state["phase"] == "guessing" and room_state["current_song"]:
        remaining_time = get_remaining_time(room_state)
        emit('host_round_started', {
            **build_round_payload(room_state['current_song'], remaining_time),
            'total_players': len(room_state['players'])
        }, to=request.sid)
        emit('guess_count', {
            'count': len(room_state['guesses']),
            'total': len(room_state['players'])
        }, to=request.sid)
    elif room_state["phase"] == "results" and room_state["last_results"]:
        emit('round_results', room_state['last_results'], to=request.sid)


@socketio.on('player_join')
def handle_player_join(data):
    player_name = (data.get('name') or '').strip()
    room_code = normalize_room_code(data.get('room_code'))

    if not player_name or not room_code:
        return

    room_state = get_room_state(room_code)
    join_room(get_player_room(room_code))

    if player_name not in room_state['players']:
        room_state['players'].append(player_name)

    if player_name not in room_state['leaderboard']:
        room_state['leaderboard'][player_name] = 0

    claim_room_owner(room_state, player_name, request.sid)

    print(f"{player_name} joined room {room_code}")

    emit(
        'player_role',
        {
            'is_host': room_has_host_control(room_code, request.sid),
            'is_owner': room_state.get('owner_sid') == request.sid,
            'owner_name': room_state.get('owner_name')
        },
        to=request.sid
    )

    # Send current roster to host
    emit(
        'update_players',
        {'players': room_state['players'], 'leaderboard': room_state['leaderboard']},
        room=get_host_room(room_code)
    )

    # Recovery: If player disconnects and drops back in mid-round, catch them up
    if room_state["phase"] == "guessing" and room_state["current_song"]:
        remaining_time = get_remaining_time(room_state)
        emit('game_state', {'state': 'guessing'}, to=request.sid)
        emit(
            'new_round',
            build_round_payload(room_state['current_song'], remaining_time),
            to=request.sid
        )
    elif room_state["phase"] == "results":
        emit('game_state', {'state': 'results'}, to=request.sid)
        if room_state["last_results"]:
            emit('round_results', room_state["last_results"], to=request.sid)


def start_round_for_room(room_code):
    room_state = get_room_state(room_code)

    # Cancel any active background timer before starting a new one
    if room_state['round_timer']:
        room_state['round_timer'].cancel()

    if len(room_state['players']) == 0:
        emit('host_error', {'message': 'At least one player must join before starting.'}, to=request.sid)
        return

    room_state['guesses'] = {}
    
    # Filter songs based on room settings
    filtered_songs = filter_songs_by_settings(
        songs, 
        room_state.get('decades_filter', set()),
        room_state.get('genres_filter', set())
    )

    if not filtered_songs:
        emit(
            'host_error',
            {'message': 'No songs match the selected decade and genre filters.'},
            to=request.sid
        )
        return
    
    room_state['current_song'] = random.choice(filtered_songs)
    room_state['phase'] = 'guessing'
    room_state['round_started_at'] = time.time()
    room_state['last_results'] = None

    print(f"ROUND STARTED [{room_code}]: {get_song_title(room_state['current_song'])}")

    # UX Broadcast: Tell players to unlock their input bars
    emit('game_state', {'state': 'guessing'}, room=get_player_room(room_code))
    
    # Send lyric to players
    emit(
        'new_round',
        build_round_payload(room_state['current_song']),
        room=get_player_room(room_code)
    )

    # Notify host screen with full data and start clock countdown
    emit(
        'host_round_started',
        {
            **build_round_payload(room_state['current_song']),
            'total_players': len(room_state['players'])
        },
        room=get_host_room(room_code)
    )

    # Start the automated round closing countdown thread
    room_state['round_timer'] = threading.Timer(TOTAL_ROUND_TIME, auto_close_round, args=[room_code])
    room_state['round_timer'].start()


def auto_close_round(room_code):
    """Triggered automatically when the thread timer expires."""
    print(f"Timer expired for {room_code}. Forcing results evaluation.")
    finalize_results_for_room(room_code)


@socketio.on('submit_guess')
def handle_submit_guess(data):
    player_name = (data.get('name') or '').strip()
    room_code = normalize_room_code(data.get('room_code'))
    title_guess = (data.get('title_guess') or '').strip()
    artist_guess = (data.get('artist_guess') or '').strip()

    if not player_name or not room_code:
        return

    room_state = rooms.get(room_code)
    if not room_state or room_state['phase'] != 'guessing':
        return

    if not title_guess and not artist_guess:
        return

    # Bug Fix: Enforce single-submission server-side to block client injection/spam
    if player_name in room_state['guesses']:
        print(f"Blocked duplicate guess attempt from: {player_name}")
        return

    room_state['guesses'][player_name] = {
        'title_guess': title_guess,
        'artist_guess': artist_guess,
        'submitted_at': time.time()
    }
    print(f"{player_name} [{room_code}] title='{title_guess}' artist='{artist_guess}'")

    # Acknowledge the player's app that their guess is safely locked down
    emit('guess_locked', {'status': 'success'}, to=request.sid)

    # Update host guess tracking mechanics
    emit(
        'guess_count',
        {'count': len(room_state['guesses']), 'total': len(room_state['players'])},
        room=get_host_room(room_code)
    )

    if len(room_state['guesses']) == len(room_state['players']):
        finalize_results_for_room(room_code)
    
@socketio.on('show_results')
def handle_show_results(data):
    room_code = normalize_room_code((data or {}).get('room_code'))
    if not room_code:
        return
    if not room_has_host_control(room_code, request.sid):
        emit('host_error', {'message': 'Only the room host can show results.'}, to=request.sid)
        return
    finalize_results_for_room(room_code)


@socketio.on('start_round')
def handle_start_round(data):
    room_code = normalize_room_code((data or {}).get('room_code'))
    if not room_code:
        emit('host_error', {'message': 'Please choose a valid room code first.'}, to=request.sid)
        return
    if not room_has_host_control(room_code, request.sid):
        emit('host_error', {'message': 'Only the room host can start the round.'}, to=request.sid)
        return
    start_round_for_room(room_code)


@socketio.on('update_game_settings')
def handle_update_game_settings(data):
    room_code = normalize_room_code((data or {}).get('room_code'))
    if not room_code:
        emit('host_error', {'message': 'Please choose a valid room code first.'}, to=request.sid)
        return
    if not room_has_host_control(room_code, request.sid):
        emit('host_error', {'message': 'Only the room host can change settings.'}, to=request.sid)
        return
    
    room_state = get_room_state(room_code)
    
    # Update settings
    if 'rounds' in data:
        room_state['rounds_per_game'] = max(1, min(20, int(data.get('rounds', 5))))
    if 'decades' in data:
        room_state['decades_filter'] = set(data.get('decades', []))
    if 'genres' in data:
        room_state['genres_filter'] = set(g.lower() for g in data.get('genres', []))
    
    emit('settings_updated', {'message': 'Settings updated successfully'}, to=request.sid)


@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in host_room_by_sid:
        del host_room_by_sid[request.sid]


def finalize_results_for_room(room_code):
    room_state = rooms.get(room_code)
    if not room_state:
        return

    if room_state['round_timer']:
        room_state['round_timer'].cancel()
        room_state['round_timer'] = None

    if not room_state['current_song'] or room_state['phase'] == 'results':
        return

    room_state['phase'] = 'results'
    round_started_at = room_state['round_started_at']
    room_state['round_started_at'] = None

    results = []
    correct_title = get_song_title(room_state['current_song'])
    correct_artist = get_song_artist(room_state['current_song'])

    for player in room_state['players']:
        raw_guess = room_state['guesses'].get(player, {})
        title_guess = (raw_guess.get('title_guess') or '').strip()
        artist_guess = (raw_guess.get('artist_guess') or '').strip()
        submitted_at = raw_guess.get('submitted_at')
        no_guess = not title_guess and not artist_guess

        title_score, title_correct = calculate_title_score(title_guess, correct_title)
        artist_score, artist_match = calculate_artist_score(artist_guess, correct_artist)
        artist_correct = artist_match == 'full'
        early_lock_bonus = 0
        if title_correct:
            early_lock_bonus = calculate_early_lock_bonus(round_started_at, submitted_at)

        score = title_score + artist_score + early_lock_bonus
        both_bonus = 0
        if title_correct and artist_correct:
            both_bonus = BOTH_BONUS_POINTS
            score += BOTH_BONUS_POINTS

        room_state['leaderboard'][player] += score
        results.append({
            'player': player,
            'title_guess': title_guess,
            'artist_guess': artist_guess,
            'title_correct': title_correct,
            'artist_correct': artist_correct,
            'artist_match': artist_match,
            'title_score': title_score,
            'artist_score': artist_score,
            'both_bonus': both_bonus,
            'early_lock_bonus': early_lock_bonus,
            'score': score,
            'round_score': score,
            'no_guess': no_guess,
            'total_score': room_state['leaderboard'][player]
        })

    results.sort(key=lambda x: x['score'], reverse=True)

    payload = {
        'answer': correct_title,
        'artist': correct_artist,
        'genre': room_state['current_song'].get('genre', 'unknown'),
        'genre_label': format_genre_label(room_state['current_song'].get('genre', 'unknown')),
        'year': get_song_year(room_state['current_song']),
        'preview': fetch_itunes_preview(correct_title, correct_artist),
        'scoring': {
            'title_points': TITLE_POINTS,
            'artist_points': ARTIST_POINTS,
            'both_bonus_points': BOTH_BONUS_POINTS,
            'early_before_lyric2_points': EARLY_LOCK_BEFORE_LYRIC2_POINTS,
            'early_before_lyric1_points': EARLY_LOCK_BEFORE_LYRIC1_POINTS
        },
        'results': results,
        'leaderboard': room_state['leaderboard']
    }
    room_state['last_results'] = payload

    socketio.emit('game_state', {'state': 'results'}, room=get_player_room(room_code))
    socketio.emit('game_state', {'state': 'results'}, room=get_host_room(room_code))

    socketio.emit('round_results', payload, room=get_player_room(room_code))
    socketio.emit('round_results', payload, room=get_host_room(room_code))
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)