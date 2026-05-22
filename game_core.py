from urllib.parse import quote_plus
from urllib.request import urlopen
import json
import time

TOTAL_ROUND_TIME = 60
QUICK_ROUND_TIME = 30
MUSIC_ONLY_ROUND_TIME = 30
LYRIC2_REVEAL_AT = 15
LYRIC1_REVEAL_AT = 45
DEFAULT_ROOM_CODE = "MAIN"
TITLE_POINTS = 60
ARTIST_POINTS = 40
BOTH_BONUS_POINTS = 30
EARLY_LOCK_BEFORE_LYRIC2_POINTS = 20
EARLY_LOCK_BEFORE_LYRIC1_POINTS = 10

rooms = {}
host_room_by_sid = {}


def normalize_room_code(raw_code):
    code = (raw_code or "").strip().upper()
    filtered = "".join(ch for ch in code if ch.isalnum() or ch in ["_", "-"])
    return filtered[:20]


def get_player_room(room_code):
    return f"{room_code}_players"


def get_host_room(room_code):
    return f"{room_code}_host"


def get_room_state(room_code):
    if room_code not in rooms:
        rooms[room_code] = {
            "phase": "lobby",
            "players": [],
            "leaderboard": {},
            "round_history": [],
            "guesses": {},
            "current_song": None,
            "round_timer": None,
            "round_started_at": None,
            "last_results": None,
            "round_number": 0,
            "game_completed": False,
            "owner_sid": None,
            "owner_name": None,
            "rounds_per_game": 5,
            "game_mode": "full",
            "round_total_time": TOTAL_ROUND_TIME,
            "quick_clue_key": None,
            "round_preview": None,
            "decades_filter": {"1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"},
            "genres_filter": {"rock", "pop", "country", "hip-hop/r&b", "reggae"},
        }
    return rooms[room_code]


def get_round_total_time(room_state):
    mode = (room_state or {}).get("game_mode")
    if mode == "quick":
        return QUICK_ROUND_TIME
    if mode == "music_only":
        return MUSIC_ONLY_ROUND_TIME
    return TOTAL_ROUND_TIME


def claim_room_owner(room_state, player_name, sid):
    if room_state.get("owner_sid"):
        return False

    room_state["owner_sid"] = sid
    room_state["owner_name"] = player_name
    return True


def room_has_host_control(room_code, sid):
    room_state = rooms.get(room_code)
    if not room_state:
        return False

    return room_state.get("owner_sid") == sid or host_room_by_sid.get(sid) == room_code


def get_remaining_time(room_state):
    total_time = int((room_state or {}).get("round_total_time") or get_round_total_time(room_state))
    if not room_state["round_started_at"]:
        return total_time

    elapsed = int(time.time() - room_state["round_started_at"])
    return max(0, total_time - elapsed)


def get_song_title(song):
    title = song.get("answer") or song.get("title") or "Unknown Title"
    if isinstance(title, list):
        return str(title[0]) if title else "Unknown Title"
    return str(title)


def get_song_artist(song):
    artist = song.get("artist", "Unknown Artist")
    if isinstance(artist, list):
        return str(artist[0]) if artist else "Unknown Artist"
    return str(artist)


def get_song_year(song):
    year = song.get("year", "unknown")
    if isinstance(year, list):
        return year[0] if year else "unknown"
    return year


def format_genre_label(genre):
    if not genre:
        return "Unknown"

    normalized = str(genre).strip().lower()
    if normalized in {"r_and_b_soul", "hip_hop_r_and_b", "hip-hop/r&b", "hip hop/r&b"}:
        return "Hip-Hop/R&B"

    return str(genre).replace("_", " ").title()


def build_clues(song, game_mode="full", quick_clue_key=None):
    if game_mode == "music_only":
        return {
            "genre": song.get("genre", "unknown"),
            "genre_label": format_genre_label(song.get("genre", "unknown")),
            "year": get_song_year(song),
            "lyric3": "",
            "lyric2": "",
            "lyric1": "",
            "quick_label": None,
        }

    if game_mode == "quick":
        preferred_key = quick_clue_key if quick_clue_key in {"lyric1", "lyric2"} else "lyric2"
        fallback_key = "lyric1" if preferred_key == "lyric2" else "lyric2"
        quick_clue = song.get(preferred_key) or song.get(fallback_key) or ""

        return {
            "genre": song.get("genre", "unknown"),
            "genre_label": format_genre_label(song.get("genre", "unknown")),
            "year": get_song_year(song),
            "lyric3": quick_clue,
            "lyric2": "",
            "lyric1": "",
            "quick_label": "Lyric",
        }

    return {
        "genre": song.get("genre", "unknown"),
        "genre_label": format_genre_label(song.get("genre", "unknown")),
        "year": get_song_year(song),
        "lyric3": song.get("lyric3", ""),
        "lyric2": song.get("lyric2", ""),
        "lyric1": song.get("lyric1", ""),
        "quick_label": None,
    }


def build_round_payload(song, remaining_time=None, game_mode="full", quick_clue_key=None, total_time=None, round_audio=None):
    if game_mode in {"quick", "music_only"}:
        default_total_time = QUICK_ROUND_TIME
    else:
        default_total_time = TOTAL_ROUND_TIME

    resolved_total_time = int(total_time or default_total_time)
    if remaining_time is None:
        remaining_time = resolved_total_time

    lyric2_reveal_at = LYRIC2_REVEAL_AT if game_mode == "full" else None
    lyric1_reveal_at = LYRIC1_REVEAL_AT if game_mode == "full" else None

    return {
        "clues": build_clues(song, game_mode=game_mode, quick_clue_key=quick_clue_key),
        "game_mode": game_mode,
        "round_audio": round_audio,
        "timing": {
            "total_time": resolved_total_time,
            "lyric2_reveal_at": lyric2_reveal_at,
            "lyric1_reveal_at": lyric1_reveal_at,
            "remaining_time": remaining_time,
        },
    }


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
        return 0, "none"

    guess_clean = sanitize_text(guess)
    artist_clean = sanitize_text(artist)
    if guess_clean == artist_clean:
        return ARTIST_POINTS, "full"

    artist_words = split_words(artist)
    guess_words = set(split_words(guess))
    if not artist_words:
        return 0, "none"

    first_name = artist_words[0]
    last_name = artist_words[-1]

    if first_name in guess_words and last_name in guess_words:
        return ARTIST_POINTS, "full"
    if last_name in guess_words:
        return int(ARTIST_POINTS * 0.75), "last_name"
    if first_name in guess_words:
        return int(ARTIST_POINTS * 0.5), "first_name"

    return 0, "none"


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
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    results = data.get("results", [])
    title_clean = sanitize_text(title)
    artist_clean = sanitize_text(artist)

    candidates = []

    for item in results:
        preview_url = item.get("previewUrl")
        if not preview_url:
            continue

        track_name = sanitize_text(item.get("trackName", ""))
        artist_name = sanitize_text(item.get("artistName", ""))

        if title_clean in track_name and (artist_clean in artist_name or artist_name in artist_clean):
            candidates.append({
                "preview_url": preview_url,
                "track_name": item.get("trackName"),
                "artist_name": item.get("artistName"),
                "is_best_match": True,
            })

    for item in results:
        preview_url = item.get("previewUrl")
        if preview_url:
            candidates.append({
                "preview_url": preview_url,
                "track_name": item.get("trackName"),
                "artist_name": item.get("artistName"),
                "is_best_match": False,
            })

    if not candidates:
        return None

    unique = []
    seen = set()
    for candidate in candidates:
        key = candidate["preview_url"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) == 3:
            break

    return {
        "preview_url": unique[0]["preview_url"],
        "track_name": unique[0]["track_name"],
        "artist_name": unique[0]["artist_name"],
        "candidates": unique,
    }
