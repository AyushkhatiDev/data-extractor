/* ═══════════════════════════════════════════════════════════════
   DataExtractor — Premium UI Interactions
   ═══════════════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', function () {

    // ── Particle Background ──
    initParticles();

    // ── Flash Messages → Toasts ──
    initFlashToasts();

    // ── Navbar Scroll Effect ──
    initNavbarScroll();

    // ── Mobile Nav Toggle ──
    initMobileNav();

    // ── User Dropdown ──
    initUserDropdown();

    // ── Scroll Fade-in Animations ──
    initScrollAnimations();
});

/* ── Particle Background ── */
function initParticles() {
    const container = document.getElementById('bgParticles');
    if (!container) return;

    for (let i = 0; i < 30; i++) {
        const span = document.createElement('span');
        const size = Math.random() * 5 + 2;
        span.style.width = size + 'px';
        span.style.height = size + 'px';
        span.style.left = Math.random() * 100 + '%';
        span.style.animationDuration = (Math.random() * 14 + 8) + 's';
        span.style.animationDelay = (Math.random() * 10) + 's';
        container.appendChild(span);
    }
}

/* ── Flash Messages → Toast Notifications ── */
function initFlashToasts() {
    const flashElements = document.querySelectorAll('.flash-data');
    flashElements.forEach(function (el, i) {
        const category = el.dataset.category;
        const message = el.dataset.message;
        setTimeout(function () {
            showToast(message, category);
        }, 200 + i * 200);
    });
}

/* ── Toast Notification System ── */
function showToast(message, type) {
    type = type || 'info';
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const iconMap = {
        success: 'fa-check-circle',
        danger: 'fa-exclamation-circle',
        warning: 'fa-exclamation-triangle',
        info: 'fa-info-circle'
    };

    const toast = document.createElement('div');
    toast.className = 'toast-notification ' + type;
    toast.innerHTML = `
        <i class="fas ${iconMap[type] || iconMap.info} toast-icon"></i>
        <span class="toast-message">${message}</span>
        <button class="toast-close" onclick="dismissToast(this.parentElement)">
            <i class="fas fa-times"></i>
        </button>
    `;

    container.appendChild(toast);

    // Auto-dismiss after 5 seconds
    setTimeout(function () {
        dismissToast(toast);
    }, 5000);
}

function dismissToast(toast) {
    if (!toast || toast.classList.contains('toast-out')) return;
    toast.classList.add('toast-out');
    setTimeout(function () {
        if (toast.parentElement) toast.parentElement.removeChild(toast);
    }, 300);
}

/* ── Navbar Scroll Effect ── */
function initNavbarScroll() {
    const navbar = document.getElementById('premiumNavbar');
    if (!navbar) return;

    let ticking = false;
    window.addEventListener('scroll', function () {
        if (!ticking) {
            window.requestAnimationFrame(function () {
                if (window.scrollY > 30) {
                    navbar.classList.add('scrolled');
                } else {
                    navbar.classList.remove('scrolled');
                }
                ticking = false;
            });
            ticking = true;
        }
    });
}

/* ── Mobile Navigation Toggle ── */
function initMobileNav() {
    const toggle = document.getElementById('navToggle');
    const links = document.getElementById('navLinks');
    if (!toggle || !links) return;

    toggle.addEventListener('click', function () {
        links.classList.toggle('open');
        const icon = toggle.querySelector('i');
        if (links.classList.contains('open')) {
            icon.className = 'fas fa-times';
        } else {
            icon.className = 'fas fa-bars';
        }
    });

    // Close on link click (mobile)
    links.querySelectorAll('a').forEach(function (link) {
        link.addEventListener('click', function () {
            links.classList.remove('open');
            const icon = toggle.querySelector('i');
            icon.className = 'fas fa-bars';
        });
    });
}

/* ── User Dropdown ── */
function initUserDropdown() {
    const dropdown = document.getElementById('userDropdown');
    if (!dropdown) return;

    const btn = dropdown.querySelector('.user-btn');
    if (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            dropdown.classList.toggle('show');
        });
    }

    document.addEventListener('click', function (e) {
        if (!dropdown.contains(e.target)) {
            dropdown.classList.remove('show');
        }
    });
}

/* ── Scroll Fade-in Animations ── */
function initScrollAnimations() {
    const elements = document.querySelectorAll('.fade-in-up');
    if (!elements.length) return;

    // Immediately make above-fold elements visible
    elements.forEach(function (el) {
        const rect = el.getBoundingClientRect();
        if (rect.top < window.innerHeight) {
            el.classList.add('visible');
        }
    });

    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });

        elements.forEach(function (el) {
            if (!el.classList.contains('visible')) {
                observer.observe(el);
            }
        });
    } else {
        // Fallback: just show all
        elements.forEach(function (el) {
            el.classList.add('visible');
        });
    }
}

/* ── Loading Overlay Utilities ── */
function showLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.classList.add('active');
}

function hideLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.classList.remove('active');
}
