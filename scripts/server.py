"""FastAPI server for Diverse View Finder."""

import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Load environment variables before importing services
load_dotenv()

from src.config import DB_PATH
from src.services import (
    DifferentViewExtractorService,
    DiverseViewFinder,
    EmbeddingService,
    KeywordGeneratorService,
    VectorDBService,
)


# Request/Response Models
class DiverseViewRequest(BaseModel):
    title: str
    body: str


class ArticleReferenceResponse(BaseModel):
    title: str
    quote: str
    url: str


class DifferentViewResponse(BaseModel):
    exact_text: str
    different_view: str
    references: list[ArticleReferenceResponse]


class DiverseViewResponse(BaseModel):
    input_summary: str
    different_views: list[DifferentViewResponse]


class HealthResponse(BaseModel):
    status: str
    indexed_articles: int


# Application Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup, cleanup on shutdown."""
    # Startup
    vector_db = VectorDBService(DB_PATH)
    vector_db.connect()

    stats = vector_db.get_index_stats()
    print(f"Vector DB connected: {stats['indexed_articles']} articles indexed")

    finder = DiverseViewFinder(
        embedding_service=EmbeddingService(),
        vector_db=vector_db,
        keyword_generator=KeywordGeneratorService(),
        different_view_extractor=DifferentViewExtractorService(),
    )

    app.state.finder = finder
    app.state.vector_db = vector_db

    yield

    # Shutdown
    vector_db.close()
    print("Vector DB connection closed")


# Create FastAPI app
app = FastAPI(
    title="Diverse View Finder API",
    description="Find diverse perspectives on Korean news articles",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware - allow all origins for Chrome extension
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check server health and vector DB status."""
    vector_db: VectorDBService = app.state.vector_db
    stats = vector_db.get_index_stats()
    return HealthResponse(
        status="healthy",
        indexed_articles=stats["indexed_articles"],
    )


@app.post("/api/diverse-views", response_model=DiverseViewResponse)
async def find_diverse_views(request: DiverseViewRequest):
    """Find diverse perspectives for the given article."""
    finder: DiverseViewFinder = app.state.finder

    # Combine title and body for analysis
    input_text = f"{request.title}\n\n{request.body}"

    try:
        result = await finder.find_diverse_views(input_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Convert dataclass to response model
    return DiverseViewResponse(
        input_summary=result.input_summary,
        different_views=[
            DifferentViewResponse(
                exact_text=view.exact_text,
                different_view=view.different_view,
                references=[
                    ArticleReferenceResponse(
                        title=ref.title,
                        quote=ref.quote,
                        url=ref.url,
                    )
                    for ref in view.reference
                ],
            )
            for view in result.different_views
        ],
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
