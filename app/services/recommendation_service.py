import re
import json
import logging
import httpx
from typing import List, Dict, Any, Optional, Set, Tuple
from collections import defaultdict
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc

from app.core.config import settings
from app.db.models import Movie, WebSeries, CinematicUniverse, UniverseContent, WatchlistItem, Review, User
from app.schemas.movies import MovieResponse
from app.schemas.recommendations import (
    RecommendationRequest,
    RecommendedItem,
    RecommendationResponse,
    UserProfileSummary
)
from app.api.v1.endpoints.movies import movie_to_movie_response, series_to_movie_response

logger = logging.getLogger(__name__)

# Mood to Genre Affinities Mapping
MOOD_AFFINITIES: Dict[str, Dict[str, int]] = {
    "mind-bending": {"Sci-Fi": 45, "Mystery": 30, "Thriller": 25},
    "mind bending": {"Sci-Fi": 45, "Mystery": 30, "Thriller": 25},
    "adrenaline": {"Action": 50, "Thriller": 30, "Adventure": 20},
    "adrenaline rush": {"Action": 50, "Thriller": 30, "Adventure": 20},
    "action-packed": {"Action": 60, "Thriller": 20, "Adventure": 20},
    "feel-good": {"Comedy": 40, "Romance": 30, "Drama": 30},
    "feel good": {"Comedy": 40, "Romance": 30, "Drama": 30},
    "wholesome": {"Comedy": 40, "Family": 35, "Drama": 25},
    "spine-chilling": {"Horror": 55, "Thriller": 30, "Mystery": 15},
    "spine chilling": {"Horror": 55, "Thriller": 30, "Mystery": 15},
    "scary": {"Horror": 60, "Thriller": 40},
    "dark": {"Thriller": 40, "Crime": 35, "Drama": 25},
    "cozy": {"Family": 35, "Comedy": 35, "Animation": 30},
    "chill": {"Comedy": 40, "Drama": 30, "Music": 30},
    "brainy": {"Mystery": 45, "Crime": 30, "Drama": 25},
    "puzzle": {"Mystery": 50, "Thriller": 30, "Sci-Fi": 20},
    "whodunit": {"Mystery": 55, "Crime": 35, "Thriller": 10},
    "romantic": {"Romance": 60, "Drama": 25, "Comedy": 15},
    "love": {"Romance": 60, "Drama": 20, "Comedy": 20},
    "epic saga": {"Adventure": 35, "Fantasy": 35, "Action": 30},
    "epic": {"Action": 40, "Adventure": 35, "History": 25},
    "nostalgic": {"Adventure": 35, "Family": 35, "Fantasy": 30},
    "melancholic": {"Drama": 60, "Romance": 25, "Music": 15},
    "emotional": {"Drama": 65, "Romance": 20, "Family": 15},
    "tearjerker": {"Drama": 70, "Romance": 30},
    "laugh out loud": {"Comedy": 75, "Family": 25},
    "funny": {"Comedy": 70, "Action": 15, "Romance": 15},
    "superhero": {"Action": 50, "Sci-Fi": 30, "Adventure": 20},
    "thought provoking": {"Drama": 40, "Sci-Fi": 35, "Mystery": 25},
    "thought-provoking": {"Drama": 40, "Sci-Fi": 35, "Mystery": 25},
}

# Standard Genre Names
STANDARD_GENRES = [
    "Action", "Adventure", "Animation", "Comedy", "Crime",
    "Documentary", "Drama", "Family", "Fantasy", "History", "Horror",
    "Music", "Mystery", "Romance", "Sci-Fi", "Sport", "Thriller", "War", "Western"
]

def clean_title_for_franchise(title: str) -> str:
    """Extracts franchise root from titles like 'Spider-Man 2' -> 'Spider-Man'"""
    # Remove subtitles after colon or dash
    base = re.split(r'[:\-–—]', title)[0].strip()
    # Remove trailing numbers or roman numerals
    base = re.sub(r'\s+(Part\s+\d+|Chapter\s+\d+|[0-9]+|[IVXLCDM]+)$', '', base, flags=re.IGNORECASE).strip()
    return base

def extract_sequel_index(title: str) -> Optional[int]:
    """Tries to extract part/sequel index from title"""
    m = re.search(r'(?:Part|Chapter)\s+(\d+)', title, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m2 = re.search(r'\b(\d+)$', title.strip())
    if m2:
        return int(m2.group(1))
    roman_map = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}
    m3 = re.search(r'\b(Part|Chapter)?\s*([IVX]+)$', title.strip(), re.IGNORECASE)
    if m3 and m3.group(2).upper() in roman_map:
        return roman_map[m3.group(2).upper()]
    return None

def compute_user_profile(user_id: Optional[int], db: Session) -> Dict[str, Any]:
    """Analyzes user's watch history, watchlist, and reviews to extract tastes and franchises."""
    if not user_id:
        return {
            "watched_ids": set(),
            "watchlist_ids": set(),
            "reviewed_ids": set(),
            "watched_items": [],
            "watchlist_items": [],
            "genre_taste": {},
            "top_genres": {},
            "universe_ids": set(),
            "favorite_universes": [],
            "history_count": 0,
            "watchlist_count": 0,
            "reviews_count": 0
        }

    # Fetch user's watch history (completed) and watchlist (to_watch / watching)
    watch_records = db.query(WatchlistItem).filter(WatchlistItem.user_id == user_id).all()
    reviews = db.query(Review).filter(Review.user_id == user_id).all()

    completed_ids = set()
    watchlist_ids = set()
    for w in watch_records:
        if w.status == "completed":
            completed_ids.add(w.content_id)
        elif w.status in ("to_watch", "watching"):
            watchlist_ids.add(w.content_id)

    reviewed_ids = {r.content_id for r in reviews}
    all_user_content_ids = list(completed_ids | watchlist_ids | reviewed_ids)

    # Fetch movie & series objects
    movies = db.query(Movie).filter(Movie.id.in_(all_user_content_ids)).all() if all_user_content_ids else []
    series = db.query(WebSeries).filter(WebSeries.series_id.in_(all_user_content_ids)).all() if all_user_content_ids else []

    item_map: Dict[str, Any] = {}
    for m in movies:
        item_map[m.id] = m
    for s in series:
        item_map[s.series_id] = s

    # Aggregate genre preferences
    genre_weights = defaultdict(float)

    # From reviews: highest weight
    for r in reviews:
        if r.user_genre_percentages and isinstance(r.user_genre_percentages, dict):
            mult = (r.rating / 10.0) * 1.5 if r.rating else 1.0
            for g, pct in r.user_genre_percentages.items():
                genre_weights[g] += float(pct) * mult

    # From completed watch history: high weight
    for cid in completed_ids:
        item = item_map.get(cid)
        if item and item.genre_percentages and isinstance(item.genre_percentages, dict):
            for g, pct in item.genre_percentages.items():
                genre_weights[g] += float(pct) * 1.2

    # From watchlist: moderate weight
    for cid in watchlist_ids:
        item = item_map.get(cid)
        if item and item.genre_percentages and isinstance(item.genre_percentages, dict):
            for g, pct in item.genre_percentages.items():
                genre_weights[g] += float(pct) * 0.8

    # Normalize genre taste to percentages
    total_weight = sum(genre_weights.values())
    genre_taste: Dict[str, int] = {}
    if total_weight > 0:
        sorted_genres = sorted(genre_weights.items(), key=lambda x: x[1], reverse=True)
        running_sum = 0
        for i, (g, w) in enumerate(sorted_genres[:6]):
            pct = round((w / total_weight) * 100)
            if i == len(sorted_genres[:6]) - 1:
                pct = max(1, 100 - running_sum)
            running_sum += pct
            genre_taste[g] = pct

    # Check cinematic universes touched
    universe_contents = db.query(UniverseContent).filter(UniverseContent.content_id.in_(all_user_content_ids)).all() if all_user_content_ids else []
    universe_ids = {uc.universe_id for uc in universe_contents}
    
    favorite_universes = []
    if universe_ids:
        univs = db.query(CinematicUniverse).filter(CinematicUniverse.id.in_(list(universe_ids))).all()
        favorite_universes = [u.name for u in univs]

    return {
        "watched_ids": completed_ids,
        "watchlist_ids": watchlist_ids,
        "reviewed_ids": reviewed_ids,
        "watched_items": [item_map[cid] for cid in completed_ids if cid in item_map],
        "watchlist_items": [item_map[cid] for cid in watchlist_ids if cid in item_map],
        "genre_taste": genre_taste,
        "top_genres": dict(list(genre_taste.items())[:5]),
        "universe_ids": universe_ids,
        "favorite_universes": favorite_universes,
        "history_count": len(completed_ids),
        "watchlist_count": len(watchlist_ids),
        "reviews_count": len(reviews)
    }

def find_prequels_and_sequels(
    user_profile: Dict[str, Any],
    all_movies: List[Movie],
    all_series: List[WebSeries],
    db: Session
) -> List[Dict[str, Any]]:
    """Identifies prequels, sequels, and cinematic universe continuations based on user history/watchlist."""
    watched_ids = user_profile["watched_ids"]
    watchlist_ids = user_profile["watchlist_ids"]
    known_user_ids = watched_ids | watchlist_ids

    results: List[Dict[str, Any]] = []
    seen_candidate_ids: Set[str] = set()

    # Map all catalog items by ID and title
    catalog_map: Dict[str, Any] = {}
    title_to_item: Dict[str, List[Any]] = defaultdict(list)
    for m in all_movies:
        catalog_map[m.id] = m
        title_to_item[clean_title_for_franchise(m.title).lower()].append(m)
    for s in all_series:
        catalog_map[s.series_id] = s
        title_to_item[clean_title_for_franchise(s.title).lower()].append(s)

    # 1. Cinematic Universe Graph Traversal
    universe_contents = db.query(UniverseContent).options(joinedload(UniverseContent.universe)).order_by(UniverseContent.universe_id, UniverseContent.order_in_universe).all()
    universe_groups = defaultdict(list)
    for uc in universe_contents:
        universe_groups[uc.universe_id].append(uc)

    for uid, items in universe_groups.items():
        # Check if user watched or watchlisted anything in this universe
        user_univ_items = [u for u in items if u.content_id in known_user_ids]
        if not user_univ_items:
            continue

        # Sort items by order_in_universe
        items_sorted = sorted(items, key=lambda x: (x.order_in_universe if x.order_in_universe is not None else 9999))
        content_id_list = [u.content_id for u in items_sorted]

        for user_item in user_univ_items:
            idx = content_id_list.index(user_item.content_id)
            source_obj = catalog_map.get(user_item.content_id)
            source_title = source_obj.title if source_obj else "earlier movie"
            univ_name = user_item.universe.name if user_item.universe else "Universe"

            # Check next in universe (Sequel / Universe Next)
            if idx + 1 < len(content_id_list):
                next_cid = content_id_list[idx + 1]
                if next_cid not in watched_ids and next_cid not in seen_candidate_ids:
                    cand_obj = catalog_map.get(next_cid)
                    if cand_obj:
                        seen_candidate_ids.add(next_cid)
                        results.append({
                            "item": cand_obj,
                            "type": "sequel",
                            "related_title": source_title,
                            "reason": f"Direct sequel / next entry to '{source_title}' in the {univ_name}.",
                            "score": 98
                        })

            # Check previous in universe (Prequel)
            if idx - 1 >= 0:
                prev_cid = content_id_list[idx - 1]
                if prev_cid not in watched_ids and prev_cid not in seen_candidate_ids:
                    cand_obj = catalog_map.get(prev_cid)
                    if cand_obj:
                        seen_candidate_ids.add(prev_cid)
                        results.append({
                            "item": cand_obj,
                            "type": "prequel",
                            "related_title": source_title,
                            "reason": f"Prequel chapter to '{source_title}' in the {univ_name}.",
                            "score": 95
                        })

    # 2. Franchise Title Pattern Matching
    for user_item in (user_profile["watched_items"] + user_profile["watchlist_items"]):
        base_title = clean_title_for_franchise(user_item.title).lower()
        if len(base_title) < 4:
            continue

        franchise_peers = title_to_item.get(base_title, [])
        if len(franchise_peers) > 1:
            source_idx = extract_sequel_index(user_item.title)
            source_year = user_item.release_year or 0

            for peer in franchise_peers:
                peer_id = getattr(peer, "id", None) or getattr(peer, "series_id", None)
                if not peer_id or peer_id in watched_ids or peer_id in seen_candidate_ids:
                    continue

                peer_idx = extract_sequel_index(peer.title)
                peer_year = peer.release_year or 0

                is_sequel = False
                is_prequel = False

                if source_idx is not None and peer_idx is not None:
                    if peer_idx > source_idx:
                        is_sequel = True
                    elif peer_idx < source_idx:
                        is_prequel = True
                elif peer_year and source_year:
                    if peer_year > source_year:
                        is_sequel = True
                    elif peer_year < source_year:
                        is_prequel = True

                if is_sequel:
                    seen_candidate_ids.add(peer_id)
                    results.append({
                        "item": peer,
                        "type": "sequel",
                        "related_title": user_item.title,
                        "reason": f"Sequel to '{user_item.title}' from your { 'watch history' if peer_id in watched_ids else 'entertainment collection' }.",
                        "score": 96
                    })
                elif is_prequel:
                    seen_candidate_ids.add(peer_id)
                    results.append({
                        "item": peer,
                        "type": "prequel",
                        "related_title": user_item.title,
                        "reason": f"Prequel to '{user_item.title}' in the same story series.",
                        "score": 93
                    })

    return results

def compute_target_genre_distribution(mood: Optional[str], genres: Optional[List[str]]) -> Dict[str, int]:
    """Blends input mood and selected genres into a target genre percentage vector."""
    target_genres = defaultdict(float)

    # 1. Parse mood keywords
    if mood:
        mood_lower = mood.lower()
        # Direct mood phrase matching
        matched_mood_terms = False
        for phrase, affinities in MOOD_AFFINITIES.items():
            if phrase in mood_lower:
                matched_mood_terms = True
                for g, w in affinities.items():
                    target_genres[g] += float(w)

        # Match single words if phrase didn't catch all
        words = re.findall(r'\b\w+\b', mood_lower)
        for w in words:
            if w in MOOD_AFFINITIES:
                for g, weight in MOOD_AFFINITIES[w].items():
                    target_genres[g] += float(weight) * 0.7
            # Also check if the word is an exact standard genre
            for std_g in STANDARD_GENRES:
                if std_g.lower() == w:
                    target_genres[std_g] += 60.0

    # 2. Add explicit genres
    if genres:
        for g in genres:
            target_genres[g] += 80.0

    # If nothing matched, provide balanced default
    if not target_genres:
        return {"Drama": 30, "Action": 30, "Comedy": 20, "Thriller": 20}

    # Normalize to 100%
    total = sum(target_genres.values())
    res: Dict[str, int] = {}
    running = 0
    items_sorted = sorted(target_genres.items(), key=lambda x: x[1], reverse=True)
    for i, (g, val) in enumerate(items_sorted):
        pct = round((val / total) * 100)
        if i == len(items_sorted) - 1:
            pct = max(1, 100 - running)
        running += pct
        res[g] = pct
    return res

def calculate_genre_match_score(
    item_percentages: Optional[Dict[str, Any]],
    target_distribution: Dict[str, int]
) -> Tuple[int, Dict[str, int]]:
    """Calculates compatibility score (0-100) and overlap breakdown between item and target genres."""
    if not item_percentages or not isinstance(item_percentages, dict):
        return 50, {}

    overlap_score = 0.0
    breakdown = {}
    for g, target_pct in target_distribution.items():
        if g in item_percentages:
            actual_pct = float(item_percentages[g])
            # High overlap when both have significant percentage
            min_pct = min(target_pct, actual_pct)
            overlap_score += min_pct
            breakdown[g] = int(actual_pct)

    # Normalize overlap score
    score = min(100, max(20, int(overlap_score * 1.35)))
    return score, breakdown

def generate_ai_recommendations_with_gemini(
    mood: Optional[str],
    genres: Optional[List[str]],
    user_profile: Dict[str, Any],
    candidate_catalog: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Attempts to prompt Gemini API for personalized recommendations and narrative reasoning."""
    if not settings.GEMINI_API_KEY:
        return None

    # Keep candidate catalog compact for LLM context
    condensed_catalog = [
        {
            "id": c["id"],
            "title": c["title"],
            "type": c["type"],
            "genres": c.get("genre_percentages", {}),
            "rating": c.get("rating"),
            "year": c.get("year"),
            "description": c.get("description", "")[:120]
        }
        for c in candidate_catalog[:60]
    ]

    watched_titles = [m.title for m in user_profile["watched_items"][:5]]
    watchlist_titles = [m.title for m in user_profile["watchlist_items"][:5]]
    user_top_genres = user_profile.get("top_genres", {})

    system_prompt = (
        "You are PrimeFlix's Master AI Cinema & Series Recommender. "
        "Recommend the most fitting titles from the provided catalog based on the user's input mood, genre desires, "
        "and their historical watch taste.\n\n"
        f"User Watch History Titles: {json.dumps(watched_titles)}\n"
        f"User Watchlist Titles: {json.dumps(watchlist_titles)}\n"
        f"User Learned Top Genres: {json.dumps(user_top_genres)}\n\n"
        f"Catalog of Candidates:\n{json.dumps(condensed_catalog, indent=2)}\n\n"
        "RESPONSE RULES:\n"
        "1. Select 6 to 10 title IDs that match the mood, genre, and user taste.\n"
        "2. For each recommendation, provide a compelling reason explaining why it fits (referencing mood, genre percentages, or past watched titles).\n"
        "3. Provide an overarching 'ai_explanation' summarizing the recommendation vibe.\n"
        "4. Respond with STRICT JSON ONLY in the following format:\n"
        "{\n"
        '  "ai_explanation": "Summary of tailored recommendations...",\n'
        '  "recommendations": [\n'
        '    {"id": "MOV-1", "reason": "Because you love mind-bending Sci-Fi...", "score": 95},\n'
        '    {"id": "SER-1396", "reason": "Matches your adrenaline rush mood...", "score": 92}\n'
        '  ]\n'
        "}"
    )

    user_query = f"Mood: {mood or 'Open to discovery'}, Desired Genres: {genres or 'All'}"

    try:
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
        request_body = {
            "contents": [
                {"parts": [{"text": f"{system_prompt}\n\n{user_query}"}]}
            ],
            "generationConfig": {
                "temperature": 0.65,
                "response_mime_type": "application/json"
            }
        }
        with httpx.Client(timeout=12.0) as client:
            res = client.post(gemini_url, json=request_body)
            if res.status_code == 200:
                data = res.json()
                candidates = data.get("candidates", [])
                if candidates:
                    content_text = candidates[0]["content"]["parts"][0]["text"]
                    return json.loads(content_text)
            else:
                logger.warning(f"Gemini API returned status {res.status_code} in recommendation engine: {res.text[:150]}")
    except Exception as e:
        logger.warning(f"Gemini AI recommendation failed or timed out, switching to algorithmic engine: {e}")

    return None

def get_ai_recommendations(
    request: RecommendationRequest,
    current_user: Optional[User],
    db: Session
) -> RecommendationResponse:
    """Core AI Recommendation Engine that blends user profile, franchise relations, mood/genre, and Gemini AI."""
    user_id = current_user.id if current_user else None
    user_profile = compute_user_profile(user_id, db)

    # 1. Fetch catalog
    normalized_type = (request.content_type or "all").lower().strip()
    include_movies = normalized_type in ("all", "movie", "movies")
    include_series = normalized_type in ("all", "web_series", "series", "tv")

    all_movies = db.query(Movie).options(joinedload(Movie.director)).all() if include_movies else []
    all_series = db.query(WebSeries).options(joinedload(WebSeries.director)).all() if include_series else []

    all_movie_responses: Dict[str, MovieResponse] = {}
    for m in all_movies:
        all_movie_responses[m.id] = movie_to_movie_response(m)
    for s in all_series:
        all_movie_responses[s.series_id] = series_to_movie_response(s)

    # 2. Prequels and Sequels extraction
    sequels_and_prequels_items: List[RecommendedItem] = []
    if request.include_prequels_sequels and user_id:
        raw_prequels_sequels = find_prequels_and_sequels(user_profile, all_movies, all_series, db)
        for ps in raw_prequels_sequels:
            item_id = getattr(ps["item"], "id", None) or getattr(ps["item"], "series_id", None)
            if item_id and item_id in all_movie_responses:
                resp = all_movie_responses[item_id]
                sequels_and_prequels_items.append(
                    RecommendedItem(
                        content=resp,
                        recommendation_reason=ps["reason"],
                        recommendation_type=ps["type"],
                        related_title=ps["related_title"],
                        match_score=ps["score"],
                        genre_match_breakdown=resp.genre_percentages if isinstance(resp.genre_percentages, dict) else None
                    )
                )

    # 3. Target Genre Distribution from Mood & Requested Genres
    target_genre_dist = compute_target_genre_distribution(request.mood, request.genres)

    # 4. Score all unwatched catalog items algorithmically
    watched_ids = user_profile["watched_ids"]
    scored_candidates: List[Dict[str, Any]] = []

    for item_id, resp in all_movie_responses.items():
        # Exclude titles already completed in watch history
        if item_id in watched_ids:
            continue

        genre_score, breakdown = calculate_genre_match_score(resp.genre_percentages, target_genre_dist)
        
        # User history taste alignment boost
        user_taste_boost = 0.0
        if user_profile["genre_taste"] and resp.genre_percentages and isinstance(resp.genre_percentages, dict):
            for ug, upct in user_profile["genre_taste"].items():
                if ug in resp.genre_percentages:
                    user_taste_boost += (float(resp.genre_percentages[ug]) / 100.0) * (upct / 100.0) * 15.0

        # Rating boost
        rating_boost = ((resp.average_rating or 7.0) / 10.0) * 10.0

        # Watchlist bonus (if already in watchlist, user showed intent!)
        watchlist_boost = 10.0 if item_id in user_profile["watchlist_ids"] else 0.0

        total_match_score = min(99, max(30, int(genre_score * 0.65 + user_taste_boost + rating_boost + watchlist_boost)))

        scored_candidates.append({
            "id": item_id,
            "title": resp.title,
            "type": resp.content_type,
            "response": resp,
            "genre_percentages": resp.genre_percentages,
            "rating": resp.average_rating,
            "year": resp.release_year,
            "description": resp.description or "",
            "calculated_score": total_match_score,
            "genre_breakdown": breakdown,
            "in_watchlist": item_id in user_profile["watchlist_ids"]
        })

    # Sort scored candidates descending
    scored_candidates.sort(key=lambda x: x["calculated_score"], reverse=True)

    # 5. Try Gemini AI for narrative explanations & fine-tuned selection
    gemini_data = None
    if request.mood or request.genres:
        gemini_data = generate_ai_recommendations_with_gemini(
            request.mood,
            request.genres,
            user_profile,
            scored_candidates
        )

    ai_explanation = ""
    personalized_items: List[RecommendedItem] = []
    mood_genre_items: List[RecommendedItem] = []
    history_inspired_items: List[RecommendedItem] = []

    seen_in_personalized: Set[str] = set()

    # If Gemini succeeded
    if gemini_data and "recommendations" in gemini_data:
        ai_explanation = gemini_data.get("ai_explanation", "")
        for rec in gemini_data["recommendations"]:
            cid = rec.get("id")
            if cid and cid in all_movie_responses and cid not in seen_in_personalized:
                seen_in_personalized.add(cid)
                resp = all_movie_responses[cid]
                score = rec.get("score", 92)
                reason = rec.get("reason", f"Selected specifically for your vibe and taste.")
                
                personalized_items.append(
                    RecommendedItem(
                        content=resp,
                        recommendation_reason=reason,
                        recommendation_type="mood_match" if request.mood else "genre_taste",
                        match_score=min(99, max(50, score)),
                        genre_match_breakdown=resp.genre_percentages if isinstance(resp.genre_percentages, dict) else None
                    )
                )

    # If Gemini wasn't available or had quota exceeded, synthesize high-quality intelligent recommendations
    if not ai_explanation:
        mood_str = f"'{request.mood}'" if request.mood else ""
        genres_str = ", ".join(request.genres) if request.genres else ""
        if mood_str and genres_str:
            ai_explanation = f"Curated for your mood {mood_str} and genre preferences ({genres_str}), aligned with your personalized viewing profile."
        elif mood_str:
            ai_explanation = f"AI matched cinema selections fine-tuned for your {mood_str} vibe, matching story percentages and narrative tone."
        elif genres_str:
            ai_explanation = f"Top-rated titles dominating the {genres_str} genres, calibrated against your watchlist and watch history."
        elif user_profile["top_genres"]:
            top_g_list = ", ".join([f"{g} ({p}%)" for g, p in list(user_profile["top_genres"].items())[:3]])
            ai_explanation = f"Personalized cinema recommendations based on your favorite genre distributions: {top_g_list}."
        else:
            ai_explanation = "Top critical and audience-rated recommendations curated for your discovery."

    # Populate algorithmic personalized items if needed
    for candidate in scored_candidates:
        cid = candidate["id"]
        if cid in seen_in_personalized:
            continue
        if len(personalized_items) >= (request.limit or 10):
            break

        seen_in_personalized.add(cid)
        resp = candidate["response"]
        
        # Dynamic, intelligent reasoning
        reason = ""
        rec_type = "mood_match"
        related_title = None

        if candidate["in_watchlist"]:
            reason = f"High priority from your Watchlist matching your {request.mood or 'preferred'} vibe."
            rec_type = "watchlist_companion"
        elif user_profile["watched_items"] and candidate["calculated_score"] > 85:
            # Find closest watched item in genres
            sample_watched = user_profile["watched_items"][0]
            related_title = sample_watched.title
            reason = f"Shares DNA with '{related_title}' from your history: rich in {', '.join(list(candidate['genre_breakdown'].keys())[:2]) or 'compelling story'}."
            rec_type = "history_inspired"
        elif request.mood:
            reason = f"Strong match for '{request.mood}' with {', '.join([f'{g} {p}%' for g, p in list(candidate['genre_breakdown'].items())[:2]])}."
            rec_type = "mood_match"
        else:
            top_genre_pair = list(resp.genre_percentages.items())[:2] if isinstance(resp.genre_percentages, dict) else []
            genre_tag = ", ".join([f"{g} ({p}%)" for g, p in top_genre_pair])
            reason = f"Exceptional {genre_tag} storytelling matching your viewing habits."
            rec_type = "genre_taste"

        personalized_items.append(
            RecommendedItem(
                content=resp,
                recommendation_reason=reason,
                recommendation_type=rec_type,
                related_title=related_title,
                match_score=candidate["calculated_score"],
                genre_match_breakdown=candidate["genre_breakdown"]
            )
        )

    # 6. Specific categories: Mood & Genre Picks, History-Inspired Picks
    for candidate in scored_candidates:
        cid = candidate["id"]
        resp = candidate["response"]
        if len(mood_genre_items) < 8 and candidate["genre_breakdown"]:
            mood_genre_items.append(
                RecommendedItem(
                    content=resp,
                    recommendation_reason=f"Optimal genre match for {', '.join(list(candidate['genre_breakdown'].keys())[:3])}.",
                    recommendation_type="mood_match" if request.mood else "genre_taste",
                    match_score=candidate["calculated_score"],
                    genre_match_breakdown=candidate["genre_breakdown"]
                )
            )

    # History inspired (titles similar to completed titles)
    if user_profile["watched_items"]:
        for cand in scored_candidates:
            if cand["id"] not in seen_in_personalized and len(history_inspired_items) < 8:
                resp = cand["response"]
                history_inspired_items.append(
                    RecommendedItem(
                        content=resp,
                        recommendation_reason=f"Recommended because you completed titles in {', '.join(list(user_profile['top_genres'].keys())[:2])}.",
                        recommendation_type="history_inspired",
                        related_title=user_profile["watched_items"][0].title,
                        match_score=max(75, cand["calculated_score"]),
                        genre_match_breakdown=cand["genre_breakdown"]
                    )
                )

    user_summary = UserProfileSummary(
        top_genres=user_profile["top_genres"],
        history_count=user_profile["history_count"],
        watchlist_count=user_profile["watchlist_count"],
        reviews_count=user_profile["reviews_count"],
        favorite_universes=user_profile["favorite_universes"]
    ) if user_id else None

    return RecommendationResponse(
        query_mood=request.mood,
        query_genres=request.genres,
        ai_explanation=ai_explanation,
        personalized_for_you=personalized_items[:(request.limit or 10)],
        sequels_and_prequels=sequels_and_prequels_items[:8],
        mood_and_genre_picks=mood_genre_items[:8],
        history_inspired=history_inspired_items[:8],
        user_profile=user_summary
    )
