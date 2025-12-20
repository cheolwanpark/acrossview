# Diverse View Finder - FastAPI Server & Chrome Extension

## Overview

Create a FastAPI server to expose the Diverse View Finder API and a Chrome extension that highlights different perspectives on Naver news articles with tooltips.

---

## Part 1: FastAPI Server

### File: `scripts/server.py`

### Dependencies to Add

```toml
# Add to pyproject.toml dependencies array:
"fastapi>=0.115.0",
"uvicorn[standard]>=0.34.0",
```

Then run: `uv sync`

### Complete Server Implementation

```python
"""FastAPI server for Diverse View Finder."""

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
```

---

## Part 2: Chrome Extension

### Directory Structure

```
extension/
├── manifest.json
├── background.js
├── content.js
└── styles.css
```

### File 1: `extension/manifest.json`

```json
{
  "manifest_version": 3,
  "name": "다양한 관점 찾기",
  "version": "1.0.0",
  "description": "네이버 뉴스 기사에서 다양한 관점을 찾아 하이라이트합니다",
  "permissions": [],
  "host_permissions": [
    "http://localhost:8000/*"
  ],
  "background": {
    "service_worker": "background.js"
  },
  "content_scripts": [
    {
      "matches": ["*://n.news.naver.com/mnews/article/*"],
      "js": ["content.js"],
      "css": ["styles.css"],
      "run_at": "document_idle"
    }
  ]
}
```

### File 2: `extension/background.js`

```javascript
/**
 * Background service worker for API calls.
 * Content scripts on HTTPS cannot fetch from HTTP localhost (mixed content).
 * Service workers can make any HTTP request.
 */

const API_BASE_URL = "http://localhost:8000";

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "FETCH_DIVERSE_VIEWS") {
    fetchDiverseViews(request.data)
      .then((response) => sendResponse({ success: true, data: response }))
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true; // Keep message channel open for async response
  }
});

async function fetchDiverseViews(articleData) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 60000); // 60s timeout

  try {
    const response = await fetch(`${API_BASE_URL}/api/diverse-views`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(articleData),
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `HTTP ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    clearTimeout(timeoutId);
    if (error.name === "AbortError") {
      throw new Error("요청 시간이 초과되었습니다. 다시 시도해주세요.");
    }
    throw error;
  }
}
```

### File 3: `extension/styles.css`

```css
/* ========================================
   TOAST NOTIFICATIONS
   ======================================== */

.dvf-toast-container {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 2147483647;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}

.dvf-toast {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  border-radius: 10px;
  background: #1a1a2e;
  color: #fff;
  font-size: 14px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
  animation: dvf-slide-in 0.3s ease-out;
  max-width: 320px;
}

.dvf-toast-loading {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.dvf-toast-success {
  background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
}

.dvf-toast-error {
  background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%);
}

.dvf-toast-info {
  background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
}

.dvf-toast-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.dvf-toast-message {
  flex: 1;
  line-height: 1.4;
}

@keyframes dvf-slide-in {
  from {
    transform: translateX(100%);
    opacity: 0;
  }
  to {
    transform: translateX(0);
    opacity: 1;
  }
}

@keyframes dvf-slide-out {
  from {
    transform: translateX(0);
    opacity: 1;
  }
  to {
    transform: translateX(100%);
    opacity: 0;
  }
}

/* Spinner for loading state */
.dvf-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: dvf-spin 0.8s linear infinite;
}

@keyframes dvf-spin {
  to {
    transform: rotate(360deg);
  }
}

/* ========================================
   TEXT HIGHLIGHT
   ======================================== */

.dvf-highlight {
  background: linear-gradient(120deg, #fef3c7 0%, #fde68a 100%);
  padding: 2px 4px;
  margin: 0 -2px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
}

.dvf-highlight:hover {
  background: linear-gradient(120deg, #fde68a 0%, #fbbf24 100%);
  box-shadow: 0 2px 8px rgba(251, 191, 36, 0.4);
}

/* ========================================
   TOOLTIP
   ======================================== */

.dvf-tooltip {
  position: fixed;
  max-width: 420px;
  padding: 0;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 10px 50px rgba(0, 0, 0, 0.2);
  z-index: 2147483646;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 14px;
  line-height: 1.6;
  animation: dvf-tooltip-in 0.2s ease-out;
  overflow: hidden;
}

@keyframes dvf-tooltip-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.dvf-tooltip-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  padding: 14px 18px;
  font-weight: 600;
  font-size: 15px;
}

.dvf-tooltip-content {
  padding: 16px 18px;
  color: #374151;
  border-bottom: 1px solid #e5e7eb;
}

.dvf-tooltip-refs {
  padding: 14px 18px;
  background: #f9fafb;
}

.dvf-tooltip-refs-header {
  font-weight: 600;
  color: #4b5563;
  margin-bottom: 10px;
  font-size: 13px;
}

.dvf-tooltip-ref {
  margin-bottom: 12px;
  padding-left: 12px;
  border-left: 3px solid #667eea;
}

.dvf-tooltip-ref:last-child {
  margin-bottom: 0;
}

.dvf-tooltip-ref-title {
  color: #667eea;
  text-decoration: none;
  font-weight: 500;
  display: block;
  margin-bottom: 4px;
}

.dvf-tooltip-ref-title:hover {
  text-decoration: underline;
}

.dvf-tooltip-ref-quote {
  color: #6b7280;
  font-size: 13px;
  margin: 0;
  font-style: italic;
}
```

### File 4: `extension/content.js`

```javascript
/**
 * Content script for Diverse View Finder extension.
 * Extracts article content, highlights text, and shows tooltips.
 */

(function () {
  "use strict";

  // Prevent multiple executions
  if (window.__DVF_INITIALIZED__) return;
  window.__DVF_INITIALIZED__ = true;

  // ========================================
  // TOAST NOTIFICATIONS
  // ========================================

  class Toast {
    constructor() {
      this.container = document.createElement("div");
      this.container.className = "dvf-toast-container";
      document.body.appendChild(this.container);
    }

    show(message, type = "info", duration = 0) {
      const toast = document.createElement("div");
      toast.className = `dvf-toast dvf-toast-${type}`;

      const icon = document.createElement("span");
      icon.className = "dvf-toast-icon";
      icon.innerHTML = this.getIcon(type);

      const msg = document.createElement("span");
      msg.className = "dvf-toast-message";
      msg.textContent = message;

      toast.appendChild(icon);
      toast.appendChild(msg);
      this.container.appendChild(toast);

      if (duration > 0) {
        setTimeout(() => this.remove(toast), duration);
      }

      return toast;
    }

    remove(toast) {
      if (!toast || !toast.parentNode) return;
      toast.style.animation = "dvf-slide-out 0.3s ease-out forwards";
      setTimeout(() => toast.remove(), 300);
    }

    getIcon(type) {
      const icons = {
        loading: '<div class="dvf-spinner"></div>',
        success: "✓",
        error: "✕",
        info: "💡",
      };
      return icons[type] || icons.info;
    }

    showLoading(message) {
      return this.show(message, "loading");
    }

    showSuccess(message) {
      return this.show(message, "success", 4000);
    }

    showError(message) {
      return this.show(message, "error", 5000);
    }

    showInfo(message) {
      return this.show(message, "info", 4000);
    }
  }

  // ========================================
  // TEXT HIGHLIGHTER
  // ========================================

  class TextHighlighter {
    constructor(containerEl) {
      this.container = containerEl;
      this.highlights = [];
      this.currentTooltip = null;
    }

    highlight(exactText, viewData) {
      // Find text nodes containing the exact text
      const walker = document.createTreeWalker(
        this.container,
        NodeFilter.SHOW_TEXT,
        null,
        false
      );

      let node;
      while ((node = walker.nextNode())) {
        const text = node.textContent;
        const index = text.indexOf(exactText);

        if (index !== -1) {
          try {
            this.wrapText(node, index, exactText, viewData);
            return true; // Only highlight first occurrence
          } catch (e) {
            console.warn("DVF: Could not wrap text:", e);
          }
        }
      }

      // Try fuzzy match if exact match fails
      return this.fuzzyHighlight(exactText, viewData);
    }

    fuzzyHighlight(exactText, viewData) {
      // Normalize whitespace and try again
      const normalizedSearch = exactText.replace(/\s+/g, " ").trim();
      if (normalizedSearch.length < 10) return false;

      const walker = document.createTreeWalker(
        this.container,
        NodeFilter.SHOW_TEXT,
        null,
        false
      );

      let node;
      while ((node = walker.nextNode())) {
        const normalizedText = node.textContent.replace(/\s+/g, " ");
        const index = normalizedText.indexOf(normalizedSearch);

        if (index !== -1) {
          // Find the actual index in original text
          let actualIndex = 0;
          let normalizedIndex = 0;
          const originalText = node.textContent;

          while (normalizedIndex < index && actualIndex < originalText.length) {
            if (/\s/.test(originalText[actualIndex])) {
              while (actualIndex < originalText.length && /\s/.test(originalText[actualIndex])) {
                actualIndex++;
              }
              normalizedIndex++;
            } else {
              actualIndex++;
              normalizedIndex++;
            }
          }

          try {
            // Find end index similarly
            let endActualIndex = actualIndex;
            let searchIndex = 0;
            while (searchIndex < normalizedSearch.length && endActualIndex < originalText.length) {
              if (/\s/.test(originalText[endActualIndex])) {
                while (endActualIndex < originalText.length && /\s/.test(originalText[endActualIndex])) {
                  endActualIndex++;
                }
                if (/\s/.test(normalizedSearch[searchIndex])) {
                  searchIndex++;
                }
              } else {
                endActualIndex++;
                searchIndex++;
              }
            }

            const matchedText = originalText.substring(actualIndex, endActualIndex);
            this.wrapText(node, actualIndex, matchedText, viewData);
            return true;
          } catch (e) {
            console.warn("DVF: Fuzzy wrap failed:", e);
          }
        }
      }

      return false;
    }

    wrapText(textNode, startIndex, text, viewData) {
      const range = document.createRange();
      range.setStart(textNode, startIndex);
      range.setEnd(textNode, startIndex + text.length);

      const mark = document.createElement("mark");
      mark.className = "dvf-highlight";
      mark.dataset.viewData = JSON.stringify(viewData);

      range.surroundContents(mark);
      this.highlights.push(mark);

      // Add hover events
      mark.addEventListener("mouseenter", (e) => this.showTooltip(e, viewData));
      mark.addEventListener("mouseleave", () => this.scheduleHideTooltip());
    }

    showTooltip(event, viewData) {
      this.cancelHideTooltip();
      this.hideTooltip();

      const tooltip = document.createElement("div");
      tooltip.className = "dvf-tooltip";
      tooltip.innerHTML = this.renderTooltipContent(viewData);

      // Keep tooltip visible when hovering over it
      tooltip.addEventListener("mouseenter", () => this.cancelHideTooltip());
      tooltip.addEventListener("mouseleave", () => this.scheduleHideTooltip());

      document.body.appendChild(tooltip);
      this.positionTooltip(tooltip, event.target);
      this.currentTooltip = tooltip;
    }

    scheduleHideTooltip() {
      this.hideTimeout = setTimeout(() => this.hideTooltip(), 200);
    }

    cancelHideTooltip() {
      if (this.hideTimeout) {
        clearTimeout(this.hideTimeout);
        this.hideTimeout = null;
      }
    }

    hideTooltip() {
      if (this.currentTooltip) {
        this.currentTooltip.remove();
        this.currentTooltip = null;
      }
    }

    renderTooltipContent(viewData) {
      const refsHtml = viewData.references
        .map(
          (ref) => `
          <div class="dvf-tooltip-ref">
            <a href="${this.escapeHtml(ref.url)}" target="_blank" rel="noopener" class="dvf-tooltip-ref-title">
              ${this.escapeHtml(ref.title)}
            </a>
            <p class="dvf-tooltip-ref-quote">"${this.escapeHtml(ref.quote)}"</p>
          </div>
        `
        )
        .join("");

      return `
        <div class="dvf-tooltip-header">💬 이런 관점도 있어요</div>
        <div class="dvf-tooltip-content">${this.escapeHtml(viewData.different_view)}</div>
        <div class="dvf-tooltip-refs">
          <div class="dvf-tooltip-refs-header">📚 참고 기사</div>
          ${refsHtml}
        </div>
      `;
    }

    escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text;
      return div.innerHTML;
    }

    positionTooltip(tooltip, targetEl) {
      const rect = targetEl.getBoundingClientRect();
      const tooltipRect = tooltip.getBoundingClientRect();

      // Try to position above the highlight
      let top = rect.top - tooltipRect.height - 12;
      let left = rect.left + rect.width / 2 - tooltipRect.width / 2;

      // If not enough space above, position below
      if (top < 10) {
        top = rect.bottom + 12;
      }

      // Keep within viewport horizontally
      if (left < 10) {
        left = 10;
      } else if (left + tooltipRect.width > window.innerWidth - 10) {
        left = window.innerWidth - tooltipRect.width - 10;
      }

      tooltip.style.top = `${top + window.scrollY}px`;
      tooltip.style.left = `${left}px`;
      tooltip.style.position = "absolute";
    }
  }

  // ========================================
  // ARTICLE EXTRACTION
  // ========================================

  function extractArticle() {
    const titleEl = document.getElementById("title_area");
    const bodyEl = document.getElementById("dic_area");

    if (!titleEl) {
      throw new Error("기사 제목을 찾을 수 없습니다");
    }
    if (!bodyEl) {
      throw new Error("기사 본문을 찾을 수 없습니다");
    }

    return {
      title: titleEl.textContent.trim(),
      body: bodyEl.textContent.trim(),
    };
  }

  // ========================================
  // MAIN EXECUTION
  // ========================================

  async function main() {
    const toast = new Toast();

    try {
      // Extract article content
      const article = extractArticle();

      if (article.body.length < 100) {
        console.log("DVF: Article too short, skipping");
        return;
      }

      // Show loading toast
      const loadingToast = toast.showLoading("다양한 관점을 찾고 있습니다...");

      // Send request to background script
      const response = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          {
            type: "FETCH_DIVERSE_VIEWS",
            data: article,
          },
          (response) => {
            if (chrome.runtime.lastError) {
              reject(new Error(chrome.runtime.lastError.message));
            } else if (!response.success) {
              reject(new Error(response.error));
            } else {
              resolve(response.data);
            }
          }
        );
      });

      // Remove loading toast
      toast.remove(loadingToast);

      // Check if we got any results
      if (!response.different_views || response.different_views.length === 0) {
        toast.showInfo("이 기사에 대한 다른 관점을 찾지 못했습니다");
        return;
      }

      // Highlight text and add tooltips
      const bodyEl = document.getElementById("dic_area");
      const highlighter = new TextHighlighter(bodyEl);

      let highlightCount = 0;
      for (const view of response.different_views) {
        if (highlighter.highlight(view.exact_text, view)) {
          highlightCount++;
        }
      }

      // Show success message
      if (highlightCount > 0) {
        toast.showSuccess(`${highlightCount}개의 다른 관점을 찾았습니다!`);
      } else {
        toast.showInfo("다른 관점을 찾았지만 본문에서 위치를 찾지 못했습니다");
      }

    } catch (error) {
      console.error("DVF Error:", error);

      if (error.message.includes("연결") || error.message.includes("fetch")) {
        toast.showError("서버에 연결할 수 없습니다. 서버가 실행 중인지 확인해주세요.");
      } else {
        toast.showError(error.message || "다양한 관점 찾기에 실패했습니다");
      }
    }
  }

  // Wait for DOM to be ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    // Small delay to ensure Naver's dynamic content is loaded
    setTimeout(main, 500);
  }
})();
```

---

## Running the Project

### 1. Start the Server

```bash
# Option A: Direct run
uv run python scripts/server.py

# Option B: With uvicorn (enables auto-reload)
uv run uvicorn scripts.server:app --reload --host 0.0.0.0 --port 8000
```

### 2. Load the Extension

1. Open Chrome and go to `chrome://extensions/`
2. Enable "Developer mode" (toggle in top right)
3. Click "Load unpacked"
4. Select the `extension/` directory
5. The extension should now be loaded

### 3. Test

1. Navigate to any Naver news article: `https://n.news.naver.com/mnews/article/...`
2. Wait for the loading toast to appear (bottom-right)
3. After processing (10-30 seconds), highlighted text should appear
4. Hover over highlighted text to see the tooltip with different perspectives

---

## API Reference

### `POST /api/diverse-views`

**Request:**
```json
{
  "title": "기사 제목",
  "body": "기사 본문 전체 텍스트..."
}
```

**Response:**
```json
{
  "input_summary": "주제 요약",
  "different_views": [
    {
      "exact_text": "본문에서 인용된 원문",
      "different_view": "이 부분에 대해 다른 시각을 가진 전문가들은...",
      "references": [
        {
          "title": "참고 기사 제목",
          "quote": "기사에서 인용된 문장",
          "url": "https://..."
        }
      ]
    }
  ]
}
```

### `GET /health`

**Response:**
```json
{
  "status": "healthy",
  "indexed_articles": 12345
}
```

---

## Error Handling

| Error | Server Response | Extension Behavior |
|-------|-----------------|-------------------|
| Missing GEMINI_API_KEY | 500 at startup | Server won't start |
| Empty vector DB | Empty `different_views` | "다른 관점을 찾지 못했습니다" |
| Server timeout (60s) | 504 | "요청 시간이 초과되었습니다" |
| Server unreachable | - | "서버에 연결할 수 없습니다" |

---

## Files Summary

| File | Purpose |
|------|---------|
| `pyproject.toml` | Add fastapi, uvicorn dependencies |
| `scripts/server.py` | FastAPI server with `/api/diverse-views` endpoint |
| `extension/manifest.json` | Chrome extension manifest (v3) |
| `extension/background.js` | Service worker for API calls |
| `extension/content.js` | DOM manipulation, highlighting, tooltips |
| `extension/styles.css` | Toast, highlight, tooltip styles |

---

## Architecture Notes

### Why Background Service Worker?

Content scripts running on HTTPS pages (like `https://n.news.naver.com`) cannot make HTTP requests to `http://localhost:8000` due to mixed content security policies. The background service worker bypasses this restriction and can make any HTTP request.

### Text Highlighting Strategy

1. First attempt: Exact string match using TreeWalker
2. Fallback: Fuzzy match with normalized whitespace
3. Use Range API to wrap text in `<mark>` elements
4. Store view data in `data-*` attributes for tooltip

### Message Flow

```
User navigates to article
       ↓
content.js extracts title/body
       ↓
content.js sends message to background.js
       ↓
background.js fetches from localhost:8000
       ↓
Server processes with DiverseViewFinder (10-30s)
       ↓
Response sent back through message channel
       ↓
content.js highlights text and adds tooltips
```
