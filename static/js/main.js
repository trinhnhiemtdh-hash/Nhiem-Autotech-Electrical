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
});
