// ==========================================================================
// NHIỆM AUTOTECH ELECTRICAL - MAIN INTERACTIVE JAVASCRIPT
// ==========================================================================

document.addEventListener("DOMContentLoaded", () => {
  // 1. Auto-dismiss flash notifications after 4.5 seconds
  document.querySelectorAll(".flash").forEach((el) => {
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateY(-10px)";
      el.style.transition = "all 0.4s ease";
      setTimeout(() => el.remove(), 400);
    }, 4500);
  });

  // 2. Mobile Drawer Navigation
  const mobileBtn = document.getElementById("mobileMenuBtn");
  const mobileDrawer = document.getElementById("mobileDrawer");
  const drawerOverlay = document.getElementById("drawerOverlay");
  const drawerCloseBtn = document.getElementById("drawerCloseBtn");

  function openDrawer() {
    if (mobileDrawer && drawerOverlay) {
      mobileDrawer.classList.add("active");
      drawerOverlay.classList.add("active");
      document.body.style.overflow = "hidden";
    }
  }

  function closeDrawer() {
    if (mobileDrawer && drawerOverlay) {
      mobileDrawer.classList.remove("active");
      drawerOverlay.classList.remove("active");
      document.body.style.overflow = "";
    }
  }

  if (mobileBtn) mobileBtn.addEventListener("click", openDrawer);
  if (drawerCloseBtn) drawerCloseBtn.addEventListener("click", closeDrawer);
  if (drawerOverlay) drawerOverlay.addEventListener("click", closeDrawer);

  // 3. Consultation Modal Global Handlers
  window.openConsultationModal = function () {
    const modal = document.getElementById("consultationModal");
    if (modal) {
      modal.classList.add("active");
      document.body.style.overflow = "hidden";
    }
  };

  window.closeConsultationModal = function () {
    const modal = document.getElementById("consultationModal");
    if (modal) {
      modal.classList.remove("active");
      document.body.style.overflow = "";
    }
  };

  // Close modal when clicking backdrop
  const modal = document.getElementById("consultationModal");
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) {
        window.closeConsultationModal();
      }
    });
  }

  // Close on Escape key press
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      window.closeConsultationModal();
      closeDrawer();
    }
  });

  // 4. Mobile 3-Category Navigation Switcher
  const catLinks = document.querySelectorAll(".mob-cat-link");
  if (catLinks.length) {
    catLinks.forEach((link) => {
      link.addEventListener("click", function (e) {
        const targetId = this.getAttribute("data-target");
        const targetEl = document.getElementById(targetId);

        // Nếu đang ở trang chủ, cuộn mượt tới mục sản phẩm tương ứng mà không cần tải lại trang
        if (targetEl && (window.location.pathname === "/" || window.location.pathname === "")) {
          e.preventDefault();
          if (history.pushState) {
            history.pushState(null, null, "#" + targetId);
          }
          const header = document.querySelector(".header-main");
          const headerH = header ? header.offsetHeight : 95;
          const targetY = targetEl.getBoundingClientRect().top + window.pageYOffset - headerH - 8;
          window.scrollTo({ top: Math.max(0, targetY), behavior: "smooth" });

          catLinks.forEach((l) => l.classList.remove("active"));
          this.classList.add("active");
        }
      });
    });

    // Tự động làm sáng nút danh mục khi người dùng lướt tới khu vực đó trên trang chủ
    if (window.location.pathname === "/" || window.location.pathname === "") {
      const targets = ["showcase-muc-1", "showcase-muc-2", "showcase-muc-3"];
      const sections = targets.map((id) => document.getElementById(id)).filter(Boolean);

      if (sections.length && "IntersectionObserver" in window) {
        const observer = new IntersectionObserver(
          (entries) => {
            entries.forEach((entry) => {
              if (entry.isIntersecting) {
                catLinks.forEach((l) => {
                  if (l.getAttribute("data-target") === entry.target.id) {
                    l.classList.add("active");
                  } else {
                    l.classList.remove("active");
                  }
                });
              }
            });
          },
          { rootMargin: "-25% 0px -55% 0px", threshold: 0 }
        );
        sections.forEach((sec) => observer.observe(sec));
      }
    }
  }
});
