from flask import Flask, render_template, request, redirect
from flask_socketio import SocketIO, emit, join_room
import json
import os
import random
import threading
import time

from game_core import (
    DEFAULT_ROOM_CODE,
    rooms,
    host_room_by_sid,
    normalize_room_code,
    get_room_state,
    get_player_room,
    get_host_room,
    room_has_host_control,
    get_round_total_time,
    get_remaining_time,
    get_song_title,
    get_song_artist,
    get_song_year,
    format_genre_label,
    build_round_payload,
    calculate_title_score,
    calculate_artist_score,
    calculate_early_lock_bonus,
    fetch_itunes_preview,
    claim_room_owner,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = "secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

with open("data/songs.json", "r", encoding="utf-8") as f:
    songs = json.load(f)

DECADES = {"1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"}
GENRES = {"rock", "pop", "country", "hip-hop/r&b", "reggae"}
GENRE_ALIASES = {
    "hiphop": "hip-hop/r&b",
    "hip_hop": "hip-hop/r&b",
    "hip hop": "hip-hop/r&b",
    "r_and_b": "hip-hop/r&b",
    "r&b": "hip-hop/r&b",
    "r and b": "hip-hop/r&b",
    "hip_hop_r_and_b": "hip-hop/r&b",
    "hip-hop/r&b": "hip-hop/r&b",
    "hip hop/r&b": "hip-hop/r&b",
    "r_and_b_soul": "hip-hop/r&b",
    "motown": "hip-hop/r&b",
    "grunge": "rock",
}


def canonicalize_genre_key(raw_genre):
    base = str(raw_genre or "").strip().lower().replace("_", " ")
    if not base:
        return ""
    if base in GENRES:
        return base
    if base in GENRE_ALIASES:
        return GENRE_ALIASES[base]

    collapsed = " ".join(base.split())
    if collapsed in GENRES:
        return collapsed
    return GENRE_ALIASES.get(collapsed, collapsed)


def get_song_genre_key(song):
    return canonicalize_genre_key(song.get("genre", ""))


def get_song_decade(song):
    year = get_song_year(song)
    try:
        year_int = int(str(year))
    except Exception:
        return None

    decade = (year_int // 10) * 10
    return f"{decade}s"


def get_settings_payload(room_state):
    return {
        "rounds": int(room_state.get("rounds_per_game", 5)),
        "game_mode": room_state.get("game_mode", "quick"),
        "decades": sorted(list(room_state.get("decades_filter", DECADES))),
        "genres": sorted(list(room_state.get("genres_filter", GENRES))),
    }


def emit_settings_updated(room_code):
    payload = {"settings": get_settings_payload(get_room_state(room_code))}
    emit("settings_updated", payload, room=get_player_room(room_code))
    emit("settings_updated", payload, room=get_host_room(room_code))


def choose_song_for_room(room_state):
    allowed_decades = set(room_state.get("decades_filter", DECADES))
    allowed_genres = set(room_state.get("genres_filter", GENRES))

    filtered = []
    for song in songs:
        decade = get_song_decade(song)
        genre = get_song_genre_key(song)

        decade_ok = not allowed_decades or (decade in allowed_decades)
        genre_ok = not allowed_genres or (genre in allowed_genres)

        if decade_ok and genre_ok:
            filtered.append(song)

    if not filtered:
        return None

    return random.choice(filtered)


def reset_game_state_for_new_game(room_state):
    room_state["leaderboard"] = {player: 0 for player in room_state.get("players", [])}
    room_state["round_history"] = []
    room_state["guesses"] = {}
    room_state["current_song"] = None
    room_state["round_started_at"] = None
    room_state["last_results"] = None
    room_state["round_number"] = 0
    room_state["game_completed"] = False


def build_game_summary(room_state):
    leaderboard = room_state.get("leaderboard", {})
    history_rows = room_state.get("round_history", [])

    players = []
    for player, score in sorted(leaderboard.items(), key=lambda item: item[1], reverse=True):
        songs_for_player = [entry for entry in history_rows if entry.get("player") == player]
        players.append({
            "player": player,
            "score": score,
            "songs": songs_for_player,
        })

    return {
        "players": players
    }


def auto_close_round(room_code):
    room_code = normalize_room_code(room_code) or DEFAULT_ROOM_CODE
    with app.app_context():
        finalize_round(room_code)


def finalize_round(room_code):
    room_state = get_room_state(room_code)

    round_timer = room_state.get("round_timer")
    if round_timer:
        round_timer.cancel()
        room_state["round_timer"] = None

    current_song = room_state.get("current_song")
    if room_state.get("phase") != "guessing" or not current_song:
        return

    room_state["phase"] = "results"

    title_answer = get_song_title(current_song)
    artist_answer = get_song_artist(current_song)
    year_value = get_song_year(current_song)
    genre_key = get_song_genre_key(current_song)
    genre_label = format_genre_label(genre_key)
    guesses = room_state.get("guesses", {})

    results = []
    leaderboard = room_state.get("leaderboard", {})
    players = list(room_state.get("players", []))

    for player in players:
        guess = guesses.get(player)

        if not guess:
            result = {
                "player": player,
                "title_guess": "",
                "artist_guess": "",
                "title_score": 0,
                "artist_score": 0,
                "artist_match": "none",
                "both_bonus": 0,
                "early_lock_bonus": 0,
                "round_score": 0,
                "total_score": int(leaderboard.get(player, 0)),
                "no_guess": True,
            }
            results.append(result)
            room_state["round_history"].append({
                **result,
                "round_number": room_state.get("round_number", 0),
                "answer": title_answer,
                "artist": artist_answer,
            })
            continue

        title_guess = str(guess.get("title_guess", "")).strip()
        artist_guess = str(guess.get("artist_guess", "")).strip()

        title_score, title_correct = calculate_title_score(title_guess, title_answer)
        artist_score, artist_match = calculate_artist_score(artist_guess, artist_answer)
        both_bonus = 30 if title_correct and artist_match == "full" else 0

        early_bonus = 0
        if not guess.get("auto_lock"):
            early_bonus = calculate_early_lock_bonus(
                room_state.get("round_started_at"),
                guess.get("submitted_at"),
            )

        round_score = int(title_score + artist_score + both_bonus + early_bonus)
        leaderboard[player] = int(leaderboard.get(player, 0)) + round_score

        result = {
            "player": player,
            "title_guess": title_guess,
            "artist_guess": artist_guess,
            "title_score": int(title_score),
            "artist_score": int(artist_score),
            "artist_match": artist_match,
            "both_bonus": int(both_bonus),
            "early_lock_bonus": int(early_bonus),
            "round_score": round_score,
            "total_score": int(leaderboard[player]),
            "no_guess": False,
        }
        results.append(result)
        room_state["round_history"].append({
            **result,
            "round_number": room_state.get("round_number", 0),
            "answer": title_answer,
            "artist": artist_answer,
        })

    results.sort(key=lambda row: row["round_score"], reverse=True)

    rounds_per_game = int(room_state.get("rounds_per_game", 5))
    round_number = int(room_state.get("round_number", 0))
    game_over = round_number >= rounds_per_game
    room_state["game_completed"] = game_over

    payload = {
        "answer": title_answer,
        "artist": artist_answer,
        "year": year_value,
        "genre": genre_key,
        "genre_label": genre_label,
        "preview": room_state.get("round_preview"),
        "results": results,
        "round_number": round_number,
        "rounds_per_game": rounds_per_game,
        "game_mode": room_state.get("game_mode", "quick"),
        "game_over": game_over,
    }

    if game_over:
        payload["game_summary"] = build_game_summary(room_state)

    room_state["last_results"] = payload

    emit("game_state", {"state": "results"}, room=get_player_room(room_code))
    emit("game_state", {"state": "results"}, room=get_host_room(room_code))
    emit("round_results", payload, room=get_player_room(room_code))
    emit("round_results", payload, room=get_host_room(room_code))


@app.route("/")
def index():
    room_prefill = normalize_room_code(request.args.get("room", ""))
    return render_template("index.html", room_prefill=room_prefill)


@app.route("/host")
def host():
    join_url = request.host_url.rstrip("/")
    return render_template("host.html", join_url=join_url)


@app.route("/join", methods=["POST"])
def join():
    name = (request.form.get("name") or "").strip()
    room_code = normalize_room_code(request.form.get("room_code", "")) or DEFAULT_ROOM_CODE

    if not name:
        return redirect(f"/?room={room_code}")

    return redirect(f"/room/{room_code}?name={name}")


@app.route("/room/<room_code>")
def room(room_code):
    safe_room = normalize_room_code(room_code) or DEFAULT_ROOM_CODE
    name = (request.args.get("name") or "").strip()
    return render_template(
        "player.html",
        name=name,
        room_code=safe_room,
    )


@socketio.on("host_join")
def handle_host_join(data):
    room_code = normalize_room_code((data or {}).get("room_code")) or DEFAULT_ROOM_CODE
    room_state = get_room_state(room_code)

    host_room_by_sid[request.sid] = room_code
    join_room(get_host_room(room_code))

    emit("host_room_joined", {
        "room_code": room_code,
        "players": list(room_state.get("players", [])),
        "settings": get_settings_payload(room_state),
    }, to=request.sid)


@socketio.on("player_join")
def handle_player_join(data):
    room_code = normalize_room_code((data or {}).get("room_code")) or DEFAULT_ROOM_CODE
    player_name = str((data or {}).get("name", "")).strip()
    if not player_name:
        return

    room_state = get_room_state(room_code)
    join_room(get_player_room(room_code))

    if player_name not in room_state["players"]:
        room_state["players"].append(player_name)

    if player_name not in room_state["leaderboard"]:
        room_state["leaderboard"][player_name] = 0

    is_host = claim_room_owner(room_state, player_name, request.sid)
    emit("player_role", {
        "is_host": bool(is_host),
        "settings": get_settings_payload(room_state),
    }, to=request.sid)

    emit("update_players", {
        "players": list(room_state.get("players", [])),
        "leaderboard": room_state.get("leaderboard", {}),
    }, room=get_host_room(room_code))

    if room_state.get("phase") == "guessing" and room_state.get("current_song"):
        emit("game_state", {"state": "guessing"}, to=request.sid)
        payload = build_round_payload(
            room_state["current_song"],
            remaining_time=get_remaining_time(room_state),
            game_mode=room_state.get("game_mode", "quick"),
            quick_clue_key=room_state.get("quick_clue_key"),
            total_time=room_state.get("round_total_time"),
            round_audio=room_state.get("round_preview"),
        )
        payload["round_number"] = room_state.get("round_number", 0)
        payload["rounds_per_game"] = room_state.get("rounds_per_game", 5)
        emit("new_round", payload, to=request.sid)
    elif room_state.get("phase") == "results" and room_state.get("last_results"):
        emit("game_state", {"state": "results"}, to=request.sid)
        emit("round_results", room_state["last_results"], to=request.sid)
    else:
        emit("game_state", {"state": "lobby"}, to=request.sid)


@socketio.on("update_game_settings")
def handle_update_game_settings(data):
    room_code = normalize_room_code((data or {}).get("room_code")) or DEFAULT_ROOM_CODE
    if not room_has_host_control(room_code, request.sid):
        emit("host_error", {"message": "Only the host can update settings."}, to=request.sid)
        return

    room_state = get_room_state(room_code)

    try:
        rounds = int((data or {}).get("rounds", room_state.get("rounds_per_game", 5)))
    except Exception:
        rounds = int(room_state.get("rounds_per_game", 5))

    rounds = max(1, min(25, rounds))
    room_state["rounds_per_game"] = rounds

    mode = str((data or {}).get("game_mode", room_state.get("game_mode", "quick"))).strip().lower()
    if mode not in {"full", "quick", "music_only"}:
        mode = "quick"
    room_state["game_mode"] = mode

    incoming_decades = (data or {}).get("decades", [])
    if isinstance(incoming_decades, list):
        normalized_decades = {str(item).strip() for item in incoming_decades}
        room_state["decades_filter"] = normalized_decades.intersection(DECADES) or set(DECADES)

    incoming_genres = (data or {}).get("genres", [])
    if isinstance(incoming_genres, list):
        normalized_genres = {canonicalize_genre_key(item) for item in incoming_genres}
        room_state["genres_filter"] = normalized_genres.intersection(GENRES) or set(GENRES)

    emit_settings_updated(room_code)


@socketio.on("start_round")
def handle_start_round(data):
    room_code = normalize_room_code((data or {}).get("room_code")) or DEFAULT_ROOM_CODE
    if not room_has_host_control(room_code, request.sid):
        emit("host_error", {"message": "Only the host can start rounds."}, to=request.sid)
        return

    room_state = get_room_state(room_code)
    if room_state.get("game_completed"):
        reset_game_state_for_new_game(room_state)

    existing_timer = room_state.get("round_timer")
    if existing_timer:
        existing_timer.cancel()
        room_state["round_timer"] = None

    song = choose_song_for_room(room_state)
    if not song:
        emit("host_error", {"message": "No songs available for current filters."}, to=request.sid)
        return

    room_state["phase"] = "guessing"
    room_state["guesses"] = {}
    room_state["current_song"] = song
    room_state["round_number"] = int(room_state.get("round_number", 0)) + 1
    room_state["round_total_time"] = get_round_total_time(room_state)
    room_state["round_started_at"] = time.time()
    room_state["quick_clue_key"] = "lyric3"

    preview = fetch_itunes_preview(get_song_title(song), get_song_artist(song))
    room_state["round_preview"] = preview

    payload = build_round_payload(
        song,
        remaining_time=room_state["round_total_time"],
        game_mode=room_state.get("game_mode", "quick"),
        quick_clue_key=room_state.get("quick_clue_key"),
        total_time=room_state["round_total_time"],
        round_audio=preview,
    )
    payload["round_number"] = room_state["round_number"]
    payload["rounds_per_game"] = room_state.get("rounds_per_game", 5)
    payload["total_players"] = len(room_state.get("players", []))

    emit("game_state", {"state": "guessing"}, room=get_player_room(room_code))
    emit("host_round_started", payload, room=get_host_room(room_code))
    emit("new_round", payload, room=get_player_room(room_code))
    emit("guess_count", {"count": 0, "total": len(room_state.get("players", []))}, room=get_host_room(room_code))

    timer = threading.Timer(room_state["round_total_time"], auto_close_round, args=[room_code])
    timer.daemon = True
    room_state["round_timer"] = timer
    timer.start()


@socketio.on("submit_guess")
def handle_submit_guess(data):
    room_code = normalize_room_code((data or {}).get("room_code")) or DEFAULT_ROOM_CODE
    player_name = str((data or {}).get("name", "")).strip()
    if not player_name:
        return

    room_state = get_room_state(room_code)
    if room_state.get("phase") != "guessing":
        return

    if player_name in room_state["guesses"]:
        return

    title_guess = str((data or {}).get("title_guess", "")).strip()
    artist_guess = str((data or {}).get("artist_guess", "")).strip()
    auto_lock = bool((data or {}).get("auto_lock"))

    room_state["guesses"][player_name] = {
        "title_guess": title_guess,
        "artist_guess": artist_guess,
        "submitted_at": time.time(),
        "auto_lock": auto_lock,
    }

    emit("guess_locked", {"status": "success"}, to=request.sid)

    total_players = len(room_state.get("players", []))
    guess_count = len(room_state.get("guesses", {}))
    emit("guess_count", {"count": guess_count, "total": total_players}, room=get_host_room(room_code))

    if total_players > 0 and guess_count >= total_players:
        finalize_round(room_code)


@socketio.on("show_results")
def handle_show_results(data):
    room_code = normalize_room_code((data or {}).get("room_code")) or DEFAULT_ROOM_CODE
    if not room_has_host_control(room_code, request.sid):
        emit("host_error", {"message": "Only the host can show results."}, to=request.sid)
        return

    finalize_round(room_code)


@socketio.on("disconnect")
def handle_disconnect():
    room_code = host_room_by_sid.pop(request.sid, None)
    if not room_code:
        return

    room_state = rooms.get(room_code)
    if not room_state:
        return

    if room_state.get("owner_sid") == request.sid:
        room_state["owner_sid"] = None
        room_state["owner_name"] = None


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
