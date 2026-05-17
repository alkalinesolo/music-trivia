from flask import Flask, render_template, request, redirect
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import json
import threading
import os
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# =========================
# CONFIG
# =========================

ROUND_TIME = 20  # Time in seconds for players to guess
DEFAULT_ROOM_CODE = "MAIN"
TITLE_POINTS = 60
ARTIST_POINTS = 40
BOTH_BONUS_POINTS = 30

# =========================
# GAME STATE
# =========================

rooms = {}
host_room_by_sid = {}


def normalize_room_code(raw_code):
    code = (raw_code or "").strip().upper()
    filtered = "".join(ch for ch in code if ch.isalnum() or ch in ['_', '-'])
    return filtered[:20]


def get_player_room(room_code):
    return f"{room_code}_players"


def get_host_room(room_code):
    return f"{room_code}_host"


def get_room_state(room_code):
    if room_code not in rooms:
        rooms[room_code] = {
            "phase": "lobby",  # lobby, guessing, results
            "players": [],
            "leaderboard": {},
            "guesses": {},
            "current_song": None,
            "round_timer": None,
            "round_started_at": None,
            "last_results": None
        }
    return rooms[room_code]


def get_remaining_time(room_state):
    if not room_state["round_started_at"]:
        return ROUND_TIME
    elapsed = int(time.time() - room_state["round_started_at"])
    return max(0, ROUND_TIME - elapsed)

# =========================
# LOAD SONGS
# =========================

with open('data/songs.json', 'r') as f:
    songs = json.load(f)

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
            'song': room_state['current_song'],
            'time': remaining_time,
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

    print(f"{player_name} joined room {room_code}")

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
            {'lyric': room_state['current_song']['lyric'], 'time': remaining_time},
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
    room_state['current_song'] = random.choice(songs)
    room_state['phase'] = 'guessing'
    room_state['round_started_at'] = time.time()
    room_state['last_results'] = None

    print(f"ROUND STARTED [{room_code}]: {room_state['current_song']['answer']}")

    # UX Broadcast: Tell players to unlock their input bars
    emit('game_state', {'state': 'guessing'}, room=get_player_room(room_code))
    
    # Send lyric to players
    emit(
        'new_round',
        {'lyric': room_state['current_song']['lyric'], 'time': ROUND_TIME},
        room=get_player_room(room_code)
    )

    # Notify host screen with full data and start clock countdown
    emit(
        'host_round_started',
        {
            'song': room_state['current_song'],
            'time': ROUND_TIME,
            'total_players': len(room_state['players'])
        },
        room=get_host_room(room_code)
    )

    # Start the automated round closing countdown thread
    room_state['round_timer'] = threading.Timer(ROUND_TIME, auto_close_round, args=[room_code])
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
        'artist_guess': artist_guess
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
    
    # Optional Optimization: If 100% of players have submitted, force cut the timer short
    if len(room_state['guesses']) == len(room_state['players']):
        finalize_results_for_room(room_code)


@socketio.on('show_results')
def handle_show_results(data):
    room_code = normalize_room_code((data or {}).get('room_code'))
    if not room_code:
        return
    finalize_results_for_room(room_code)


@socketio.on('start_round')
def handle_start_round(data):
    room_code = normalize_room_code((data or {}).get('room_code'))
    if not room_code:
        emit('host_error', {'message': 'Please choose a valid room code first.'}, to=request.sid)
        return
    start_round_for_room(room_code)


@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in host_room_by_sid:
        del host_room_by_sid[request.sid]


def finalize_results_for_room(room_code):
    room_state = rooms.get(room_code)
    if not room_state:
        return

    # Safety clean: Kill the background thread clock if host forced results early
    if room_state['round_timer']:
        room_state['round_timer'].cancel()
        room_state['round_timer'] = None

    if not room_state['current_song'] or room_state['phase'] == 'results':
        return

    room_state['phase'] = 'results'
    room_state['round_started_at'] = None

    results = []
    correct_title = room_state['current_song']['answer']
    correct_artist = room_state['current_song']['artist']

    for player in room_state['players']:
        raw_guess = room_state['guesses'].get(player, {})
        title_guess = (raw_guess.get('title_guess') or '').strip()
        artist_guess = (raw_guess.get('artist_guess') or '').strip()
        no_guess = not title_guess and not artist_guess

        title_correct = sanitize_text(title_guess) == sanitize_text(correct_title) if title_guess else False
        artist_correct = sanitize_text(artist_guess) == sanitize_text(correct_artist) if artist_guess else False

        score = 0
        if title_correct:
            score += TITLE_POINTS
        if artist_correct:
            score += ARTIST_POINTS
        if title_correct and artist_correct:
            score += BOTH_BONUS_POINTS

        room_state['leaderboard'][player] += score
        results.append({
            'player': player,
            'title_guess': title_guess,
            'artist_guess': artist_guess,
            'title_correct': title_correct,
            'artist_correct': artist_correct,
            'score': score,
            'round_score': score,
            'no_guess': no_guess,
            'total_score': room_state['leaderboard'][player]
        })

    results.sort(key=lambda x: x['score'], reverse=True)

    payload = {
        'answer': room_state['current_song']['answer'],
        'artist': room_state['current_song']['artist'],
        'scoring': {
            'title_points': TITLE_POINTS,
            'artist_points': ARTIST_POINTS,
            'both_bonus_points': BOTH_BONUS_POINTS
        },
        'results': results,
        'leaderboard': room_state['leaderboard']
    }
    room_state['last_results'] = payload

    # Shift UX states globally across all phone devices
    emit('game_state', {'state': 'results'}, room=get_player_room(room_code))
    
    # Broadcast scoreboard lists
    emit('round_results', payload, room=get_player_room(room_code))
    emit('round_results', payload, room=get_host_room(room_code))


# =========================
# SCORING ENGINE
# =========================

def sanitize_text(text):
    text = text.lower()
    for char in [".", ",", "!", "?", "'", '"', "(", ")", "-", "_"]:
        text = text.replace(char, "")
    return text.strip()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)