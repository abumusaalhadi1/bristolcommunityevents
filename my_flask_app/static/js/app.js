(function () {
    const hamburger = document.querySelector('.hamburger');
    const navList = document.querySelector('nav ul');

    if (hamburger && navList) {
        hamburger.addEventListener('click', function () {
            navList.classList.toggle('show');
        });
    }

    const form = document.getElementById('bookingForm');
    const proceedBtn = document.getElementById('proceedToPayment');
    const modal = document.getElementById('paymentModal');
    const closeModal = document.querySelector('.close-modal');

    function validateForm() {
        if (!form) return false;
        let isValid = true;
        const requiredFields = form.querySelectorAll('[required]');

        requiredFields.forEach((field) => {
            const formGroup = field.closest('.form-group');
            if (!field.value.trim()) {
                if (formGroup) formGroup.classList.add('error');
                isValid = false;
            } else if (formGroup) {
                formGroup.classList.remove('error');
            }

            if (field.type === 'email' && field.value.trim()) {
                const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
                if (!emailRegex.test(field.value.trim())) {
                    if (formGroup) formGroup.classList.add('error');
                    isValid = false;
                }
            }

            if (field.name === 'tickets' && field.value.trim()) {
                const tickets = parseInt(field.value, 10);
                if (tickets < 1 || tickets > 10) {
                    if (formGroup) formGroup.classList.add('error');
                    isValid = false;
                }
            }
        });

        return isValid;
    }

    if (form && proceedBtn) {
        const requiredFields = form.querySelectorAll('[required]');

        requiredFields.forEach((field) => {
            field.addEventListener('input', function () {
                const formGroup = this.closest('.form-group');
                if (this.value.trim() && formGroup) {
                    formGroup.classList.remove('error');
                }
                proceedBtn.disabled = !validateForm();
            });

            field.addEventListener('change', function () {
                proceedBtn.disabled = !validateForm();
            });
        });

        proceedBtn.disabled = true;

        if (modal) {
            proceedBtn.addEventListener('click', function () {
                if (validateForm()) {
                    modal.style.display = 'flex';
                }
            });
        }
    }

    if (closeModal && modal) {
        closeModal.addEventListener('click', function () {
            modal.style.display = 'none';
        });
    }

    if (modal) {
        window.addEventListener('click', function (e) {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        });
    }

    window.processPayment = function () {
        if (!form) return;
        if (typeof form.requestSubmit === 'function') {
            form.requestSubmit();
        } else {
            form.submit();
        }
    };

    const authTabs = document.querySelectorAll('.auth-tab');
    if (authTabs.length) {
        authTabs.forEach((tab) => {
            tab.addEventListener('click', function () {
                authTabs.forEach((t) => t.classList.remove('active'));
                this.classList.add('active');

                const loginForm = document.getElementById('login-form');
                const registerForm = document.getElementById('register-form');

                if (this.textContent.trim() === 'Login') {
                    if (loginForm) loginForm.style.display = 'block';
                    if (registerForm) registerForm.style.display = 'none';
                } else {
                    if (loginForm) loginForm.style.display = 'none';
                    if (registerForm) registerForm.style.display = 'block';
                }
            });
        });
    }
})();