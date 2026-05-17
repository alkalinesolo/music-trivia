from flask import Flask, render_template, request, redirect
from flask_socketio import SocketIO, emit, join_room
import random
import json
import threading
import os
import eventlet

eventlet.monkey_patch()


app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# =========================
# CONFIG
# =========================

ROOM_CODE = "ABCD"
PLAYER_ROOM = ROOM_CODE
HOST_ROOM = f"{ROOM_CODE}_host"
ROUND_TIME = 20  # Time in seconds for players to guess

# =========================
# GAME STATE
# =========================

players = []
leaderboard = {}
guesses = {}
current_song = None
round_timer = None  # Holds our threading.Timer object

game_state = {
    "phase": "lobby"  # lobby, guessing, results
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
    return render_template('index.html')


@app.route('/host')
def host():
    join_url = request.host_url
    return render_template(
        'host.html',
        room_code=ROOM_CODE,
        join_url=join_url
    )


@app.route('/join', methods=['POST'])
def join():
    name = request.form.get('name')
    return redirect(f'/room/{ROOM_CODE}?name={name}')


@app.route('/room/<room_code>')
def room(room_code):
    name = request.args.get('name')
    return render_template(
        'player.html',
        name=name,
        room_code=room_code
    )

# =========================
# SOCKET EVENTS
# =========================

@socketio.on('host_join')
def handle_host_join():
    join_room(HOST_ROOM)
    print("Host connected")


@socketio.on('player_join')
def handle_player_join(data):
    global players, leaderboard

    player_name = data['name'].strip()
    if not player_name:
        return

    join_room(PLAYER_ROOM)

    if player_name not in players:
        players.append(player_name)

    if player_name not in leaderboard:
        leaderboard[player_name] = 0

    print(f"{player_name} joined")

    # Send current roster to host
    emit('update_players', {'players': players, 'leaderboard': leaderboard}, room=HOST_ROOM)

    # Recovery: If player disconnects and drops back in mid-round, catch them up
    if game_state["phase"] == "guessing" and current_song:
        emit('game_state', {'state': 'guessing'}, to=request.sid)
        emit('new_round', {'lyric': current_song['lyric']}, to=request.sid)
    elif game_state["phase"] == "results":
        emit('game_state', {'state': 'results'}, to=request.sid)


@socketio.on('start_round')
def handle_start_round():
    global current_song, guesses, round_timer
    
    # Cancel any active background timer before starting a new one
    if round_timer:
        round_timer.cancel()

    guesses = {}
    current_song = random.choice(songs)
    game_state["phase"] = "guessing"

    print(f"ROUND STARTED: {current_song['answer']}")

    # UX Broadcast: Tell players to unlock their input bars
    emit('game_state', {'state': 'guessing'}, room=PLAYER_ROOM)
    
    # Send lyric to players
    emit('new_round', {'lyric': current_song['lyric']}, room=PLAYER_ROOM)

    # Notify host screen with full data and start clock countdown
    emit('host_round_started', {'song': current_song, 'time': ROUND_TIME}, room=HOST_ROOM)

    # Start the automated round closing countdown thread
    round_timer = threading.Timer(ROUND_TIME, auto_close_round)
    round_timer.start()


def auto_close_round():
    """Triggered automatically when the thread timer expires."""
    print("Timer expired. Forcing results evaluation.")
    # Use socketio.on context fallback execution to call our processing block
    handle_show_results()


@socketio.on('submit_guess')
def handle_submit_guess(data):
    global guesses

    player_name = data['name'].strip()
    guess = data['guess'].strip()

    if not player_name or game_state["phase"] != "guessing":
        return

    # Bug Fix: Enforce single-submission server-side to block client injection/spam
    if player_name in guesses:
        print(f"Blocked duplicate guess attempt from: {player_name}")
        return

    guesses[player_name] = guess
    print(f"{player_name}: {guess}")

    # Acknowledge the player's app that their guess is safely locked down
    emit('guess_locked', {'status': 'success'}, to=request.sid)

    # Update host guess tracking mechanics
    emit('guess_count', {'count': len(guesses), 'total': len(players)}, room=HOST_ROOM)
    
    # Optional Optimization: If 100% of players have submitted, force cut the timer short
    if len(guesses) == len(players):
        handle_show_results()


@socketio.on('show_results')
def handle_show_results():
    global leaderboard, round_timer

    # Safety clean: Kill the background thread clock if host forced results early
    if round_timer:
        round_timer.cancel()
        round_timer = None

    if not current_song or game_state["phase"] == "results":
        return

    game_state["phase"] = "results"
    results = []
    correct_answer = current_song['answer']

    for player in players:
        guess = guesses.get(player, "").strip()
        if guess:
            score = calculate_score(guess, correct_answer)
        else:
            guess = "[No Guess]"
            score = 0

        leaderboard[player] += score
        results.append({
            'player': player,
            'guess': guess,
            'score': score,
            'total_score': leaderboard[player]
        })

    results.sort(key=lambda x: x['score'], reverse=True)

    payload = {
        'answer': current_song['answer'],
        'artist': current_song['artist'],
        'results': results,
        'leaderboard': leaderboard
    }

    # Shift UX states globally across all phone devices
    emit('game_state', {'state': 'results'}, room=PLAYER_ROOM)
    
    # Broadcast scoreboard lists
    emit('round_results', payload, room=PLAYER_ROOM)
    emit('round_results', payload, room=HOST_ROOM)

# =========================
# SCORING ENGINE
# =========================

def sanitize_text(text):
    text = text.lower()
    for char in [".", ",", "!", "?", "'", '"', "(", ")", "-", "_"]:
        text = text.replace(char, "")
    return text.strip()


def calculate_score(guess, answer):
    guess = sanitize_text(guess)
    answer = sanitize_text(answer)

    if guess == answer:
        return 100

    guess_words = set(guess.split())
    answer_words = set(answer.split())

    if not answer_words:
        return 0

    matches = len(guess_words.intersection(answer_words))
    total = len(answer_words)

    return int((matches / total) * 100)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)