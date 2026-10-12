import re
import json
import uuid
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
import httpx
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, or_, func

from app.core.config import settings
from app.db.models import (
    User,
    Movie,
    WebSeries,
    Director,
    WatchlistItem,
    Review,
    WatchHistory,
    UserInteraction,
    AgentConversation,
    AgentMessage,
    CinematicUniverse,
    UniverseContent
)
from app.schemas.agent import (
    WatchProgressRequest,
    WatchProgressResponse,
    UserInteractionRequest,
    UserInteractionResponse,
    TasteProfileResponse,
    AgentActionResult,
    AgentChatResponse
)
from app.schemas.recommendations import RecommendedItem
from app.api.v1.endpoints.movies import movie_to_movie_response, series_to_movie_response

logger = logging.getLogger(__name__)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# =========================================================================
# 1. Watch Progress & User Interactions
# =========================================================================

def record_watch_progress(
    user_id: int,
    req: WatchProgressRequest,
    db: Session
) -> WatchProgressResponse:
    """Records or updates playback timestamps and calculates completion rate."""
    clean_cid = req.content_id.strip()
    
    # Check existing history for this content
    q = db.query(WatchHistory).filter(
        WatchHistory.user_id == user_id,
        WatchHistory.content_id == clean_cid
    )
    if req.episode_id:
        q = q.filter(WatchHistory.episode_id == req.episode_id.strip())
    
    history_entry = q.first()
    
    duration = req.duration_seconds
    # If duration not provided in request, look up from movie or episode
    if not duration or duration <= 0:
        if req.content_type == "movie":
            m = db.query(Movie).filter(Movie.id == clean_cid).first()
            if m and m.duration:
                duration = m.duration * 60
        else:
            from app.db.models import Episode
            if req.episode_id:
                ep = db.query(Episode).filter(Episode.episode_id == req.episode_id.strip()).first()
                if ep and ep.duration:
                    duration = ep.duration * 60

    completion_rate = 0.0
    if duration and duration > 0:
        completion_rate = min(1.0, round(req.progress_seconds / duration, 3))
    
    is_completed = bool(req.completed) if req.completed is not None else (completion_rate >= 0.85)

    if not history_entry:
        history_entry = WatchHistory(
            user_id=user_id,
            content_id=clean_cid,
            content_type=req.content_type,
            episode_id=req.episode_id.strip() if req.episode_id else None,
            progress_seconds=req.progress_seconds,
            duration_seconds=duration,
            completion_rate=completion_rate,
            completed=is_completed,
            watched_at=utcnow()
        )
        db.add(history_entry)
    else:
        history_entry.progress_seconds = req.progress_seconds
        if duration:
            history_entry.duration_seconds = duration
        history_entry.completion_rate = max(history_entry.completion_rate, completion_rate)
        if is_completed:
            history_entry.completed = True
        history_entry.watched_at = utcnow()

    # If completed, update watchlist item to 'completed' status if it exists
    if is_completed:
        wl = db.query(WatchlistItem).filter(
            WatchlistItem.user_id == user_id,
            WatchlistItem.content_id == clean_cid
        ).first()
        if wl:
            wl.status = "completed"
            wl.completed_at = utcnow()

    db.commit()
    db.refresh(history_entry)

    return WatchProgressResponse(
        content_id=history_entry.content_id,
        content_type=history_entry.content_type,
        progress_seconds=history_entry.progress_seconds,
        duration_seconds=history_entry.duration_seconds,
        completion_rate=history_entry.completion_rate,
        completed=history_entry.completed,
        watched_at=history_entry.watched_at
    )


def record_user_interaction(
    user_id: int,
    req: UserInteractionRequest,
    db: Session
) -> UserInteractionResponse:
    """Records explicit user engagement (like, dislike, favorite, rating)."""
    clean_cid = req.content_id.strip()
    clean_type = req.interaction_type.lower().strip()

    # Check existing interaction of same type
    existing = db.query(UserInteraction).filter(
        UserInteraction.user_id == user_id,
        UserInteraction.content_id == clean_cid,
        UserInteraction.interaction_type == clean_type
    ).first()

    if existing:
        if req.rating is not None:
            existing.rating = req.rating
        db.commit()
        db.refresh(existing)
        return UserInteractionResponse(
            id=existing.id,
            content_id=existing.content_id,
            content_type=existing.content_type,
            interaction_type=existing.interaction_type,
            rating=existing.rating,
            created_at=existing.created_at
        )

    # If liking, remove any opposing dislike
    if clean_type in ("like", "favorite"):
        db.query(UserInteraction).filter(
            UserInteraction.user_id == user_id,
            UserInteraction.content_id == clean_cid,
            UserInteraction.interaction_type == "dislike"
        ).delete()
    elif clean_type == "dislike":
        db.query(UserInteraction).filter(
            UserInteraction.user_id == user_id,
            UserInteraction.content_id == clean_cid,
            UserInteraction.interaction_type.in_(["like", "favorite"])
        ).delete()

    entry = UserInteraction(
        user_id=user_id,
        content_id=clean_cid,
        content_type=req.content_type,
        interaction_type=clean_type,
        rating=req.rating,
        created_at=utcnow()
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return UserInteractionResponse(
        id=entry.id,
        content_id=entry.content_id,
        content_type=entry.content_type,
        interaction_type=entry.interaction_type,
        rating=entry.rating,
        created_at=entry.created_at
    )


# =========================================================================
# 2. Taste Profile Computation
# =========================================================================

def get_user_taste_profile(user_id: Optional[int], db: Session) -> TasteProfileResponse:
    """Builds an aggregated user taste profile from watch histories and interactions."""
    if not user_id:
        return TasteProfileResponse(
            user_id=None,
            top_genres={},
            top_directors=[],
            completed_count=0,
            in_progress_count=0,
            liked_count=0,
            disliked_count=0,
            watchlist_count=0,
            total_watch_time_minutes=0,
            recently_completed=[],
            in_progress=[],
            liked_titles=[]
        )

    # 1. Watch History
    histories = db.query(WatchHistory).filter(WatchHistory.user_id == user_id).all()
    completed_items = [h for h in histories if h.completed]
    in_progress_items = [h for h in histories if not h.completed and h.progress_seconds > 60]

    total_seconds = sum(h.progress_seconds for h in histories)
    total_minutes = int(total_seconds / 60)

    # 2. Interactions
    interactions = db.query(UserInteraction).filter(UserInteraction.user_id == user_id).all()
    liked_interactions = [i for i in interactions if i.interaction_type in ("like", "favorite")]
    disliked_interactions = [i for i in interactions if i.interaction_type == "dislike"]

    # 3. Watchlist
    wl_count = db.query(WatchlistItem).filter(WatchlistItem.user_id == user_id).count()

    # Resolve content details for completed, in_progress, and liked
    all_content_ids = list(set([h.content_id for h in histories] + [i.content_id for i in interactions]))
    
    movies_map = {
        m.id: m for m in db.query(Movie).options(joinedload(Movie.director)).filter(Movie.id.in_(all_content_ids)).all()
    } if all_content_ids else {}

    series_map = {
        s.series_id: s for s in db.query(WebSeries).options(joinedload(WebSeries.director)).filter(WebSeries.series_id.in_(all_content_ids)).all()
    } if all_content_ids else {}

    genre_counts: Dict[str, float] = {}
    director_counts: Dict[str, int] = {}

    def extract_genres(item: Any, weight: float = 1.0):
        if not item:
            return
        # Genre percentages JSON
        if hasattr(item, "genre_percentages") and item.genre_percentages:
            for g, pct in item.genre_percentages.items():
                genre_counts[g] = genre_counts.get(g, 0.0) + (pct * weight)
        elif hasattr(item, "genre") and item.genre:
            for g in item.genre.split(","):
                g_clean = g.strip()
                if g_clean:
                    genre_counts[g_clean] = genre_counts.get(g_clean, 0.0) + (50.0 * weight)
        # Directors
        if hasattr(item, "director") and item.director and item.director.name:
            d_name = item.director.name.strip()
            director_counts[d_name] = director_counts.get(d_name, 0) + 1

    # Give highest weight to completed and liked
    for c in completed_items:
        m = movies_map.get(c.content_id) or series_map.get(c.content_id)
        extract_genres(m, weight=1.5)

    for l in liked_interactions:
        m = movies_map.get(l.content_id) or series_map.get(l.content_id)
        extract_genres(m, weight=2.0)

    # Normalize genre percentages
    total_genre_weight = sum(genre_counts.values()) or 1.0
    normalized_genres = {
        g: int(round((w / total_genre_weight) * 100))
        for g, w in sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:6]
    }

    top_directors = [
        d for d, _ in sorted(director_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    ]

    def item_to_dict(cid: str) -> Dict[str, Any]:
        item = movies_map.get(cid) or series_map.get(cid)
        if not item:
            return {"content_id": cid, "title": "Unknown Title"}
        return {
            "content_id": cid,
            "title": item.title,
            "poster_image_url": getattr(item, "poster_image_url", None),
            "genre": getattr(item, "genre", None),
            "average_rating": getattr(item, "average_rating", None),
            "director": item.director.name if getattr(item, "director", None) else None
        }

    return TasteProfileResponse(
        user_id=user_id,
        top_genres=normalized_genres,
        top_directors=top_directors,
        completed_count=len(completed_items),
        in_progress_count=len(in_progress_items),
        liked_count=len(liked_interactions),
        disliked_count=len(disliked_interactions),
        watchlist_count=wl_count,
        total_watch_time_minutes=total_minutes,
        recently_completed=[item_to_dict(c.content_id) for c in completed_items[:5]],
        in_progress=[item_to_dict(i.content_id) for i in in_progress_items[:5]],
        liked_titles=[item_to_dict(l.content_id) for l in liked_interactions[:5]]
    )


# =========================================================================
# 3. Autonomous Agent Tools
# =========================================================================

# =========================================================================
# 3. Autonomous Agent Tools & Cinema Entity Resolution
# =========================================================================

STOP_WORDS = {
    "every", "all", "the", "a", "an", "is", "are", "where", "there", "in", "on",
    "with", "and", "or", "to", "for", "of", "about", "show", "shows", "movie",
    "movies", "film", "films", "series", "watch", "tell", "me", "find", "suggest",
    "recommend", "list", "give", "please", "can", "you", "which", "what",
    "first", "last", "latest", "newest", "best", "top", "director", "directed", "by",
    "season", "seasons", "episode", "episodes", "many", "how", "total", "number",
    "random", "any", "some", "good", "great", "nice", "popular", "famous", "trending",
    "hit", "classic", "standout", "acclaimed", "unknown", "spontaneous", "pick", "picks",
    "title", "titles", "cinema", "something", "tonight", "today", "now"
}

# Rich Character & Franchise Knowledge Base
CHARACTER_DATABASE: Dict[str, Dict[str, Any]] = {
    "tony_stark": {
        "aliases": ["tony shark", "tony stark", "iron man", "ironman", "stark", "robert downey", "downey jr"],
        "character_name": "Tony Stark / Iron Man",
        "actor": "Robert Downey Jr.",
        "universe_name": "Marvel Cinematic Universe",
        "universe_id": 7,
        "titles": [
            ("Iron Man", 2008, "The birth of the MCU: billionaire Tony Stark builds the Mark I armor to escape captivity and famously announces: 'I am Iron Man.'"),
            ("Iron Man 2", 2010, "Tony confronts Ivan Vanko (Whiplash) and rival weapons maker Justin Hammer while dealing with palladium core poisoning."),
            ("The Avengers", 2012, "Tony joins Earth's Mightiest Heroes to repel Loki and the Chitauri invasion, flying the nuclear missile into space."),
            ("Iron Man 3", 2013, "Grappling with PTSD after the Battle of New York, Tony faces Aldrich Killian and the counterfeit Mandarin."),
            ("Avengers: Age of Ultron", 2015, "Tony invents the global defense AI Ultron to protect Earth, but must fight his rogue creation when it turns genocidal."),
            ("Captain America: Civil War", 2016, "A bitter ideological rift over the Sokovia Accords fractures the Avengers, pitting Tony against Steve Rogers and Bucky Barnes."),
            ("Spider-Man: Homecoming", 2017, "Tony mentors young Peter Parker, testing his responsibility and providing his high-tech Stark suits."),
            ("Avengers: Infinity War", 2018, "Tony takes the fight directly to Titan in an epic confrontation against the Mad Titan Thanos."),
            ("Avengers: Endgame", 2019, "The emotional culmination of the Infinity Saga: Tony discovers quantum time travel and executes the final Nano Gauntlet snap to restore the universe.")
        ]
    },
    "steve_rogers": {
        "aliases": ["steve rogers", "captain america", "first avenger", "chris evans", "cap"],
        "character_name": "Steve Rogers / Captain America",
        "actor": "Chris Evans",
        "universe_name": "Marvel Cinematic Universe",
        "universe_id": 7,
        "titles": [
            ("Captain America: The First Avenger", 2011, "Steve Rogers volunteers for Project Rebirth and battles Red Skull and Hydra across WWII Europe."),
            ("The Avengers", 2012, "Awakened in the modern era, Captain America takes tactical leadership of the Avengers during the Battle of New York."),
            ("Captain America: The Winter Soldier", 2014, "Cap uncovers a massive Hydra conspiracy inside S.H.I.E.L.D. and faces his brainwashed friend Bucky Barnes."),
            ("Avengers: Age of Ultron", 2015, "Cap leads the assault against Ultron in Sokovia to safeguard civilian lives."),
            ("Captain America: Civil War", 2016, "Cap defies government oversight and the Sokovia Accords to protect Bucky, fracturing the Avengers."),
            ("Avengers: Infinity War", 2018, "Operating underground as Nomad, Cap leads the vanguard defense of Wakanda against the Outriders."),
            ("Avengers: Endgame", 2019, "Cap wields Mjolnir, leads the final charge with 'Avengers Assemble!', and journeys back in time to reunite with Peggy Carter.")
        ]
    },
    "peter_parker": {
        "aliases": ["peter parker", "spider-man", "spiderman", "spider man", "web slinger", "tom holland"],
        "character_name": "Peter Parker / Spider-Man",
        "actor": "Tom Holland",
        "universe_name": "Marvel Cinematic Universe",
        "universe_id": 7,
        "titles": [
            ("Captain America: Civil War", 2016, "Recruited by Tony Stark in Queens for the thrilling airport battle in Leipzig."),
            ("Spider-Man: Homecoming", 2017, "Peter navigates high-school life while proving his superhero worth against the Vulture."),
            ("Avengers: Infinity War", 2018, "Peter is officially knighted as an Avenger aboard the Black Order spaceship."),
            ("Avengers: Endgame", 2019, "Peter emerges through the portal on Earth to aid in the final battle and say goodbye to Tony."),
            ("Spider-Man: Far from Home", 2019, "Traveling Europe on a school trip, Peter battles Mysterio and steps up in a post-Iron Man world."),
            ("Spider-Man: No Way Home", 2021, "Peter's identity is revealed, unleashing multiverse villains and uniting three generations of Spider-Men.")
        ]
    },
    "thor": {
        "aliases": ["thor", "god of thunder", "odinson", "chris hemsworth"],
        "character_name": "Thor Odinson",
        "actor": "Chris Hemsworth",
        "universe_name": "Marvel Cinematic Universe",
        "universe_id": 7,
        "titles": [
            ("Thor", 2011, "Stripped of his godly power and cast out to Earth, Thor must discover true humility to reclaim Mjolnir."),
            ("The Avengers", 2012, "Thor descends upon Earth to stop his trickster brother Loki and retrieve the Tesseract."),
            ("Thor: The Dark World", 2013, "Thor partners with Loki to defeat Malekith and protect the Reality Stone (Aether)."),
            ("Avengers: Age of Ultron", 2015, "Thor uncovers visions of the Infinity Stones at the Water of Sights."),
            ("Thor: Ragnarok", 2017, "Stranded on Sakaar, Thor loses his hammer, battles the Hulk, and unleashes his inner lightning against Hela."),
            ("Avengers: Infinity War", 2018, "Thor forges Stormbreaker with Eitri and makes a thunderous entrance into the Battle of Wakanda."),
            ("Avengers: Endgame", 2019, "Thor joins the Time Heist and dual-wields Mjolnir and Stormbreaker against Thanos."),
            ("Thor: Love and Thunder", 2022, "Thor teams up with Mighty Thor (Jane Foster) and King Valkyrie to stop Gorr the God Butcher.")
        ]
    },
    "batman": {
        "aliases": ["batman", "bruce wayne", "dark knight", "caped crusader", "christian bale"],
        "character_name": "Bruce Wayne / Batman",
        "actor": "Christian Bale / Robert Pattinson",
        "titles": [
            ("Batman Begins", 2005, "Bruce Wayne trains under Ra's al Ghul and returns to save a corrupt Gotham as the Batman."),
            ("The Dark Knight", 2008, "Batman engages in an iconic battle of morals and chaos against the Joker."),
            ("The Dark Knight Rises", 2012, "Eight years later, Batman emerges from retirement and overcomes Bane to save Gotham from nuclear annihilation."),
            ("The Batman", 2022, "A noir detective Batman investigates a trail of cryptic clues left by the Riddler in Gotham's underworld.")
        ]
    }
}


def match_character_in_query(query_lower: str) -> Optional[Dict[str, Any]]:
    """Checks if a user query refers to a known superhero or iconic character."""
    for char_key, data in CHARACTER_DATABASE.items():
        if any(alias in query_lower for alias in data["aliases"]):
            return data
    return None


def match_universe_in_query(query_lower: str, db: Session) -> Optional[CinematicUniverse]:
    """Checks if query references any cinematic universe stored in the database."""
    universes = db.query(CinematicUniverse).all()
    
    # Direct alias mappings
    u_aliases = {
        "marvel": "marvel cinematic universe",
        "mcu": "marvel cinematic universe",
        "avengers": "marvel cinematic universe",
        "dc": "dc cinematic universe",
        "dceu": "dc cinematic universe",
        "star wars": "star wars universe",
        "harry potter": "wizarding world",
        "wizarding": "wizarding world",
        "fast and furious": "fast & furious saga",
        "fast & furious": "fast & furious saga",
        "lord of the rings": "middle-earth universe",
        "middle earth": "middle-earth universe",
        "middle-earth": "middle-earth universe",
        "hobbit": "middle-earth universe",
        "mission impossible": "mission: impossible",
        "mission: impossible": "mission: impossible",
        "transformers": "transformers universe",
        "james bond": "james bond 007",
        "007": "james bond 007",
        "ghibli": "studio ghibli universe",
        "jurassic": "jurassic universe",
        "monsterverse": "monsterverse",
        "spider-verse": "spider-verse & sony marvel universe"
    }

    target_name = None
    for term, u_name in u_aliases.items():
        if term in query_lower:
            target_name = u_name
            break

    if target_name:
        for u in universes:
            if u_name.lower() in u.name.lower():
                return u

    for u in universes:
        if u.name.lower() in query_lower:
            return u

    return None


# =========================================================================
# 2.5 Sequel, Prequel & Viewing Continuation Intelligence
# =========================================================================

def extract_continuation_target(query: str) -> Optional[str]:
    """Detects sequential questions ('what to watch after X', 'sequel to X', 'what is next after X')."""
    q = query.strip()
    patterns = [
        r'\b(?:what\s+(?:to|should\s+i|can\s+i|do\s+i)\s+(?:watch|see|stream)\s+after)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$',
        r'\b(?:what(?:\'s|\s+is)?\s+next\s+after)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$',
        r'\b(?:what\s+comes\s+after)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$',
        r'\b(?:next\s+(?:movie|film|part|installment|chapter)?\s+after)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$',
        r'\b(?:sequel\s+to|sequel\s+of)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$',
        r'\b(?:watch\s+order\s+after|watch\s+next\s+after)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$',
        r'\b(?:what\s+follows)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$',
    ]
    for p in patterns:
        m = re.search(p, q, re.IGNORECASE)
        if m:
            clean = m.group(1).strip(" \"'?!.")
            return clean
    return None


NUMBERED_CONTINUATION_ALIASES = {
    "harry potter 1": "Harry Potter and the Sorcerer's Stone",
    "harry potter 2": "Harry Potter and the Chamber of Secrets",
    "harry potter 3": "Harry Potter and the Prisoner of Azkaban",
    "harry potter 4": "Harry Potter and the Goblet of Fire",
    "harry potter 5": "Harry Potter and the Order of the Phoenix",
    "harry potter 6": "Harry Potter and the Half-Blood Prince",
    "harry potter 7": "Harry Potter and the Deathly Hallows: Part 1",
    "harry potter 8": "Harry Potter and the Deathly Hallows: Part 2",
    "iron man 1": "Iron Man",
    "iron man 2": "Iron Man 2",
    "iron man 3": "Iron Man 3",
    "avengers 1": "The Avengers",
    "avengers 2": "Avengers: Age of Ultron",
    "avengers 3": "Avengers: Infinity War",
    "avengers 4": "Avengers: Endgame",
    "fast and furious 1": "The Fast and the Furious",
    "fast 1": "The Fast and the Furious",
    "mission impossible 1": "Mission: Impossible",
    "top gun 1": "Top Gun",
}


def find_movie_by_title_query(target: str, db: Session) -> Optional[Movie]:
    """Finds the most accurate matching movie given a freeform title query."""
    clean_target = target.lower().strip()
    if clean_target in NUMBERED_CONTINUATION_ALIASES:
        canonical = NUMBERED_CONTINUATION_ALIASES[clean_target]
        m = db.query(Movie).options(joinedload(Movie.director)).filter(Movie.title.ilike(canonical)).first()
        if m:
            return m

    # Direct exact/ilike match
    candidates = db.query(Movie).options(joinedload(Movie.director)).filter(Movie.title.ilike(f"%{clean_target}%")).all()
    if not candidates:
        words = [w for w in clean_target.split() if len(w) > 3 and w not in ("movie", "film", "part", "the", "and")]
        if words:
            q_filter = [Movie.title.ilike(f"%{w}%") for w in words]
            candidates = db.query(Movie).options(joinedload(Movie.director)).filter(or_(*q_filter)).all()
    if candidates:
        return min(candidates, key=lambda x: abs(len(x.title) - len(clean_target)))
    return None


def resolve_continuation_intent(user_msg: str, db: Session) -> Optional[Dict[str, Any]]:
    """
    Resolves continuation questions into direct sequel, universe viewing order, or related picks.
    """
    target_raw = extract_continuation_target(user_msg)
    if not target_raw:
        return None

    current_movie = find_movie_by_title_query(target_raw, db)
    if not current_movie:
        return None

    # 1. Check Cinematic Universe
    uc = db.query(UniverseContent).filter(UniverseContent.content_id == current_movie.id).first()
    if uc:
        universe = db.query(CinematicUniverse).filter(CinematicUniverse.id == uc.universe_id).first()
        u_name = universe.name if universe else "Cinematic Universe"

        all_uc = db.query(UniverseContent).filter(UniverseContent.universe_id == uc.universe_id).all()
        content_ids = [c.content_id for c in all_uc]
        all_movies = db.query(Movie).options(joinedload(Movie.director)).filter(Movie.id.in_(content_ids)).all()
        movie_map = {m.id: m for m in all_movies}

        # Sequentially numbered universes: Wizarding World, Fast & Furious, Mission: Impossible, Pirates
        if any(term in u_name.lower() for term in ["wizarding", "potter", "fast", "mission", "pirates"]):
            sorted_uc = sorted([c for c in all_uc if c.order_in_universe > uc.order_in_universe], key=lambda x: x.order_in_universe)
            remaining_movies = [movie_map[c.content_id] for c in sorted_uc if c.content_id in movie_map]

            if remaining_movies:
                return {
                    "type": "direct_sequel",
                    "current": current_movie,
                    "direct_next": remaining_movies[0],
                    "subsequent": remaining_movies[1:],
                    "universe_name": u_name,
                    "universe_order": uc.order_in_universe,
                    "is_finale": False
                }
            else:
                return {
                    "type": "universe_finale",
                    "current": current_movie,
                    "direct_next": None,
                    "subsequent": [],
                    "universe_name": u_name,
                    "universe_order": uc.order_in_universe,
                    "is_finale": True
                }
        else:
            # Universe like MCU / DC: check sub-franchise title first, then chronological
            prefix = current_movie.title.split(":")[0].split(" - ")[0].strip()
            prefix_words = [w for w in prefix.split() if w.lower() not in ("the", "a", "an")]
            franchise_root = " ".join(prefix_words[:2]) if prefix_words else prefix

            sub_franchise_next = [
                m for m in all_movies
                if franchise_root.lower() in m.title.lower()
                and (m.release_year or 0) > (current_movie.release_year or 0)
            ]
            sub_franchise_next.sort(key=lambda x: x.release_year or 0)

            if sub_franchise_next:
                direct_next = sub_franchise_next[0]
                subsequent = sub_franchise_next[1:]
                if len(subsequent) < 3:
                    universe_chronological = [
                        m for m in all_movies
                        if (m.release_year or 0) >= (direct_next.release_year or 0)
                        and m.id != direct_next.id and m not in subsequent
                    ]
                    universe_chronological.sort(key=lambda x: (x.release_year or 0, -(x.average_rating or 0)))
                    subsequent.extend(universe_chronological[:3])
                return {
                    "type": "direct_sequel",
                    "current": current_movie,
                    "direct_next": direct_next,
                    "subsequent": subsequent,
                    "universe_name": u_name,
                    "universe_order": uc.order_in_universe,
                    "is_finale": False
                }
            else:
                universe_chronological = [
                    m for m in all_movies
                    if (m.release_year or 0) >= (current_movie.release_year or 0)
                    and m.id != current_movie.id
                ]
                universe_chronological.sort(key=lambda x: x.release_year or 0)
                if universe_chronological:
                    return {
                        "type": "direct_sequel",
                        "current": current_movie,
                        "direct_next": universe_chronological[0],
                        "subsequent": universe_chronological[1:],
                        "universe_name": u_name,
                        "universe_order": uc.order_in_universe,
                        "is_finale": False
                    }
                else:
                    # Check if a direct sequel exists in the broader catalog (e.g. The Dark Knight Rises)
                    prefix = current_movie.title.split(":")[0].strip()
                    prefix_clean = re.escape(prefix)
                    candidates = db.query(Movie).options(joinedload(Movie.director)).filter(
                        Movie.title.ilike(f"%{prefix}%"),
                        Movie.id != current_movie.id,
                        (Movie.release_year or 0) > (current_movie.release_year or 0)
                    ).all()
                    valid_sequels = [
                        m for m in candidates
                        if re.search(r"\b" + prefix_clean + r"\b", m.title, re.IGNORECASE)
                        and (m.average_rating or 0) >= 5.5
                    ]
                    valid_sequels.sort(key=lambda x: (x.release_year or 0, -(x.average_rating or 0)))
                    if valid_sequels:
                        return {
                            "type": "direct_sequel",
                            "current": current_movie,
                            "direct_next": valid_sequels[0],
                            "subsequent": valid_sequels[1:],
                            "universe_name": u_name,
                            "universe_order": uc.order_in_universe,
                            "is_finale": False
                        }

                    return {
                        "type": "universe_finale",
                        "current": current_movie,
                        "direct_next": None,
                        "subsequent": [],
                        "universe_name": u_name,
                        "universe_order": uc.order_in_universe,
                        "is_finale": True
                    }

    # 2. Standalone movie or sub-franchise not in CinematicUniverse (e.g. Top Gun)
    prefix = current_movie.title.split(":")[0].strip()
    prefix_clean = re.escape(prefix)
    candidates = db.query(Movie).options(joinedload(Movie.director)).filter(
        Movie.title.ilike(f"%{prefix}%"),
        Movie.id != current_movie.id,
        Movie.release_year >= (current_movie.release_year or 1900)
    ).all()

    # Require word boundary to avoid mockbusters like "Top Gunner"
    valid_sequels = [
        m for m in candidates
        if re.search(r"\b" + prefix_clean + r"\b", m.title, re.IGNORECASE)
        and (m.average_rating or 0) >= 5.5
    ]
    valid_sequels.sort(key=lambda x: (x.release_year or 0, -(x.average_rating or 0)))

    if valid_sequels:
        return {
            "type": "direct_sequel",
            "current": current_movie,
            "direct_next": valid_sequels[0],
            "subsequent": valid_sequels[1:],
            "universe_name": f"{prefix} Series",
            "universe_order": 1,
            "is_finale": False
        }

    return {
        "type": "standalone",
        "current": current_movie,
        "direct_next": None,
        "subsequent": [],
        "universe_name": None,
        "universe_order": 1,
        "is_finale": False
    }


# =========================================================================
# 2.6 Director Intelligence & Filmography Resolution
# =========================================================================

DIRECTOR_INTENT_PATTERNS = [
    # First / debut
    (r'\b(?:what\s+(?:is|was)\s+(?:the\s+)?(?:first|debut|earliest)\s+(?:movie|film|project)\s+(?:of|by|from|directed\s+by))\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'first'),
    (r'\b(?:first|debut|earliest)\s+(?:movie|film|picture|project)\s+(?:of|by|from|directed\s+by)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'first'),

    # Latest / recent
    (r'\b(?:what\s+(?:is|was)\s+(?:the\s+)?(?:latest|recent|newest|last)\s+(?:movie|film|project)\s+(?:of|by|from|directed\s+by))\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'latest'),
    (r'\b(?:latest|recent|newest|last)\s+(?:movie|film|project)\s+(?:of|by|from|directed\s+by)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'latest'),

    # Best / top
    (r'\b(?:best|top|highest\s+rated|finest)\s+(?:movie|film)\s+(?:of|by|from|directed\s+by)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'best'),

    # General directed by / movies by
    (r'\b(?:movies?|films?|works?|cinema)\s+(?:directed\s+by|by|of|from)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'all'),
    (r'\b(?:what\s+(?:movies?|films?)\s+(?:did\s+)?([^?"’\n]+?)\s+(?:direct|make))\??$', 'all'),
    (r'\b(?:filmography\s+of)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'all'),
    (r'\b(?:directed\s+by)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'all'),
]


def resolve_director_intent(user_msg: str, db: Session) -> Optional[Dict[str, Any]]:
    """
    Detects director inquiries ('first movie of X', 'movies directed by X', 'latest movie by X').
    """
    q = user_msg.strip()
    matched_target = None
    intent_type = None

    for pattern, itype in DIRECTOR_INTENT_PATTERNS:
        m = re.search(pattern, q, re.IGNORECASE)
        if m:
            matched_target = m.group(1).strip(" \"'?!.")
            intent_type = itype
            break

    # If no pattern matched, check if any director name exists in query
    if not matched_target:
        tokens = [w for w in re.findall(r'\b\w+\b', q.lower()) if len(w) > 3 and w not in STOP_WORDS]
        if tokens:
            q_filter = [Director.name.ilike(f"%{w}%") for w in tokens]
            candidate_dirs = db.query(Director).filter(or_(*q_filter)).all()
            for d in candidate_dirs:
                if d.name.lower() in q.lower():
                    matched_target = d.name
                    intent_type = 'all'
                    break

    if not matched_target:
        return None

    # Search Director record
    director = db.query(Director).filter(Director.name.ilike(matched_target)).first()
    if not director:
        director = db.query(Director).filter(Director.name.ilike(f"%{matched_target}%")).first()
    if not director:
        words = [w for w in matched_target.split() if len(w) > 3]
        if words:
            q_filter = [Director.name.ilike(f"%{w}%") for w in words]
            director = db.query(Director).filter(or_(*q_filter)).first()

    if not director:
        return None

    movies = db.query(Movie).options(joinedload(Movie.director)).filter(
        Movie.director_id == director.id
    ).order_by(Movie.release_year.asc().nullslast()).all()

    if not movies:
        return None

    return {
        "director": director,
        "intent_type": intent_type,
        "movies": movies
    }


# =========================================================================
# 2.7 Web Series, Anime & Season Metadata Intelligence
# =========================================================================

SERIES_METADATA_PATTERNS = [
    # Seasons count
    (r'\b(?:how\s+many\s+seasons\s+(?:are\s+there\s+in|does\s+[\w\s]+\s+have|in|of))\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'seasons'),
    (r'\b(?:how\s+many\s+seasons\s+(?:does)\s+["\'“]?([^?"’\n]+?)["\'”]?\s+(?:have))\??$', 'seasons'),
    (r'\b(?:number\s+of\s+seasons\s+(?:in|of))\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'seasons'),
    (r'\b(?:total\s+seasons\s+(?:in|of))\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'seasons'),

    # Episodes count
    (r'\b(?:how\s+many\s+episodes\s+(?:are\s+there\s+in|does\s+[\w\s]+\s+have|in|of))\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'episodes'),
    (r'\b(?:how\s+many\s+episodes\s+(?:does)\s+["\'“]?([^?"’\n]+?)["\'”]?\s+(?:have))\??$', 'episodes'),
    (r'\b(?:number\s+of\s+episodes\s+(?:in|of))\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'episodes'),
    (r'\b(?:total\s+episodes\s+(?:in|of))\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'episodes'),

    # General overview / seasons breakdown
    (r'\b(?:seasons\s+of)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'overview'),
    (r'\b(?:season\s+breakdown\s+(?:of|for))\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'overview'),
    (r'\b(?:tell\s+me\s+about\s+(?:the\s+)?series)\s+["\'“]?([^?"’\n]+?)["\'”]?\??$', 'overview'),
]


def resolve_series_metadata_intent(user_msg: str, db: Session) -> Optional[Dict[str, Any]]:
    """
    Detects series and season inquiries ('how many seasons in X', 'how many episodes in X').
    """
    q = user_msg.strip()
    matched_target = None
    intent_type = None

    for pattern, itype in SERIES_METADATA_PATTERNS:
        m = re.search(pattern, q, re.IGNORECASE)
        if m:
            matched_target = m.group(1).strip(" \"'?!.")
            intent_type = itype
            break

    if not matched_target:
        return None

    # Search in WebSeries table
    series = db.query(WebSeries).options(
        joinedload(WebSeries.director),
        joinedload(WebSeries.seasons)
    ).filter(WebSeries.title.ilike(matched_target)).first()

    if not series:
        series = db.query(WebSeries).options(
            joinedload(WebSeries.director),
            joinedload(WebSeries.seasons)
        ).filter(WebSeries.title.ilike(f"%{matched_target}%")).first()

    if not series:
        words = [w for w in matched_target.split() if len(w) > 3 and w not in STOP_WORDS]
        if words:
            q_filter = [WebSeries.title.ilike(f"%{w}%") for w in words]
            series = db.query(WebSeries).options(
                joinedload(WebSeries.director),
                joinedload(WebSeries.seasons)
            ).filter(or_(*q_filter)).first()

    if not series:
        return None

    seasons_list = sorted(series.seasons or [], key=lambda s: s.season_number)
    total_episodes = sum((s.episode_count or len(s.episodes) or 0) for s in seasons_list)

    return {
        "series": series,
        "intent_type": intent_type,
        "seasons_count": series.total_seasons or len(seasons_list),
        "total_episodes": total_episodes,
        "seasons_list": seasons_list
    }


# =========================================================================
# 2.8 Specific Episode & Chapter Intelligence
# =========================================================================

def resolve_episode_intent(user_msg: str, db: Session) -> Optional[Dict[str, Any]]:
    """
    Detects episode-specific inquiries (e.g. 'what is the name of episode 57 in attack on titan',
    'attack on titan season 3 episode 20', 'episode 1 of breaking bad', 'which episode is that day in attack on titan').
    """
    from app.db.models import Season, Episode
    q_clean = user_msg.strip().rstrip("?!.").strip()

    # 1. Season X Episode Y pattern: "season 3 episode 20 of attack on titan"
    s_num = None
    ep_num = None
    series_raw = None

    m_se = re.search(
        r'(?:what\s+is\s+(?:the\s+)?(?:name|title|plot|synopsis)\s+of\s+)?(?:season|s)\s*(\d+)\s*(?:episode|ep)\s*(\d+)\s+(?:in|of|from)\s+(.+)',
        q_clean,
        re.I
    )
    if m_se:
        s_num, ep_num, series_raw = int(m_se.group(1)), int(m_se.group(2)), m_se.group(3)
    else:
        m_se = re.search(
            r'(.+?)\s+(?:season|s)\s*(\d+)\s*(?:episode|ep)\s*(\d+)',
            q_clean,
            re.I
        )
        if m_se:
            series_raw, s_num, ep_num = m_se.group(1), int(m_se.group(2)), int(m_se.group(3))

    # 2. Overall Episode N pattern: "episode 57 in attack on titan"
    overall_ep_num = None
    if not s_num:
        m_ep = re.search(
            r'(?:what\s+is\s+(?:the\s+)?(?:name|title|plot|synopsis)\s+of\s+)?(?:episode|ep)\s*(?:#|no\.?|number)?\s*(\d+)\s+(?:in|of|from)\s+(.+)',
            q_clean,
            re.I
        )
        if m_ep:
            overall_ep_num, series_raw = int(m_ep.group(1)), m_ep.group(2)
        else:
            m_ep = re.search(
                r'(.+?)\s+(?:episode|ep)\s*(?:#|no\.?|number)?\s*(\d+)',
                q_clean,
                re.I
            )
            if m_ep:
                series_raw, overall_ep_num = m_ep.group(1), int(m_ep.group(2))

    # 3. Episode by Title: "what episode is That Day in Attack on Titan" or "which episode is That Day in Attack on Titan"
    ep_title_search = None
    if not s_num and not overall_ep_num:
        m_title = re.search(
            r'(?:which|what)\s+episode\s+is\s+["\'“]?([^?"’\n]+?)["\'”]?\s+(?:in|of|from)\s+(.+)',
            q_clean,
            re.I
        )
        if m_title:
            ep_title_search, series_raw = m_title.group(1).strip(), m_title.group(2).strip()

    if not series_raw:
        return None

    # Clean series_raw
    series_raw = series_raw.strip(" \"'?!.")
    if not series_raw:
        return None

    # Find series in WebSeries
    series = db.query(WebSeries).filter(WebSeries.title.ilike(series_raw)).first()
    if not series:
        series = db.query(WebSeries).filter(WebSeries.title.ilike(f"%{series_raw}%")).first()
    if not series:
        words = [w for w in series_raw.split() if len(w) > 3 and w.lower() not in STOP_WORDS]
        if words:
            q_filter = [WebSeries.title.ilike(f"%{w}%") for w in words]
            series = db.query(WebSeries).filter(or_(*q_filter)).first()

    if not series:
        return None

    seasons = db.query(Season).filter(Season.series_id == series.series_id).order_by(Season.season_number).all()
    if not seasons:
        return None

    target_ep = None
    target_season = None
    calc_overall_num = overall_ep_num

    # Case A: Title search
    if ep_title_search:
        for s in seasons:
            ep = db.query(Episode).filter(
                Episode.season_id == s.season_id,
                Episode.title.ilike(f"%{ep_title_search}%")
            ).first()
            if ep:
                target_ep = ep
                target_season = s
                break
        if target_ep and target_season:
            cum = 0
            for s in seasons:
                if s.season_number < target_season.season_number:
                    eps_count = db.query(func.count(Episode.episode_id)).filter(Episode.season_id == s.season_id).scalar() or (s.episode_count or 0)
                    cum += eps_count
                else:
                    break
            calc_overall_num = cum + target_ep.episode_number

    # Case B: Overall episode number
    elif overall_ep_num:
        cum = 0
        for s in seasons:
            eps = db.query(Episode).filter(Episode.season_id == s.season_id).order_by(Episode.episode_number).all()
            if cum < overall_ep_num <= cum + len(eps):
                local_ep_num = overall_ep_num - cum
                for ep in eps:
                    if ep.episode_number == local_ep_num:
                        target_ep = ep
                        target_season = s
                        break
                break
            cum += len(eps)

    # Case C: Season X Episode Y
    elif s_num and ep_num:
        for s in seasons:
            if s.season_number == s_num:
                target_season = s
                target_ep = db.query(Episode).filter(
                    Episode.season_id == s.season_id,
                    Episode.episode_number == ep_num
                ).first()
                break
        if target_season and target_ep:
            cum = 0
            for s in seasons:
                if s.season_number < target_season.season_number:
                    eps_count = db.query(func.count(Episode.episode_id)).filter(Episode.season_id == s.season_id).scalar() or (s.episode_count or 0)
                    cum += eps_count
                else:
                    break
            calc_overall_num = cum + target_ep.episode_number

    if target_ep and target_season:
        return {
            "series": series,
            "season": target_season,
            "episode": target_ep,
            "overall_episode_number": calc_overall_num,
            "local_episode_number": target_ep.episode_number,
            "season_number": target_season.season_number
        }

    return None


def is_cinema_knowledge_query(text: str) -> bool:
    """
    Distinguishes informational/trivia/lore questions from purely generic recommendation queries.
    """
    t = text.lower().strip()
    q_starters = (
        "what is", "what was", "what are", "what were",
        "who is", "who was", "who are", "who were", "who played", "who directs", "who directed",
        "why is", "why was", "why did", "why does",
        "how is", "how was", "how did", "how does", "how many",
        "where is", "where was", "when was", "when did",
        "can you explain", "explain the", "tell me about", "what happens in",
        "is there", "does ", "did ", "which episode", "what episode", "what movie"
    )
    if any(t.startswith(qs) for qs in q_starters):
        # Exclude purely generic recommendation requests
        if any(rec in t for rec in ["what should i watch", "what to watch", "what can i watch", "what do you recommend"]):
            return False
        return True

    knowledge_keywords = [
        "meaning of", "ending of", "plot of", "synopsis", "trivia", "lore", 
        "timeline", "order to watch", "who played", "actor of", "character of", 
        "episode name", "what is the name", "who won", "box office", "budget of",
        "post-credit", "easter egg"
    ]
    return any(kw in t for kw in knowledge_keywords)


LANGUAGE_ALIAS_MAP: Dict[str, List[str]] = {
    "tamil": ["ta", "tamil"],
    "hindi": ["hi", "hindi"],
    "telugu": ["te", "telugu"],
    "malayalam": ["ml", "malayalam"],
    "kannada": ["kn", "kannada"],
    "korean": ["ko", "korean"],
    "japanese": ["ja", "japanese"],
    "spanish": ["es", "spanish"],
    "french": ["fr", "french"],
    "german": ["de", "german"],
    "italian": ["it", "italian"],
    "english": ["en", "english"],
    "chinese": ["zh", "cn", "chinese"]
}


def build_language_filter(model_col, aliases: List[str]):
    """Constructs SQL filter preventing 2-letter ISO codes (e.g. 'ta') from matching substrings in other languages (e.g. 'Italian')."""
    conditions = []
    for a in aliases:
        if len(a) <= 2:
            conditions.append(model_col == a)
            conditions.append(model_col.ilike(f"{a},%"))
            conditions.append(model_col.ilike(f"%, {a},%"))
            conditions.append(model_col.ilike(f"%, {a}"))
            conditions.append(model_col.ilike(f"{a} %"))
        else:
            conditions.append(model_col.ilike(f"%{a}%"))
    return or_(*conditions)


def parse_user_query_constraints(text: str) -> Dict[str, Any]:
    """Extracts explicit numeric constraints from natural language (count, rating, duration, year, language)."""
    t = text.lower()

    # 1. Rating
    min_rating = None
    max_rating = None
    r_min = re.search(r'\b(?:rating|rated)?\s*(?:more than|greater than|above|over|higher than|>|>=|at least)\s*(\d+(?:\.\d+)?)', t)
    if r_min:
        min_rating = float(r_min.group(1))
    r_max = re.search(r'\b(?:rating|rated)?\s*(?:less than|lower than|below|under|<|<=)\s*(\d+(?:\.\d+)?)', t)
    if r_max and not any(unit in t for unit in ['hour', 'hr', 'min']):
        max_rating = float(r_max.group(1))

    # 2. Count / Quantity (detects numerals, singular phrasing, and imperative numbers)
    count = None
    m_num = re.search(r'\b(\d+)\s+(?:[\w-]+\s+)*(?:movies?|films?|shows?|picks?|titles?)\b', t)
    if m_num:
        count = int(m_num.group(1))
    elif re.search(r'\b(?:a|an|one|single)\s+(?:[\w-]+\s+)*(?:movie|film|show|series|title|pick|recommendation)\b', t):
        count = 1
    elif re.search(r'\b(?:any\s+one|just\s+one|only\s+one|single|a\s+single)\b', t):
        count = 1
    else:
        c_match = re.search(r'\b(?:top|best|show|give me|find|recommend|suggest)\s*(\d+)\b', t)
        if c_match:
            count = int(c_match.group(1))
        else:
            words = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
            w_match = re.search(r'\b(one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:[\w-]+\s+)*(?:movies?|films?|shows?|picks?|titles?)', t)
            if w_match:
                count = words[w_match.group(1)]

    # 3. Duration
    max_duration = None
    d_hr = re.search(r'\b(?:under|less than|shorter than)\s*(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b', t)
    if d_hr:
        max_duration = int(float(d_hr.group(1)) * 60)
    d_min = re.search(r'\b(?:under|less than|shorter than)\s*(\d+)\s*(?:mins?|minutes?)\b', t)
    if d_min:
        max_duration = int(d_min.group(1))

    # 4. Year
    min_year = None
    max_year = None
    y_after = re.search(r'\b(?:after|since|post|from)\s*(\d{4})\b', t)
    if y_after:
        min_year = int(y_after.group(1))
    y_before = re.search(r'\b(?:before|prior to)\s*(\d{4})\b', t)
    if y_before:
        max_year = int(y_before.group(1))

    # 5. Language Detection
    language = None
    for lang in LANGUAGE_ALIAS_MAP.keys():
        if re.search(r'\b' + re.escape(lang) + r'\b', t):
            language = lang
            break

    # 6. Random selection indicator
    is_random = bool(re.search(r'\b(?:random|spontaneous|any|surprise)\b', t))

    return {
        "min_rating": min_rating,
        "max_rating": max_rating,
        "count": count,
        "max_duration": max_duration,
        "min_year": min_year,
        "max_year": max_year,
        "language": language,
        "is_random": is_random
    }


def filter_by_constraints(items: List[Any], constraints: Dict[str, Any]) -> List[Any]:
    """Applies rating, duration, and year filters to candidate content items."""
    filtered = items
    min_r = constraints.get("min_rating")
    if min_r is not None:
        filtered = [i for i in filtered if (getattr(i, "average_rating", None) or 0) >= min_r]

    max_r = constraints.get("max_rating")
    if max_r is not None:
        filtered = [i for i in filtered if (getattr(i, "average_rating", None) or 0) <= max_r]

    max_d = constraints.get("max_duration")
    if max_d is not None:
        filtered = [i for i in filtered if getattr(i, "duration", None) and i.duration <= max_d]

    min_y = constraints.get("min_year")
    if min_y is not None:
        filtered = [i for i in filtered if (getattr(i, "release_year", None) or 0) >= min_y]

    max_y = constraints.get("max_year")
    if max_y is not None:
        filtered = [i for i in filtered if (getattr(i, "release_year", None) or 0) <= max_y]

    # When min_rating is specified, order by rating descending
    if min_r is not None:
        filtered.sort(key=lambda x: getattr(x, "average_rating", 0) or 0, reverse=True)

    return filtered


def tool_search_catalog(
    query_text: str,
    db: Session,
    genre: Optional[str] = None,
    limit: int = 8,
    constraints: Optional[Dict[str, Any]] = None
) -> List[Any]:
    """
    Intelligent search tool:
    1. Checks character/actor knowledge base
    2. Checks cinematic universes
    3. Respects language constraints (e.g. Tamil, Hindi, Korean)
    4. Handles spontaneous / random sampling requests
    5. Executes multi-token weighted fuzzy search
    6. Applies high-quality fallback filtering (no obscure 1-vote movies)
    7. Honors count and numeric filters
    """
    clean_q = query_text.strip().lower()
    if constraints is None:
        constraints = parse_user_query_constraints(clean_q)

    effective_limit = constraints.get("count") or limit
    req_lang = constraints.get("language")
    is_random = constraints.get("is_random", False)

    # Prepare language filter expressions if a specific language is requested
    m_lang_filter = None
    s_lang_filter = None
    if req_lang:
        aliases = LANGUAGE_ALIAS_MAP.get(req_lang, [req_lang])
        m_lang_filter = build_language_filter(Movie.language, aliases)
        s_lang_filter = build_language_filter(WebSeries.language, aliases)

    # 1. Check Character Match
    char_match = match_character_in_query(clean_q)
    if char_match:
        found_char_movies: List[Movie] = []
        for t_name, yr, _ in char_match["titles"]:
            m = db.query(Movie).options(joinedload(Movie.director)).filter(
                Movie.title.ilike(f"%{t_name}%"),
                Movie.release_year == yr
            ).first()
            if not m:
                # Fallback to title match without year constraint
                m = db.query(Movie).options(joinedload(Movie.director)).filter(
                    Movie.title.ilike(f"%{t_name}%")
                ).first()
            if m and m not in found_char_movies:
                found_char_movies.append(m)
        if found_char_movies:
            filtered = filter_by_constraints(found_char_movies, constraints)
            return filtered[:effective_limit] if filtered else found_char_movies[:effective_limit]

    # 2. Check Universe Match
    universe_match = match_universe_in_query(clean_q, db)
    if universe_match and universe_match.contents:
        c_ids = [c.content_id for c in universe_match.contents]
        u_movies = db.query(Movie).options(joinedload(Movie.director)).filter(
            Movie.id.in_(c_ids)
        ).order_by(Movie.release_year.asc().nullslast()).all()
        if u_movies:
            filtered = filter_by_constraints(u_movies, constraints)
            return filtered[:effective_limit] if filtered else u_movies[:effective_limit]

    # 2.5 Spontaneous / Random selection request (e.g. 'random tamil movie', 'pick a random movie')
    if is_random:
        q_m = db.query(Movie).options(joinedload(Movie.director)).filter(
            Movie.poster_image_url.isnot(None),
            Movie.average_rating >= 6.5
        )
        if m_lang_filter is not None:
            q_m = q_m.filter(m_lang_filter)
        all_random_pool = filter_by_constraints(q_m.order_by(Movie.average_rating.desc()).limit(40).all(), constraints)
        if all_random_pool:
            import random
            return random.sample(all_random_pool, min(len(all_random_pool), effective_limit))

    # 3. Multi-Token Keyword & Semantic Search across Movies & WebSeries
    tokens = [t for t in re.findall(r"\b\w+\b", clean_q) if len(t) >= 3 and t not in STOP_WORDS]
    if tokens:
        m_filters = []
        s_filters = []
        for t in tokens:
            m_filters.append(Movie.title.ilike(f"%{t}%"))
            m_filters.append(Movie.description.ilike(f"%{t}%"))
            m_filters.append(Movie.genre.ilike(f"%{t}%"))
            m_filters.append(Movie.director.has(Director.name.ilike(f"%{t}%")))

            s_filters.append(WebSeries.title.ilike(f"%{t}%"))
            s_filters.append(WebSeries.description.ilike(f"%{t}%"))
            s_filters.append(WebSeries.genre.ilike(f"%{t}%"))
            s_filters.append(WebSeries.director.has(Director.name.ilike(f"%{t}%")))

        q_movies = db.query(Movie).options(joinedload(Movie.director)).filter(or_(*m_filters))
        if m_lang_filter is not None:
            q_movies = q_movies.filter(m_lang_filter)
        candidate_movies = filter_by_constraints(q_movies.all(), constraints)

        q_series = db.query(WebSeries).options(
            joinedload(WebSeries.director),
            joinedload(WebSeries.seasons)
        ).filter(or_(*s_filters))
        if s_lang_filter is not None:
            q_series = q_series.filter(s_lang_filter)
        candidate_series = filter_by_constraints(q_series.all(), constraints)

        all_candidates = candidate_movies + candidate_series
        if all_candidates:
            scored_candidates: List[Tuple[float, Any]] = []
            for m in all_candidates:
                score = 0.0
                t_lower = m.title.lower()
                d_lower = (m.description or "").lower()
                g_lower = (m.genre or "").lower()
                dir_name = (m.director.name if m.director else "").lower()
                tokens_matched = 0

                for t in tokens:
                    matched_token = False
                    if t in dir_name:
                        score += 25.0
                        matched_token = True
                    if t in t_lower:
                        score += 15.0
                        matched_token = True
                    if t in d_lower:
                        score += 6.0
                        matched_token = True
                    if t in g_lower:
                        score += 8.0
                        matched_token = True
                    if matched_token:
                        tokens_matched += 1

                if tokens_matched > 1:
                    score += tokens_matched * 25.0

                rating = m.average_rating or 7.0
                score += min(10.0, rating)
                scored_candidates.append((score, m))

            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            results = [c for _, c in scored_candidates[:effective_limit]]
            if results:
                return results

    # 4. Qualified High-Acclaim Fallback (Filters out obscure 1-vote movies)
    fallback_q = db.query(Movie).options(joinedload(Movie.director)).filter(
        Movie.average_rating >= 6.5,
        Movie.poster_image_url.isnot(None)
    )
    if m_lang_filter is not None:
        fallback_q = fallback_q.filter(m_lang_filter)
    else:
        fallback_q = fallback_q.filter(Movie.duration >= 75, Movie.release_year >= 1995)

    fallback_movies = fallback_q.order_by(Movie.average_rating.desc().nullslast()).all()
    filtered_fallback = filter_by_constraints(fallback_movies, constraints)
    final_pool = filtered_fallback or fallback_movies
    if is_random and final_pool:
        import random
        return random.sample(final_pool, min(len(final_pool), effective_limit))
    return final_pool[:effective_limit]


def tool_execute_watchlist_action(
    user_id: int,
    action: str,  # 'add' or 'remove'
    target_title_or_id: str,
    db: Session
) -> Optional[AgentActionResult]:
    """Autonomous action tool to add or remove items from the user's watchlist."""
    clean_target = target_title_or_id.strip()

    # Try exact or ID match
    movie = db.query(Movie).filter(
        or_(Movie.id == clean_target, Movie.title.ilike(clean_target))
    ).first()

    series = None
    if not movie:
        series = db.query(WebSeries).filter(
            or_(WebSeries.series_id == clean_target, WebSeries.title.ilike(clean_target))
        ).first()

    target_item = movie or series
    if not target_item:
        return None

    cid = movie.id if movie else series.series_id
    title = movie.title if movie else series.title
    c_type = "movie" if movie else "web_series"

    if action == "add":
        existing = db.query(WatchlistItem).filter(
            WatchlistItem.user_id == user_id,
            WatchlistItem.content_id == cid
        ).first()
        if not existing:
            new_item = WatchlistItem(
                user_id=user_id,
                content_id=cid,
                content_type=c_type,
                status="to_watch"
            )
            db.add(new_item)
            db.commit()
            return AgentActionResult(
                action="added_to_watchlist",
                content_id=cid,
                title=title,
                status="success",
                detail=f"Added '{title}' to your Watchlist."
            )
        return AgentActionResult(
            action="already_in_watchlist",
            content_id=cid,
            title=title,
            status="info",
            detail=f"'{title}' is already in your Watchlist."
        )

    elif action == "remove":
        deleted = db.query(WatchlistItem).filter(
            WatchlistItem.user_id == user_id,
            WatchlistItem.content_id == cid
        ).delete()
        db.commit()
        if deleted:
            return AgentActionResult(
                action="removed_from_watchlist",
                content_id=cid,
                title=title,
                status="success",
                detail=f"Removed '{title}' from your Watchlist."
            )
        return AgentActionResult(
            action="not_in_watchlist",
            content_id=cid,
            title=title,
            status="info",
            detail=f"'{title}' was not found in your Watchlist."
        )

    return None


# =========================================================================
# 4. Multi-Turn Conversational Agent Concierge
# =========================================================================

def run_agent_concierge_chat(
    user: Optional[User],
    message: str,
    conversation_id: Optional[str],
    db: Session
) -> AgentChatResponse:
    """
    Autonomous PrimeFlix AI Entertainment Concierge.
    Reasons over user intent, parses filters, resolves characters & franchises,
    executes actions, and produces rich, accurate recommendations.
    """
    user_id = user.id if user else None
    user_msg = message.strip()
    msg_lower = user_msg.lower()

    # 1. Setup or retrieve conversation thread
    conv_id = conversation_id.strip() if conversation_id and conversation_id.strip() else f"conv-{uuid.uuid4().hex[:12]}"
    conversation = db.query(AgentConversation).filter(AgentConversation.id == conv_id).first()
    if not conversation:
        conversation = AgentConversation(
            id=conv_id,
            user_id=user_id,
            title=user_msg[:60]
        )
        db.add(conversation)
        db.commit()

    # 2. Compile user taste profile & parse explicit constraints
    taste = get_user_taste_profile(user_id, db)
    constraints = parse_user_query_constraints(user_msg)

    # 3. Detect Actionable Intents (Watchlist Add / Remove)
    actions_taken: List[AgentActionResult] = []
    add_match = re.search(r"\b(?:add|put|save)\s+['\"]?([^'\"\n]+?)['\"]?\s+(?:to|in)\s+(?:my\s+)?watchlist\b", user_msg, re.IGNORECASE)
    remove_match = re.search(r"\b(?:remove|delete|drop)\s+['\"]?([^'\"\n]+?)['\"]?\s+from\s+(?:my\s+)?watchlist\b", user_msg, re.IGNORECASE)

    if add_match:
        target_title = add_match.group(1).strip()
        if user_id:
            res = tool_execute_watchlist_action(user_id, "add", target_title, db)
            if res:
                actions_taken.append(res)
        else:
            actions_taken.append(AgentActionResult(
                action="login_required",
                content_id="",
                title=target_title,
                status="info",
                detail=f"Please log in so I can save '{target_title}' directly to your personal Watchlist!"
            ))
    elif remove_match:
        target_title = remove_match.group(1).strip()
        if user_id:
            res = tool_execute_watchlist_action(user_id, "remove", target_title, db)
            if res:
                actions_taken.append(res)
        else:
            actions_taken.append(AgentActionResult(
                action="login_required",
                content_id="",
                title=target_title,
                status="info",
                detail="Please log in to manage your Watchlist."
            ))

    # 4. Check for Sequential / Continuation / Sequel Intent FIRST
    continuation_match = resolve_continuation_intent(user_msg, db)

    # 4.2 Check for Specific Episode Inquiry Intent
    episode_match = resolve_episode_intent(user_msg, db) if not continuation_match else None

    # 4.3 Check for Web Series / Season Metadata Intent
    series_metadata_match = resolve_series_metadata_intent(user_msg, db) if not continuation_match and not episode_match else None

    # 4.6 Check for Director Specific Intent
    director_match = resolve_director_intent(user_msg, db) if not continuation_match and not series_metadata_match and not episode_match else None

    # 5. Check for Entity or Character Specific Match
    char_match = match_character_in_query(msg_lower)
    universe_match = match_universe_in_query(msg_lower, db)

    candidate_cards: List[RecommendedItem] = []
    agent_text = ""
    suggested_replies = [
        "Tell me more about this pick",
        "Add this to my Watchlist",
        "Show in chronological release order",
        "Find next franchise release"
    ]

    action_prefix = ""
    if actions_taken:
        action_prefix = " ".join([f"✨ **{a.detail}**" for a in actions_taken]) + "\n\n"

    # =========================================================================
    # INTENT BRANCH 1: Continuation / Sequel / "What to watch after X"
    # =========================================================================
    if continuation_match:
        curr_m = continuation_match["current"]
        next_m = continuation_match.get("direct_next")
        subsequent = continuation_match.get("subsequent", [])
        u_name = continuation_match.get("universe_name")
        c_type = continuation_match["type"]

        if c_type == "direct_sequel" and next_m:
            candidate_cards.append(
                RecommendedItem(
                    content=movie_to_movie_response(next_m),
                    recommendation_reason=f"Direct official sequel to '{curr_m.title}' ({curr_m.release_year or 'N/A'}).",
                    recommendation_type="agent_curated",
                    match_score=99,
                    genre_match_breakdown=getattr(next_m, "genre_percentages", None)
                )
            )
            for idx, sm in enumerate(subsequent[:7], 2):
                candidate_cards.append(
                    RecommendedItem(
                        content=movie_to_movie_response(sm),
                        recommendation_reason=f"Next in viewing sequence (Part {idx + (continuation_match.get('universe_order') or 1)}).",
                        recommendation_type="agent_curated",
                        match_score=max(70, 96 - idx * 3),
                        genre_match_breakdown=getattr(sm, "genre_percentages", None)
                    )
                )

            u_desc = f" in the **{u_name}**" if u_name else ""
            desc_snippet = f"\n*{next_m.description[:240]}...*" if next_m.description else ""
            subsequent_text = ""
            if subsequent:
                sub_lines = []
                start_num = (continuation_match.get("universe_order") or 1) + 2
                for idx, sm in enumerate(subsequent[:6]):
                    num = start_num + idx
                    sub_lines.append(f"{num}. 🎬 **{sm.title}** ({sm.release_year or 'N/A'}) — ⭐ **{sm.average_rating or 7.5:.1f}/10**")
                subsequent_text = "\n\n### 📜 Subsequent Viewing Order:\n" + "\n".join(sub_lines)

            agent_text = (
                f"{action_prefix}The direct next movie to watch after **{curr_m.title}** ({curr_m.release_year or 'N/A'}){u_desc} is:\n\n"
                f"⚡ 🎬 **{next_m.title}** ({next_m.release_year or 'N/A'}) — ⭐ **{next_m.average_rating or 7.5:.1f}/10**\n{desc_snippet}"
                f"{subsequent_text}\n\n"
                f"I've attached **{next_m.title}** and the following chapters in your cards below so you can stream them directly or add them to your Watchlist!"
            )
            suggested_replies = [
                f"Add {next_m.title} to Watchlist",
                f"Tell me more about {next_m.title}",
                "Show full franchise viewing order",
                "Recommend similar universes"
            ]
        elif c_type == "universe_finale":
            other_candidates = db.query(Movie).options(joinedload(Movie.director)).filter(
                Movie.id != curr_m.id,
                Movie.average_rating >= 7.6
            ).limit(6).all()
            for sm in other_candidates:
                candidate_cards.append(
                    RecommendedItem(
                        content=movie_to_movie_response(sm),
                        recommendation_reason=f"Recommended after completing {u_name or curr_m.title}.",
                        recommendation_type="agent_curated",
                        match_score=88,
                        genre_match_breakdown=getattr(sm, "genre_percentages", None)
                    )
                )
            agent_text = (
                f"{action_prefix}**{curr_m.title}** ({curr_m.release_year or 'N/A'}) is the grand finale of the mainline **{u_name or 'franchise'}** saga!\n\n"
                f"Since you've completed this storyline, I've curated top spin-offs, prequels, and related cinematic sagas below to continue your journey."
            )
            suggested_replies = [
                "Explore similar universes",
                "Show top-rated fantasy films",
                "Back to trending movies"
            ]
        else:  # standalone
            director_films = []
            if curr_m.director_id:
                director_films = db.query(Movie).options(joinedload(Movie.director)).filter(
                    Movie.director_id == curr_m.director_id,
                    Movie.id != curr_m.id
                ).limit(4).all()
            genre_films = db.query(Movie).options(joinedload(Movie.director)).filter(
                Movie.id != curr_m.id,
                Movie.average_rating >= 7.8
            ).limit(4).all()
            for sm in (director_films + genre_films)[:6]:
                candidate_cards.append(
                    RecommendedItem(
                        content=movie_to_movie_response(sm),
                        recommendation_reason=f"Acclaimed work sharing thematic style with {curr_m.title}.",
                        recommendation_type="agent_curated",
                        match_score=85,
                        genre_match_breakdown=getattr(sm, "genre_percentages", None)
                    )
                )
            agent_text = (
                f"{action_prefix}**{curr_m.title}** ({curr_m.release_year or 'N/A'}) is an acclaimed standalone film with no direct sequels.\n\n"
                f"To keep the momentum going, I've curated top films matching its style, atmosphere, and cinematic calibre in your cards below!"
            )
            suggested_replies = [
                f"Tell me more about {candidate_cards[0].content.title}" if candidate_cards else "Top sci-fi movies",
                "Show similar directors",
                "Add first pick to Watchlist"
            ]

    # =========================================================================
    # INTENT BRANCH 1.2: Specific Episode & Chapter Inquiries
    # =========================================================================
    elif episode_match:
        series = episode_match["series"]
        season = episode_match["season"]
        ep = episode_match["episode"]
        overall_num = episode_match["overall_episode_number"]
        s_num = episode_match["season_number"]
        ep_num = episode_match["local_episode_number"]
        resp_obj = series_to_movie_response(series)

        candidate_cards.append(
            RecommendedItem(
                content=resp_obj,
                recommendation_reason=f"Featuring Episode {overall_num or ep_num} ('{ep.title}') in Season {s_num}.",
                recommendation_type="agent_curated",
                match_score=99,
                genre_match_breakdown=getattr(series, "genre_percentages", None)
            )
        )

        overall_str = f" (Overall Episode **{overall_num}**)" if overall_num and overall_num != ep_num else ""
        air_date_val = getattr(ep, "release_date", None)
        air_date_str = ""
        if air_date_val:
            if hasattr(air_date_val, "strftime"):
                air_date_str = f"\n• 📅 **Air Date:** {air_date_val.strftime('%B %d, %Y')}"
            else:
                air_date_str = f"\n• 📅 **Air Date:** {air_date_val}"
        duration_str = f"\n• ⏱️ **Runtime:** {ep.duration} mins" if ep.duration else ""
        rating_str = f"\n• ⭐ **Rating:** {ep.average_rating:.1f}/10" if ep.average_rating else ""
        desc_str = f"\n\n### 📖 Episode Synopsis:\n*{ep.description}*" if ep.description else ""

        agent_text = (
            f"{action_prefix}The name of **Episode {overall_num or ep_num}** in **{series.title}** is **\"{ep.title}\"**:\n\n"
            f"• 📺 **Season & Episode:** Season {s_num}, Episode {ep_num}{overall_str}"
            f"{rating_str}{duration_str}{air_date_str}"
            f"{desc_str}\n\n"
            f"I've attached the **{series.title}** series card below so you can stream this episode directly, explore the season, or add it to your Watchlist!"
        )
        suggested_replies = [
            f"Add {series.title} to Watchlist",
            f"What happens in Episode {overall_num + 1}?" if overall_num else f"Tell me about Season {s_num}",
            f"How many episodes in Season {s_num}?",
            "Tell me about the series lore"
        ]

    # =========================================================================
    # INTENT BRANCH 1.3: Web Series, Anime & Season Metadata Inquiries
    # =========================================================================
    elif series_metadata_match:
        series = series_metadata_match["series"]
        seasons_count = series_metadata_match["seasons_count"]
        total_episodes = series_metadata_match["total_episodes"]
        seasons_list = series_metadata_match["seasons_list"]
        resp_obj = series_to_movie_response(series)

        candidate_cards.append(
            RecommendedItem(
                content=resp_obj,
                recommendation_reason=f"Complete series overview ({seasons_count} Seasons, {total_episodes} Episodes).",
                recommendation_type="agent_curated",
                match_score=99,
                genre_match_breakdown=getattr(series, "genre_percentages", None)
            )
        )

        seasons_bullets = "\n".join([
            f"• **{s.title or f'Season {s.season_number}'}** ({s.release_year or 'N/A'}) — {s.episode_count or len(s.episodes) or 0} episodes"
            for s in seasons_list
        ]) if seasons_list else f"• Total Seasons: **{seasons_count}**\n• Total Episodes: **{total_episodes}**"

        desc_snippet = f"\n\n*{series.description[:240]}...*" if series.description else ""

        agent_text = (
            f"{action_prefix}**{series.title}** has **{seasons_count} seasons** comprising a total of **{total_episodes} episodes**:\n\n"
            f"{seasons_bullets}\n\n"
            f"⭐ Overall Rating: **{series.average_rating or 8.5:.1f}/10** | Genre: {series.genre or 'Series'}{desc_snippet}\n\n"
            f"I've attached the complete **{series.title}** series card below so you can explore episodes, jump directly to streaming, or add it to your Watchlist!"
        )
        suggested_replies = [
            f"Add {series.title} to Watchlist",
            f"Tell me about Season 1",
            "Show similar anime series" if "animation" in (series.genre or "").lower() else "Recommend similar shows",
            "Show top-rated web series"
        ]

    # =========================================================================
    # INTENT BRANCH 1.5: Director Inquiries (First/Debut, Latest, Best, Filmography)
    # =========================================================================
    elif director_match:
        director = director_match["director"]
        d_name = director.name
        mvs = director_match["movies"]
        itype = director_match["intent_type"]

        if itype == "first":
            primary_m = mvs[0]
            other_mvs = mvs[1:]
            candidate_cards.append(
                RecommendedItem(
                    content=movie_to_movie_response(primary_m),
                    recommendation_reason=f"Debut feature film directed by {d_name}.",
                    recommendation_type="agent_curated",
                    match_score=99,
                    genre_match_breakdown=getattr(primary_m, "genre_percentages", None)
                )
            )
            for idx, om in enumerate(other_mvs[:7], 2):
                candidate_cards.append(
                    RecommendedItem(
                        content=movie_to_movie_response(om),
                        recommendation_reason=f"Subsequent directorial release ({om.release_year or 'N/A'}).",
                        recommendation_type="agent_curated",
                        match_score=max(70, 96 - idx * 3),
                        genre_match_breakdown=getattr(om, "genre_percentages", None)
                    )
                )

            desc_snippet = f"\n*{primary_m.description[:240]}...*" if primary_m.description else ""
            subsequent_lines = "\n".join([f"{idx+1}. 🎬 **{m.title}** ({m.release_year or 'N/A'}) — ⭐ **{m.average_rating or 7.5:.1f}/10**" for idx, m in enumerate(other_mvs[:5], 1)])
            subsequent_section = f"\n\n### 📽️ Subsequent Directorial Works by {d_name}:\n{subsequent_lines}" if other_mvs else ""

            agent_text = (
                f"{action_prefix}The first (debut) feature film directed by **{d_name}** is:\n\n"
                f"🎬 **{primary_m.title}** ({primary_m.release_year or 'N/A'}) — ⭐ **{primary_m.average_rating or 7.5:.1f}/10**\n{desc_snippet}"
                f"{subsequent_section}\n\n"
                f"I've attached **{primary_m.title}** and his complete filmography in your cards below so you can stream them directly or add them to your Watchlist!"
            )
            suggested_replies = [
                f"Add {primary_m.title} to Watchlist",
                f"Tell me more about {primary_m.title}",
                f"Show all movies by {d_name}",
                "Explore LCU movies" if "lokesh" in d_name.lower() else "Recommend similar directors"
            ]

        elif itype == "latest":
            dated_mvs = [m for m in mvs if m.release_year]
            primary_m = dated_mvs[-1] if dated_mvs else mvs[-1]
            earlier_mvs = [m for m in mvs if m.id != primary_m.id]
            earlier_mvs.sort(key=lambda x: -(x.release_year or 0))

            candidate_cards.append(
                RecommendedItem(
                    content=movie_to_movie_response(primary_m),
                    recommendation_reason=f"Latest directorial release by {d_name}.",
                    recommendation_type="agent_curated",
                    match_score=99,
                    genre_match_breakdown=getattr(primary_m, "genre_percentages", None)
                )
            )
            for idx, em in enumerate(earlier_mvs[:6], 2):
                candidate_cards.append(
                    RecommendedItem(
                        content=movie_to_movie_response(em),
                        recommendation_reason=f"Previous acclaimed release ({em.release_year or 'N/A'}).",
                        recommendation_type="agent_curated",
                        match_score=max(70, 96 - idx * 3),
                        genre_match_breakdown=getattr(em, "genre_percentages", None)
                    )
                )

            desc_snippet = f"\n*{primary_m.description[:240]}...*" if primary_m.description else ""
            agent_text = (
                f"{action_prefix}The latest directorial release by **{d_name}** is:\n\n"
                f"🎬 **{primary_m.title}** ({primary_m.release_year or 'N/A'}) — ⭐ **{primary_m.average_rating or 7.5:.1f}/10**\n{desc_snippet}\n\n"
                f"I've attached **{primary_m.title}** and his celebrated filmography below so you can check details or start watching!"
            )
            suggested_replies = [
                f"Add {primary_m.title} to Watchlist",
                f"Show all movies by {d_name}",
                "Explore LCU movies" if "lokesh" in d_name.lower() else "Recommend similar directors"
            ]

        elif itype == "best":
            sorted_by_rating = sorted(mvs, key=lambda x: -(x.average_rating or 0))
            primary_m = sorted_by_rating[0]
            candidate_cards.append(
                RecommendedItem(
                    content=movie_to_movie_response(primary_m),
                    recommendation_reason=f"Highest rated film directed by {d_name} ({primary_m.average_rating:.1f}/10).",
                    recommendation_type="agent_curated",
                    match_score=99,
                    genre_match_breakdown=getattr(primary_m, "genre_percentages", None)
                )
            )
            for idx, sm in enumerate(sorted_by_rating[1:7], 2):
                candidate_cards.append(
                    RecommendedItem(
                        content=movie_to_movie_response(sm),
                        recommendation_reason=f"Rated {sm.average_rating:.1f}/10 directed by {d_name}.",
                        recommendation_type="agent_curated",
                        match_score=max(70, 96 - idx * 3),
                        genre_match_breakdown=getattr(sm, "genre_percentages", None)
                    )
                )

            desc_snippet = f"\n*{primary_m.description[:240]}...*" if primary_m.description else ""
            agent_text = (
                f"{action_prefix}The highest-rated film directed by **{d_name}** is:\n\n"
                f"⭐ 🎬 **{primary_m.title}** ({primary_m.release_year or 'N/A'}) — ⭐ **{primary_m.average_rating:.1f}/10**\n{desc_snippet}\n\n"
                f"I've ranked his top-rated works in your cards below!"
            )
            suggested_replies = [
                f"Add {primary_m.title} to Watchlist",
                f"Show in chronological order",
                "Recommend similar directors"
            ]

        else:  # all / general filmography
            for idx, m in enumerate(mvs):
                candidate_cards.append(
                    RecommendedItem(
                        content=movie_to_movie_response(m),
                        recommendation_reason=f"Directed by {d_name} ({m.release_year or 'N/A'}).",
                        recommendation_type="agent_curated",
                        match_score=max(70, 98 - idx * 2),
                        genre_match_breakdown=getattr(m, "genre_percentages", None)
                    )
                )

            bullets = "\n".join([f"{idx}. 🎬 **{m.title}** ({m.release_year or 'N/A'}) — ⭐ **{m.average_rating or 7.5:.1f}/10**" for idx, m in enumerate(mvs, 1)])
            agent_text = (
                f"{action_prefix}Here is the complete filmography of **{d_name}** available in our streaming catalog:\n\n"
                f"{bullets}\n\n"
                f"I've populated all {len(mvs)} titles in your cards below so you can explore details or start watching!"
            )
            suggested_replies = [
                f"Add {mvs[0].title} to Watchlist",
                f"Which one is the best?",
                "Explore LCU movies" if "lokesh" in d_name.lower() else "Recommend similar directors"
            ]

    # =========================================================================
    # INTENT BRANCH 2: General Catalog Search, Characters, Universes, Constraints
    # =========================================================================
    if not candidate_cards and not agent_text:
        req_count = constraints.get("count")
        search_limit = req_count if req_count else 4
        catalog_candidates = tool_search_catalog(user_msg, db, limit=search_limit, constraints=constraints)

        for idx, c in enumerate(catalog_candidates):
            is_movie = isinstance(c, Movie)
            resp_obj = movie_to_movie_response(c) if is_movie else series_to_movie_response(c)
            genre_str = getattr(c, "genre", "Cinema") or "Cinema"
            score = max(70, 98 - (idx * 2))

            reason = f"Curated for you based on '{genre_str}' and critical acclaim ({resp_obj.average_rating or 8.0}/10)."
            if char_match:
                sorted_char_titles = sorted(char_match.get("titles", []), key=lambda x: len(x[0]), reverse=True)
                for t_title, _, blurb in sorted_char_titles:
                    if t_title.lower() in resp_obj.title.lower():
                        reason = blurb
                        break
            elif taste.top_genres and any(g in genre_str for g in taste.top_genres.keys()):
                top_g = next(g for g in taste.top_genres.keys() if g in genre_str)
                reason = f"Matches your affinity for {top_g} ({taste.top_genres[top_g]}% of your watch patterns)."

            candidate_cards.append(
                RecommendedItem(
                    content=resp_obj,
                    recommendation_reason=reason,
                    recommendation_type="agent_curated",
                    match_score=score,
                    genre_match_breakdown=getattr(c, "genre_percentages", None)
                )
            )

    # CASE A: Character Specific Match (e.g. Tony Stark / Iron Man)
    if not agent_text and char_match:
        c_name = char_match["character_name"]
        actor = char_match.get("actor", "")
        u_name = char_match.get("universe_name", "Cinema Universe")
        
        # If user specified a rating or count constraint
        if constraints.get("min_rating") or constraints.get("count"):
            if len(candidate_cards) == 1:
                rating_phrase = f" with a rating higher than **{constraints['min_rating']}**" if constraints.get("min_rating") else ""
                agent_text = (
                    f"{action_prefix}Here is a top-rated movie featuring **{c_name}** ({actor}){rating_phrase}:\n\n"
                    f"🎬 **{candidate_cards[0].content.title}** ({candidate_cards[0].content.release_year or 'N/A'}) — ⭐ **{candidate_cards[0].content.average_rating:.1f}/10**\n\n"
                    f"{candidate_cards[0].recommendation_reason}\n\n"
                    f"I've attached its movie card below so you can check details or start watching!"
                )
            else:
                rating_phrase = f" with ratings higher than **{constraints['min_rating']}**" if constraints.get("min_rating") else ""
                agent_text = (
                    f"{action_prefix}Here are {len(candidate_cards)} standout movies featuring **{c_name}** ({actor}){rating_phrase}:\n\n"
                    + "\n".join([f"{idx+1}. 🎬 **{c.content.title}** ({c.content.release_year or 'N/A'}) — ⭐ **{c.content.average_rating:.1f}/10**\n   {c.recommendation_reason}" for idx, c in enumerate(candidate_cards)])
                    + "\n\nI've populated these selections in your cards below!"
                )
        else:
            # Full chronological overview
            movie_bullets = []
            for idx, (t_name, yr, blurb) in enumerate(char_match["titles"], 1):
                movie_bullets.append(f"{idx}. 🎬 **{t_name} ({yr})** — {blurb}")

            agent_text = (
                f"{action_prefix}Here is the complete chronological list of all **{u_name}** movies featuring **{c_name}** ({actor}):\n\n"
                + "\n".join(movie_bullets)
                + "\n\nI've populated all the matching titles in your cards below so you can check details or add them directly to your Watchlist!"
            )

        suggested_replies = [
            f"Add {candidate_cards[0].content.title if candidate_cards else 'first'} to Watchlist",
            "What about Captain America?",
            "Show MCU Phase 1",
            "Where does Spider-Man appear?"
        ]

    # CASE B: Universe / Franchise Match
    elif not agent_text and universe_match:
        u_name = universe_match.name
        
        # If user asked for a single movie or a rating constraint
        if constraints.get("min_rating") or constraints.get("count"):
            if len(candidate_cards) == 1:
                rating_phrase = f" with a rating higher than **{constraints['min_rating']}**" if constraints.get("min_rating") else ""
                agent_text = (
                    f"{action_prefix}Here is a standout movie from the **{u_name}**{rating_phrase}:\n\n"
                    f"🎬 **{candidate_cards[0].content.title}** ({candidate_cards[0].content.release_year or 'N/A'}) — ⭐ **{candidate_cards[0].content.average_rating:.1f}/10**\n\n"
                    f"{candidate_cards[0].recommendation_reason}\n\n"
                    f"I've attached its movie card below so you can inspect details or add it directly to your Watchlist!"
                )
            else:
                rating_phrase = f" with ratings higher than **{constraints['min_rating']}**" if constraints.get("min_rating") else ""
                agent_text = (
                    f"{action_prefix}Here are {len(candidate_cards)} top-rated titles from the **{u_name}**{rating_phrase}:\n\n"
                    + "\n".join([f"• 🎬 **{c.content.title}** ({c.content.release_year or 'N/A'}) — ⭐ **{c.content.average_rating:.1f}/10**" for c in candidate_cards])
                    + "\n\nI've populated these selections in your cards below!"
                )
        else:
            agent_text = (
                f"{action_prefix}Here are the key titles from the **{u_name}** available in our streaming catalog:\n\n"
                + "\n".join([f"• **{c.content.title}** ({c.content.release_year or 'N/A'}) — ⭐ {c.content.average_rating or 7.5}/10" for c in candidate_cards[:8]])
                + "\n\nFeel free to explore any title or ask for viewing orders and lore connections!"
            )

        suggested_replies = [
            f"Add {candidate_cards[0].content.title if candidate_cards else 'first'} to Watchlist",
            "Show in chronological order",
            "Find prequel or sequel",
            "Recommend similar universes"
        ]

    # CASE C: Gemini LLM Enhancement (if valid key)
    gemini_key = getattr(settings, "GEMINI_API_KEY", "").strip()
    is_knowledge = is_cinema_knowledge_query(user_msg)

    if not agent_text and gemini_key and len(gemini_key) > 20:
        try:
            cand_summary = [
                {
                    "title": c.content.title,
                    "id": c.content.id,
                    "rating": c.content.average_rating,
                    "genre": c.content.genre,
                    "description": c.content.description[:120] if c.content.description else ""
                }
                for c in candidate_cards[:5]
            ]
            action_summary = [a.detail for a in actions_taken]

            if is_knowledge:
                sys_prompt = (
                    "You are PrimeFlix AI — an elite, authoritative cinema, anime & TV series knowledge assistant and entertainment concierge. "
                    "When the user asks factual cinema, anime, or series questions (such as episode names, plot points, trivia, cast, lore, chronology, release dates, or directing details):\n"
                    "1. Directly and accurately answer their specific question first with rich, engaging cinema knowledge.\n"
                    "2. Use clean, stylish markdown formatting (bolding, headers, bullet points).\n"
                    "3. Do NOT recommend random, unrelated movies when the user is asking a factual knowledge question.\n"
                    "4. If relevant, conclude with a brief offer to explore related lore or titles."
                )
            else:
                req_num_phrase = f"Spotlight and review EXACTLY {constraints['count']} title(s)." if constraints.get("count") else "Review the top matching titles."
                sys_prompt = (
                    "You are the PrimeFlix AI Entertainment Concierge — an elite, insightful cinema & series companion. "
                    "Directly answer user queries with accurate, stylish, and engaging cinema recommendations and taste matching.\n"
                    "CRITICAL INSTRUCTIONS:\n"
                    "1. If Candidate Titles from our database are provided below, you MUST spotlight and recommend exclusively from those provided candidates. Do NOT suggest movies outside this list or claim candidates are missing.\n"
                    f"2. {req_num_phrase}\n"
                    "3. Format your response in clean, stylish markdown with bold titles, release years, and ratings.\n\n"
                    f"User Profile: Top Genres: {json.dumps(taste.top_genres)}, Completed: {taste.completed_count} titles.\n"
                    f"Actions Already Taken: {json.dumps(action_summary)}\n"
                    f"Candidate Titles in Database:\n{json.dumps(cand_summary, indent=1)}\n"
                )

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={gemini_key}"
            payload = {
                "contents": [
                    {"parts": [{"text": f"{sys_prompt}\n\nUser Question/Message: {user_msg}"}]}
                ],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": 600}
            }
            with httpx.Client(timeout=12.0) as client:
                res = client.post(url, json=payload)
                if res.status_code == 200:
                    cand_list = res.json().get("candidates", [])
                    if cand_list:
                        agent_text = cand_list[0]["content"]["parts"][0]["text"].strip()
                        if action_prefix and not agent_text.startswith(action_prefix):
                            agent_text = f"{action_prefix}{agent_text}"

                        # For knowledge questions, filter candidate cards to only those relevant to the topic
                        if is_knowledge:
                            relevant_cards = []
                            msg_clean = user_msg.lower()
                            agent_clean = agent_text.lower()
                            for c in candidate_cards:
                                c_title = c.content.title.lower()
                                if c_title in msg_clean or c_title in agent_clean or any(word in c_title for word in msg_clean.split() if len(word) > 4 and word not in STOP_WORDS):
                                    relevant_cards.append(c)
                            candidate_cards = relevant_cards[:4]
        except Exception as ex:
            logger.warning(f"Agent Gemini generation skipped or timed out: {ex}")

    # CASE D: General Curated Fallback
    if not agent_text:
        if is_knowledge:
            agent_text = (
                f"{action_prefix}I explored our entertainment knowledge catalog for your inquiry. "
                f"You can explore related titles and series in our streaming collection below!"
            )
        else:
            top_candidates = [
                f"**{c.content.title}** ({c.content.genre or 'Cinema'}, ⭐ {c.content.average_rating or 8.0}/10)"
                for c in candidate_cards[:4]
            ]
            candidates_str = ", ".join(top_candidates) if top_candidates else "our featured collection"

            agent_text = (
                f"{action_prefix}Here are my top curated picks matching your request:\n\n"
                f"🎬 {candidates_str}.\n\n"
                f"I picked these because they fit your requested vibe with high audience acclaim and captivating narrative pacing. "
                f"You can explore details, jump directly to streaming, or ask me to add any of them to your Watchlist!"
            )

    # 6.5 Reconcile & Align Recommended Items with Text Response and Count Constraint
    req_count = constraints.get("count")
    if candidate_cards and agent_text:
        agent_clean = agent_text.lower()
        mentioned = []
        unmentioned = []
        for c in candidate_cards:
            if c.content.title.lower() in agent_clean:
                mentioned.append(c)
            else:
                unmentioned.append(c)
        if mentioned:
            # Place items spotlighted in the agent's text response first
            candidate_cards = mentioned + unmentioned

    if req_count:
        candidate_cards = candidate_cards[:req_count]
    else:
        candidate_cards = candidate_cards[:4]

    # 7. Persist Conversation in Database
    user_record = AgentMessage(
        conversation_id=conv_id,
        sender="user",
        content=user_msg,
        created_at=utcnow()
    )
    db.add(user_record)

    rec_snapshots = [
        {"id": c.content.id, "title": c.content.title, "score": c.match_score, "reason": c.recommendation_reason}
        for c in candidate_cards
    ]
    agent_record = AgentMessage(
        conversation_id=conv_id,
        sender="agent",
        content=agent_text,
        recommended_items=rec_snapshots,
        actions_taken=[a.model_dump() for a in actions_taken],
        created_at=utcnow()
    )
    db.add(agent_record)
    if conversation:
        conversation.updated_at = utcnow()
    db.commit()

    return AgentChatResponse(
        conversation_id=conv_id,
        message=agent_text,
        recommended_items=candidate_cards,
        actions_taken=actions_taken,
        suggested_replies=suggested_replies
    )
