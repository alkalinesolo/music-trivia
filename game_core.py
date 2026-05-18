from urllib.parse import quote_plus
from urllib.request import urlopen
import json
import time

TOTAL_ROUND_TIME = 60
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
            "guesses": {},
            "current_song": None,
            "round_timer": None,
            "round_started_at": None,
            "last_results": None,
            "owner_sid": None,
            "owner_name": None,
            "rounds_per_game": 5,
            "decades_filter": {"1960s", "1970s", "1980s", "1990s"},
            "genres_filter": {"rock", "pop", "country", "r&b", "motown", "hip hop", "hip-hop", "hip_hop_r_and_b", "r_and_b_soul", "reggae", "grunge", "alternative rock", "soft rock", "folk rock"},
        }
    return rooms[room_code]


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
    if not room_state["round_started_at"]:
        return TOTAL_ROUND_TIME

    elapsed = int(time.time() - room_state["round_started_at"])
    return max(0, TOTAL_ROUND_TIME - elapsed)


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
    if normalized in {"r_and_b_soul", "hip_hop_r_and_b"}:
        return "R&B"

    return str(genre).replace("_", " ").title()


def build_clues(song):
    return {
        "genre": song.get("genre", "unknown"),
        "genre_label": format_genre_label(song.get("genre", "unknown")),
        "year": get_song_year(song),
        "lyric3": song.get("lyric3", ""),
        "lyric2": song.get("lyric2", ""),
        "lyric1": song.get("lyric1", ""),
    }


def build_round_payload(song, remaining_time=None):
    if remaining_time is None:
        remaining_time = TOTAL_ROUND_TIME

    return {
        "clues": build_clues(song),
        "timing": {
            "total_time": TOTAL_ROUND_TIME,
            "lyric2_reveal_at": LYRIC2_REVEAL_AT,
            "lyric1_reveal_at": LYRIC1_REVEAL_AT,
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
