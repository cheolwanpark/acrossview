(function() {
  const slides = document.querySelectorAll('.slide');
  const totalSlides = slides.length;
  const currentSlideEl = document.querySelector('.current-slide');
  const totalSlidesEl = document.querySelector('.total-slides');

  let currentIndex = 0;

  // Initialize
  function init() {
    totalSlidesEl.textContent = totalSlides;

    // Check URL hash for initial slide
    const hash = window.location.hash;
    if (hash && hash.startsWith('#slide-')) {
      const slideNum = parseInt(hash.replace('#slide-', ''), 10);
      if (slideNum >= 1 && slideNum <= totalSlides) {
        currentIndex = slideNum - 1;
      }
    }

    showSlide(currentIndex);
  }

  // Show specific slide
  function showSlide(index) {
    // Bounds check
    if (index < 0) index = 0;
    if (index >= totalSlides) index = totalSlides - 1;

    currentIndex = index;

    // Update active class
    slides.forEach((slide, i) => {
      slide.classList.toggle('active', i === currentIndex);
    });

    // Update indicator
    currentSlideEl.textContent = currentIndex + 1;

    // Update URL hash
    window.history.replaceState(null, null, `#slide-${currentIndex + 1}`);
  }

  // Navigation functions
  function nextSlide() {
    if (currentIndex < totalSlides - 1) {
      showSlide(currentIndex + 1);
    }
  }

  function prevSlide() {
    if (currentIndex > 0) {
      showSlide(currentIndex - 1);
    }
  }

  // Fullscreen functionality
  const fullscreenBtn = document.querySelector('.fullscreen-btn');

  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(err => {
        console.log('Fullscreen error:', err);
      });
    } else {
      document.exitFullscreen();
    }
  }

  function updateFullscreenButton() {
    if (fullscreenBtn) {
      fullscreenBtn.classList.toggle('is-fullscreen', !!document.fullscreenElement);
      fullscreenBtn.title = document.fullscreenElement ? 'Exit fullscreen (F)' : 'Fullscreen (F)';
    }
  }

  if (fullscreenBtn) {
    fullscreenBtn.addEventListener('click', toggleFullscreen);
  }

  document.addEventListener('fullscreenchange', updateFullscreenButton);

  // Keyboard navigation
  document.addEventListener('keydown', (e) => {
    // Ignore if focus is on interactive elements
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
      return;
    }

    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
      case 'PageDown':
        e.preventDefault();
        nextSlide();
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
      case 'PageUp':
        e.preventDefault();
        prevSlide();
        break;
      case 'Home':
        e.preventDefault();
        showSlide(0);
        break;
      case 'End':
        e.preventDefault();
        showSlide(totalSlides - 1);
        break;
      case 'f':
      case 'F':
        e.preventDefault();
        toggleFullscreen();
        break;
      case 'Escape':
        // Escape is handled by browser for exiting fullscreen
        break;
    }
  });

  // Handle hash change (browser back/forward)
  window.addEventListener('hashchange', () => {
    const hash = window.location.hash;
    if (hash && hash.startsWith('#slide-')) {
      const slideNum = parseInt(hash.replace('#slide-', ''), 10);
      if (slideNum >= 1 && slideNum <= totalSlides && slideNum - 1 !== currentIndex) {
        showSlide(slideNum - 1);
      }
    }
  });

  // Initialize on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
