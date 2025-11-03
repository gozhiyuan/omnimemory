"""Simple search endpoint backed by Qdrant."""

from fastapi import APIRouter, Query

from ..vectorstore import search


router = APIRouter()


@router.get("/", summary="Search processed items")
def search_items(q: str = Query(..., description="Search query"), limit: int = Query(5, ge=1, le=50)) -> dict:
    results = search(q, limit=limit)
    return {"query": q, "results": results}
