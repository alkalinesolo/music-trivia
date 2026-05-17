from flask import Flask, render_template, request, redirect
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import json
import threading
import os
import time
from urllib.parse import quote_plus
from urllib.request import urlopen

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# =========================
# CONFIG
# =========================

TOTAL_ROUND_TIME = 60
LYRIC2_REVEAL_AT = 15
LYRIC1_REVEAL_AT = 45
DEFAULT_ROOM_CODE = "MAIN"
TITLE_POINTS = 60
ARTIST_POINTS = 40
BOTH_BONUS_POINTS = 30
EARLY_LOCK_BEFORE_LYRIC2_POINTS = 20
EARLY_LOCK_BEFORE_LYRIC1_POINTS = 10

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
        return TOTAL_ROUND_TIME
    elapsed = int(time.time() - room_state["round_started_at"])
    return max(0, TOTAL_ROUND_TIME - elapsed)


def get_song_title(song):
    title = song.get('answer') or song.get('title') or 'Unknown Title'
    if isinstance(title, list):
        return str(title[0]) if title else 'Unknown Title'
    return str(title)


def get_song_artist(song):
    artist = song.get('artist', 'Unknown Artist')
    if isinstance(artist, list):
        return str(artist[0]) if artist else 'Unknown Artist'
    return str(artist)


def get_song_year(song):
    year = song.get('year', 'unknown')
    if isinstance(year, list):
        return year[0] if year else 'unknown'
    return year


def build_clues(song):
    return {
        'genre': song.get('genre', 'unknown'),
        'year': get_song_year(song),
        'lyric3': song.get('lyric3', ''),
        'lyric2': song.get('lyric2', ''),
        'lyric1': song.get('lyric1', '')
    }


def build_round_payload(song, remaining_time=None):
    if remaining_time is None:
        remaining_time = TOTAL_ROUND_TIME

    return {
        'clues': build_clues(song),
        'timing': {
            'total_time': TOTAL_ROUND_TIME,
            'lyric2_reveal_at': LYRIC2_REVEAL_AT,
            'lyric1_reveal_at': LYRIC1_REVEAL_AT,
            'remaining_time': remaining_time
        }
    }

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
    room_state['current_song'] = random.choice(songs)
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


def split_words(text):
    clean = sanitize_text(text)
    return [word for word in clean.split() if word]


def calculate_title_score(guess, answer):
    if not guess:
        return 0, False

    guess_clean = sanitize_text(guess)
    answer_clean = sanitize_text(answer)
    if guess_clean == answer_clean:
        return TITLE_POINTS, True

    answer_words = set(split_words(answer))
    if not answer_words:
        return 0, False

    guess_words = set(split_words(guess))
    matches = len(answer_words.intersection(guess_words))
    score = int((matches / len(answer_words)) * TITLE_POINTS)
    return score, False


def calculate_artist_score(guess, artist):
    if not guess:
        return 0, 'none'

    guess_clean = sanitize_text(guess)
    artist_clean = sanitize_text(artist)
    if guess_clean == artist_clean:
        return ARTIST_POINTS, 'full'

    artist_words = split_words(artist)
    guess_words = set(split_words(guess))
    if not artist_words:
        return 0, 'none'

    first_name = artist_words[0]
    last_name = artist_words[-1]

    if first_name in guess_words and last_name in guess_words:
        return ARTIST_POINTS, 'full'
    if last_name in guess_words:
        return int(ARTIST_POINTS * 0.75), 'last_name'
    if first_name in guess_words:
        return int(ARTIST_POINTS * 0.5), 'first_name'

    return 0, 'none'


def calculate_early_lock_bonus(round_started_at, submitted_at):
    if not round_started_at or not submitted_at:
        return 0

    elapsed = submitted_at - round_started_at
    if elapsed < LYRIC2_REVEAL_AT:
        return EARLY_LOCK_BEFORE_LYRIC2_POINTS
    if elapsed < LYRIC1_REVEAL_AT:
        return EARLY_LOCK_BEFORE_LYRIC1_POINTS
    return 0


def fetch_itunes_preview(title, artist):
    term = quote_plus(f"{title} {artist}")
    url = f"https://itunes.apple.com/search?term={term}&entity=song&limit=10"

    try:
        with urlopen(url, timeout=4) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception:
        return None

    results = data.get('results', [])
    title_clean = sanitize_text(title)
    artist_clean = sanitize_text(artist)

    for item in results:
        preview_url = item.get('previewUrl')
        if not preview_url:
            continue

        track_name = sanitize_text(item.get('trackName', ''))
        artist_name = sanitize_text(item.get('artistName', ''))

        if title_clean in track_name and (artist_clean in artist_name or artist_name in artist_clean):
            return {
                'preview_url': preview_url,
                'track_name': item.get('trackName'),
                'artist_name': item.get('artistName')
            }

    for item in results:
        preview_url = item.get('previewUrl')
        if preview_url:
            return {
                'preview_url': preview_url,
                'track_name': item.get('trackName'),
                'artist_name': item.get('artistName')
            }

    return None


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)