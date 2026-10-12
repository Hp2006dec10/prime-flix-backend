import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_

from app.db.session import get_db
from app.db.models import User, AgentConversation, AgentMessage, Movie, WebSeries, utcnow
from app.api.deps import get_optional_current_user, get_current_user
from app.schemas.agent import (
    WatchProgressRequest,
    WatchProgressResponse,
    UserInteractionRequest,
    UserInteractionResponse,
    TasteProfileResponse,
    AgentChatRequest,
    AgentChatResponse,
    RenameConversationRequest
)
from app.services.agent_service import (
    record_watch_progress,
    record_user_interaction,
    get_user_taste_profile,
    run_agent_concierge_chat
)
from app.api.v1.endpoints.movies import movie_to_movie_response, series_to_movie_response

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/chat", response_model=AgentChatResponse, summary="Chat with Autonomous PrimeFlix AI Entertainment Concierge")
def chat_with_agent(
    req: AgentChatRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Conversational Agent Endpoint:
    Processes natural queries, executes autonomous actions (e.g., managing watchlists),
    reasons over user history and taste preferences, and produces rich movie/series recommendations.
    """
    try:
        return run_agent_concierge_chat(
            user=current_user,
            message=req.message,
            conversation_id=req.conversation_id,
            db=db
        )
    except Exception as e:
        logger.error(f"Error in Agent chat concierge: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI Agent Concierge encountered an error: {str(e)}"
        )


@router.post("/watch-progress", response_model=WatchProgressResponse, summary="Record Video Streaming Playback Progress & Completion")
def update_watch_progress(
    req: WatchProgressRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Called by video player to log watch timestamps, calculate completion rate,
    and automatically flag titles as completed once >= 85% is watched.
    """
    try:
        return record_watch_progress(user_id=current_user.id, req=req, db=db)
    except Exception as e:
        logger.error(f"Error recording watch progress: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not update watch progress: {str(e)}"
        )


@router.post("/interact", response_model=UserInteractionResponse, summary="Record User Engagement (Like, Dislike, Favorite, Rating)")
def submit_user_interaction(
    req: UserInteractionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Records explicit user affinity signals to refine AI agent recommendation persona.
    """
    try:
        return record_user_interaction(user_id=current_user.id, req=req, db=db)
    except Exception as e:
        logger.error(f"Error recording user interaction: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not record interaction: {str(e)}"
        )


@router.get("/taste-profile", response_model=TasteProfileResponse, summary="Get User Taste Profile Metrics & Affinity Breakdown")
def fetch_taste_profile(
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns aggregated genre distribution, top directors, completion counts,
    and liked titles for the active user.
    """
    user_id = current_user.id if current_user else None
    return get_user_taste_profile(user_id=user_id, db=db)


@router.get("/conversations", summary="List Older Agent Conversations")
def list_conversations(
    ids: Optional[str] = None,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns older chat sessions for the active user and/or requested conversation IDs.
    """
    q_filters = []
    ids_list = [i.strip() for i in ids.split(",") if i.strip()] if ids else []

    if current_user:
        if ids_list:
            q_filters.append(or_(AgentConversation.user_id == current_user.id, AgentConversation.id.in_(ids_list)))
        else:
            q_filters.append(AgentConversation.user_id == current_user.id)
    elif ids_list:
        q_filters.append(AgentConversation.id.in_(ids_list))
    else:
        q_filters.append(AgentConversation.user_id.is_(None))

    convs = (
        db.query(AgentConversation)
        .filter(or_(*q_filters))
        .order_by(AgentConversation.updated_at.desc())
        .limit(30)
        .all()
    )

    result = []
    for c in convs:
        last_msg = (
            db.query(AgentMessage)
            .filter(AgentMessage.conversation_id == c.id)
            .order_by(AgentMessage.created_at.desc())
            .first()
        )
        msg_count = db.query(AgentMessage).filter(AgentMessage.conversation_id == c.id).count()
        result.append({
            "id": c.id,
            "title": c.title or "Chat Session",
            "created_at": c.created_at,
            "updated_at": c.updated_at,
            "message_count": msg_count,
            "last_message": last_msg.content[:85] if last_msg else None
        })

    return {
        "conversations": result,
        "count": len(result)
    }


@router.patch("/conversations/{conversation_id}", summary="Rename Conversation Session")
@router.put("/conversations/{conversation_id}", summary="Rename Conversation Session (PUT)")
def rename_conversation(
    conversation_id: str,
    req: RenameConversationRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Updates the title of a conversation session.
    """
    conv = db.query(AgentConversation).filter(AgentConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user and conv.user_id and conv.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to rename this conversation")

    clean_title = req.title.strip()
    if not clean_title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    conv.title = clean_title
    conv.updated_at = utcnow()
    db.commit()
    db.refresh(conv)

    return {
        "status": "success",
        "id": conv.id,
        "title": conv.title,
        "updated_at": conv.updated_at
    }


@router.delete("/conversations/{conversation_id}", summary="Delete Conversation Session")
def delete_conversation(
    conversation_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Deletes a conversation session and all its messages.
    """
    conv = db.query(AgentConversation).filter(AgentConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user and conv.user_id and conv.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this conversation")

    db.delete(conv)
    db.commit()
    return {"status": "success", "message": "Conversation deleted"}


@router.get("/conversations/{conversation_id}/history", summary="Get Chat History for Conversation")
def get_conversation_history(
    conversation_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Retrieves previous message history for a specific conversation session and enriches movie cards.
    """
    conv = db.query(AgentConversation).filter(AgentConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = (
        db.query(AgentMessage)
        .filter(AgentMessage.conversation_id == conversation_id)
        .order_by(AgentMessage.created_at.asc())
        .all()
    )

    # Collect content IDs to batch load
    content_ids = set()
    for m in messages:
        if m.recommended_items:
            for item in m.recommended_items:
                if isinstance(item, dict) and item.get("id"):
                    content_ids.add(item["id"])

    movies_map = {}
    if content_ids:
        movies = db.query(Movie).options(joinedload(Movie.director)).filter(Movie.id.in_(list(content_ids))).all()
        for mv in movies:
            movies_map[mv.id] = movie_to_movie_response(mv)
        series = db.query(WebSeries).options(joinedload(WebSeries.director), joinedload(WebSeries.seasons)).filter(WebSeries.series_id.in_(list(content_ids))).all()
        for s in series:
            movies_map[s.series_id] = series_to_movie_response(s)

    enriched_messages = []
    for m in messages:
        recs = []
        if m.recommended_items:
            for item in m.recommended_items:
                if isinstance(item, dict):
                    cid = item.get("id")
                    content_obj = movies_map.get(cid)
                    if content_obj:
                        recs.append({
                            "content": content_obj,
                            "recommendation_reason": item.get("reason", "Curated for you"),
                            "match_score": item.get("score", 90),
                            "recommendation_type": "agent_curated"
                        })
        enriched_messages.append({
            "id": f"msg-{m.id}",
            "sender": m.sender,
            "text": m.content,
            "recommendations": recs,
            "actionsTaken": m.actions_taken or [],
            "timestamp": m.created_at
        })

    return {
        "conversation_id": conv.id,
        "title": conv.title,
        "messages": enriched_messages
    }
