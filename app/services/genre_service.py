import re
import json
import logging
from typing import Dict, List, Optional
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Standard film genres
CANONICAL_GENRES = [
    "Action", "Adventure", "Animation", "Biography", "Comedy", "Crime",
    "Documentary", "Drama", "Family", "Fantasy", "History", "Horror",
    "Music", "Mystery", "Romance", "Sci-Fi", "Sport", "Thriller", "War", "Western"
]

# Thematic story keyword dictionaries to analyze plot text
GENRE_STORY_KEYWORDS = {
    "Action": [
        "fight", "chase", "explos", "gun", "combat", "soldier", "battle", "agent", "assassin",
        "martial arts", "weapon", "danger", "mission", "rescue", "warrior", "hero", "survival",
        "revenge", "strike", "hostage", "attack", "pursuit", "destroy", "lethal", "fury"
    ],
    "Adventure": [
        "journey", "quest", "explore", "treasure", "expedition", "island", "jungle", "lost",
        "travel", "voyage", "mountain", "discover", "wilderness", "exotic", "peril", "path", "odyssey"
    ],
    "Sci-Fi": [
        "alien", "space", "future", "technology", "robot", "cyber", "ai", "artificial intelligence",
        "planet", "galaxy", "time travel", "portal", "dimension", "scientific", "laboratory",
        "clone", "matrix", "virtual", "starship", "dystop", "teleport", "quantum"
    ],
    "Thriller": [
        "conspiracy", "suspense", "psycho", "paranoia", "trap", "twist", "cat-and-mouse", "terror",
        "danger", "tense", "stalk", "secret", "threat", "blackmail", "deadly", "deception", "heist"
    ],
    "Mystery": [
        "detective", "murder", "clue", "investigat", "uncover", "solve", "disappear", "suspect",
        "riddle", "enigma", "truth", "crime scene", "whodunit", "evidence", "witness"
    ],
    "Horror": [
        "ghost", "demon", "monster", "haunt", "blood", "curse", "evil", "darkness", "nightmare",
        "creepy", "slasher", "possession", "supernatural", "fear", "creature", "zombie", "terror"
    ],
    "Comedy": [
        "hilarious", "laugh", "funny", "humor", "mischief", "awkward", "satire", "parody",
        "prank", "goofy", "bizarre", "eccentric", "amusing", "wit", "buddy", "ridiculous"
    ],
    "Drama": [
        "struggle", "relationship", "emotional", "family", "tragedy", "loss", "grief", "moral",
        "conflict", "redemption", "heartbreaking", "life", "personal", "hardship", "forgive", "destiny"
    ],
    "Romance": [
        "love", "romance", "passion", "couple", "kiss", "heart", "marriage", "crush", "lover",
        "dating", "attraction", "soulmate", "bride", "wedding", "fall in love", "romantic"
    ],
    "Crime": [
        "gang", "mob", "drug", "cartel", "cop", "police", "robbery", "thief", "mafia", "prison",
        "syndicate", "underworld", "illegal", "convict", "smuggl", "jail", "corruption", "bribe"
    ],
    "Fantasy": [
        "magic", "wizard", "spell", "dragon", "myth", "enchanted", "kingdom", "realm", "legend",
        "prophecy", "fairy", "sorcerer", "sword", "curse", "powers", "immortal"
    ],
    "Animation": [
        "animated", "cartoon", "cgi", "anime", "pixar", "illustrated", "talking animal"
    ],
    "Biography": [
        "true story", "biograph", "based on the life", "real-life", "memoir", "historical figure", "chronicles the life"
    ],
    "History": [
        "historical", "century", "empire", "ancient", "monarch", "dynasty", "reign", "world war", "civil war", "revolution"
    ],
    "War": [
        "war", "military", "army", "battlefield", "trench", "troops", "invasion", "wwii", "vietnam", "navy", "combatant"
    ],
    "Western": [
        "cowboy", "outlaw", "sheriff", "saloon", "frontier", "wild west", "ranch", "bounty hunter", "horses", "gunslinger"
    ],
    "Music": [
        "singer", "song", "band", "concert", "musician", "musical", "album", "orchestra", "rock", "melody"
    ],
    "Sport": [
        "champion", "coach", "boxing", "football", "baseball", "tournament", "athlete", "game", "race", "stadium"
    ],
    "Family": [
        "children", "kid", "youth", "growing up", "wholesome", "pet", "school", "parent"
    ]
}


def normalize_percentages(raw_scores: Dict[str, float], top_n: int = 3) -> Dict[str, int]:
    """
    Takes a dictionary of genre scores, picks the top N dominant genres,
    and scales them into integer percentages that sum to exactly 100.
    """
    if not raw_scores:
        return {"Drama": 100}

    # Sort descending by score
    sorted_genres = sorted(raw_scores.items(), key=lambda x: x[1], reverse=True)
    selected = sorted_genres[:top_n]

    total_score = sum(score for _, score in selected)
    if total_score <= 0:
        return {g: int(100 / len(selected)) for g, _ in selected}

    # Calculate integer percentages
    percentages = {}
    current_sum = 0
    for i, (g, score) in enumerate(selected):
        if i == len(selected) - 1:
            # Last item absorbs rounding difference to guarantee exact sum of 100
            val = max(5, 100 - current_sum)
            percentages[g] = val
        else:
            val = max(10, int(round((score / total_score) * 100)))
            # Don't let single item take 100% unless it's the only one
            if len(selected) > 1 and val >= 95:
                val = 80
            percentages[g] = val
            current_sum += val

    # Verify and fix sum
    diff = 100 - sum(percentages.values())
    if diff != 0:
        top_genre = selected[0][0]
        percentages[top_genre] += diff

    # Final filter: ensure all are >= 5 and sorted descending
    sorted_res = dict(sorted(percentages.items(), key=lambda x: x[1], reverse=True))
    return sorted_res


def analyze_story_lexical(title: str, description: str, base_genre_str: Optional[str] = None) -> Dict[str, int]:
    """
    Computes genre percentages based on story analysis using semantic keyword density
    and existing genre hints.
    """
    text = f"{title} {description or ''}".lower()
    scores: Dict[str, float] = {}

    # 1. Base genre prior (if existing genres are known, give them a foundation)
    if base_genre_str:
        for g_raw in base_genre_str.split(","):
            g = g_raw.strip()
            # Canonicalize
            if g.lower() == "action & adventure":
                scores["Action"] = scores.get("Action", 0) + 3.0
                scores["Adventure"] = scores.get("Adventure", 0) + 3.0
            elif g.lower() == "sci-fi & fantasy":
                scores["Sci-Fi"] = scores.get("Sci-Fi", 0) + 3.0
                scores["Fantasy"] = scores.get("Fantasy", 0) + 3.0
            elif g.lower() == "science fiction":
                scores["Sci-Fi"] = scores.get("Sci-Fi", 0) + 3.5
            else:
                for c in CANONICAL_GENRES:
                    if c.lower() == g.lower():
                        scores[c] = scores.get(c, 0) + 3.5

    # 2. Match story keywords against the description text
    for genre, keywords in GENRE_STORY_KEYWORDS.items():
        count = 0
        for kw in keywords:
            # Match whole or partial words
            pattern = r"\b" + re.escape(kw)
            matches = len(re.findall(pattern, text))
            count += matches
        if count > 0:
            scores[genre] = scores.get(genre, 0) + (count * 1.5)

    # 3. Ensure at least one score
    if not scores:
        scores["Drama"] = 5.0

    return normalize_percentages(scores, top_n=3)


def batch_analyze_stories_with_gemini(items: List[Dict[str, str]]) -> Dict[str, Dict[str, int]]:
    """
    Uses Gemini API to analyze a batch of film stories and returns genre percentages.
    items format: [{'id': 'MOV-1', 'title': '...', 'description': '...', 'genres': '...'}]
    """
    if not settings.GEMINI_API_KEY:
        return {}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"

    system_instruction = (
        "You are an expert film narrative & story genre analyst. "
        "Analyze each movie/series storyline, narrative tone, plot devices, and themes from its description. "
        "Assign 2 to 4 dominant genres as percentages based on the story. "
        "CRITICAL RULES:\n"
        "1. Standard genre names to use: Action, Adventure, Animation, Biography, Comedy, Crime, Documentary, "
        "Drama, Family, Fantasy, History, Horror, Music, Mystery, Romance, Sci-Fi, Sport, Thriller, War, Western.\n"
        "2. The percentages for each title MUST be positive integers summing to EXACTLY 100 (e.g. {\"Action\": 55, \"Sci-Fi\": 30, \"Thriller\": 15}).\n"
        "3. Output MUST be a valid JSON object mapping title IDs to their genre percentages object:\n"
        "{\n"
        "  \"MOV-1\": {\"Action\": 50, \"Sci-Fi\": 30, \"Thriller\": 20},\n"
        "  \"SER-1\": {\"Crime\": 60, \"Drama\": 40}\n"
        "}"
    )

    batch_payload = [
        {
            "id": it["id"],
            "title": it["title"],
            "base_genres": it.get("genres", ""),
            "story": (it.get("description") or "")[:400]
        }
        for it in items
    ]

    prompt = (
        f"{system_instruction}\n\n"
        f"Titles to analyze:\n{json.dumps(batch_payload, indent=2)}\n\n"
        "Respond ONLY with the JSON object."
    )

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "response_mime_type": "application/json"
        }
    }

    try:
        with httpx.Client(timeout=35.0) as client:
            res = client.post(url, json=body)
            if res.status_code == 200:
                data = res.json()
                candidates = data.get("candidates", [])
                if candidates:
                    text_out = candidates[0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text_out)
                    # Validate and fix sums
                    results = {}
                    for item_id, g_dict in parsed.items():
                        if isinstance(g_dict, dict) and g_dict:
                            cleaned = {}
                            for k, v in g_dict.items():
                                try:
                                    cleaned[k.strip()] = int(v)
                                except Exception:
                                    continue
                            total = sum(cleaned.values())
                            if total > 0:
                                # Scale or fix diff to ensure exact 100
                                if total != 100:
                                    diff = 100 - total
                                    top_k = max(cleaned, key=cleaned.get)
                                    cleaned[top_k] += diff
                                # Sort descending
                                results[item_id] = dict(sorted(cleaned.items(), key=lambda x: x[1], reverse=True))
                    return results
            else:
                logger.warning(f"Gemini batch call status {res.status_code}: {res.text[:150]}")
    except Exception as e:
        logger.error(f"Gemini story analysis batch error: {e}")

    return {}
