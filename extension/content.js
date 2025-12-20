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

      // Add click event to show modal
      mark.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.showTooltip(e, viewData);
      });
    }

    showTooltip(event, viewData) {
      this.hideTooltip();

      // Create overlay backdrop
      const overlay = document.createElement("div");
      overlay.className = "dvf-overlay";
      overlay.addEventListener("click", () => this.hideTooltip());

      // Create centered modal tooltip
      const tooltip = document.createElement("div");
      tooltip.className = "dvf-tooltip dvf-tooltip-centered";
      tooltip.innerHTML = this.renderTooltipContent(viewData);

      // Add close button functionality
      const closeBtn = tooltip.querySelector(".dvf-tooltip-close");
      if (closeBtn) {
        closeBtn.addEventListener("click", () => this.hideTooltip());
      }

      // Prevent clicks inside tooltip from closing it
      tooltip.addEventListener("click", (e) => e.stopPropagation());

      document.body.appendChild(overlay);
      document.body.appendChild(tooltip);
      this.currentOverlay = overlay;
      this.currentTooltip = tooltip;

      // Prevent body scroll when modal is open
      document.body.style.overflow = "hidden";
    }

    scheduleHideTooltip() {
      // No longer used for centered modal, but keep for compatibility
    }

    cancelHideTooltip() {
      // No longer used for centered modal, but keep for compatibility
    }

    hideTooltip() {
      if (this.currentOverlay) {
        this.currentOverlay.remove();
        this.currentOverlay = null;
      }
      if (this.currentTooltip) {
        this.currentTooltip.remove();
        this.currentTooltip = null;
      }
      // Restore body scroll
      document.body.style.overflow = "";
    }

    renderTooltipContent(viewData) {
      // Limit references to 5
      const limitedRefs = viewData.references.slice(0, 5);
      const refsHtml = limitedRefs
        .map(
          (ref) => `
          <div class="dvf-tooltip-ref">
            <a href="${this.escapeHtml(ref.url)}" target="_blank" rel="noopener" class="dvf-tooltip-ref-title">
              ${this.escapeHtml(ref.title)}
            </a>
            <p class="dvf-tooltip-ref-quote" title="${this.escapeHtml(ref.quote).replace(/"/g, '&quot;')}">"${this.escapeHtml(ref.quote)}"</p>
          </div>
        `
        )
        .join("");

      return `
        <div class="dvf-tooltip-header">
          <span>💬 이런 관점도 있어요</span>
          <button class="dvf-tooltip-close" aria-label="닫기">✕</button>
        </div>
        <div class="dvf-tooltip-content">${this.escapeHtml(viewData.different_view)}</div>
        <div class="dvf-tooltip-refs">
          <div class="dvf-tooltip-refs-header">📚 참고 기사 ${limitedRefs.length < viewData.references.length ? `(${limitedRefs.length}/${viewData.references.length})` : ''}</div>
          ${refsHtml}
        </div>
      `;
    }

    escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text;
      return div.innerHTML;
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
