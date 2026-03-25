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
    const paymentMethodField = document.getElementById('paymentMethod');
    const eventSelect = document.getElementById('event_id');

    function formatMoney(value) {
        const num = Number(value);
        if (!Number.isFinite(num)) return '0.00';
        return num.toFixed(2);
    }

    function getTicketsField() {
        return form ? form.querySelector('[name="tickets"]') : null;
    }

    function getStudentField() {
        return form ? form.querySelector('[name="is_student"]') : null;
    }

    function readFormEventData() {
        if (!form || !form.dataset.eventName) {
            return null;
        }

        return {
            price: form.dataset.price || '0.00',
            name: form.dataset.eventName || '',
            date: form.dataset.eventDate || '',
            venue: form.dataset.venueName || '',
            remainingSeats: form.dataset.remainingSeats || '',
            maxTickets: form.dataset.maxTickets || '0',
        };
    }

    function readSelectedEventData() {
        if (!eventSelect) return null;
        const option = eventSelect.options[eventSelect.selectedIndex];
        if (!option || !option.value) {
            return null;
        }

        return {
            price: option.dataset.price || '0.00',
            name: option.dataset.eventName || option.textContent.trim(),
            date: option.dataset.eventDate || '',
            venue: option.dataset.venueName || '',
            remainingSeats: option.dataset.remainingSeats || '',
            maxTickets: option.dataset.maxTickets || '10',
        };
    }

    function applyTicketOptions(maxTickets, preserveValue) {
        const ticketsField = getTicketsField();
        if (!ticketsField) return;

        const max = Math.max(0, Number(maxTickets) || 0);
        const selectedValue = preserveValue && Number(preserveValue) <= max ? String(preserveValue) : '1';

        ticketsField.innerHTML = '';

        if (max < 1) {
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'Select an event first';
            ticketsField.appendChild(placeholder);
            ticketsField.disabled = true;
            ticketsField.value = '';
            return;
        }

        ticketsField.disabled = false;
        for (let i = 1; i <= max; i += 1) {
            const option = document.createElement('option');
            option.value = String(i);
            option.textContent = String(i);
            if (String(i) === selectedValue) {
                option.selected = true;
            }
            ticketsField.appendChild(option);
        }
        ticketsField.value = selectedValue;
    }

    function syncEventSummary(eventData) {
        const summaryEventName = document.getElementById('summaryEventName');
        const summaryEventMeta = document.getElementById('summaryEventMeta');
        const summaryRemainingSeatsRow = document.getElementById('summaryRemainingSeatsRow');
        const summaryRemainingSeats = document.getElementById('summaryRemainingSeats');
        const modalEventName = document.getElementById('modalEventName');
        const modalEventMeta = document.getElementById('modalEventMeta');
        const bookingDate = form ? form.dataset.bookingDate || '' : '';

        if (!eventData) {
            if (summaryEventName) summaryEventName.textContent = 'No event selected';
            if (summaryEventMeta) summaryEventMeta.textContent = 'Select an event to see the date, venue, and price.';
            if (summaryRemainingSeatsRow) summaryRemainingSeatsRow.hidden = true;
            if (summaryRemainingSeats) summaryRemainingSeats.textContent = '';
            if (modalEventName) modalEventName.textContent = 'No event selected';
            if (modalEventMeta) modalEventMeta.textContent = 'Select an event to continue.';
            return;
        }

        const metaParts = [];
        if (eventData.date) metaParts.push(eventData.date);
        if (eventData.venue) metaParts.push(eventData.venue);

        if (summaryEventName) summaryEventName.textContent = eventData.name || 'Selected event';
        if (summaryEventMeta) {
            summaryEventMeta.textContent = metaParts.length ? metaParts.join(' | ') : 'Event details will appear here.';
        }
        if (summaryRemainingSeatsRow) summaryRemainingSeatsRow.hidden = false;
        if (summaryRemainingSeats) {
            summaryRemainingSeats.textContent = eventData.remainingSeats || 'Unlimited';
        }
        if (modalEventName) modalEventName.textContent = eventData.name || 'Selected event';
        if (modalEventMeta) {
            const modalParts = [];
            if (eventData.date) modalParts.push(`Event date: ${eventData.date}`);
            if (eventData.venue) modalParts.push(`Venue: ${eventData.venue}`);
            if (bookingDate) modalParts.push(`Booking date: ${bookingDate}`);
            modalEventMeta.textContent = modalParts.length ? modalParts.join(' | ') : 'Select an event to continue.';
        }
    }

    function syncBookingSelection(eventData) {
        if (!form) return;

        if (!eventData) {
            form.dataset.price = '0.00';
            form.dataset.eventName = '';
            form.dataset.eventDate = '';
            form.dataset.venueName = '';
            form.dataset.remainingSeats = '';
            form.dataset.maxTickets = '0';
            applyTicketOptions(0, '');
            syncEventSummary(null);
            return;
        }

        form.dataset.price = eventData.price || '0.00';
        form.dataset.eventName = eventData.name || '';
        form.dataset.eventDate = eventData.date || '';
        form.dataset.venueName = eventData.venue || '';
        form.dataset.remainingSeats = eventData.remainingSeats || '';
        form.dataset.maxTickets = String(eventData.maxTickets || 0);

        const ticketsField = getTicketsField();
        const currentTickets = ticketsField && ticketsField.value ? ticketsField.value : '1';
        applyTicketOptions(eventData.maxTickets || 0, currentTickets);
        syncEventSummary(eventData);
    }

    function updateBookingTotals() {
        if (!form) return;

        const basePrice = Number(form.dataset.price || '0');
        const ticketsField = getTicketsField();
        const rawTickets = ticketsField && ticketsField.value ? parseInt(ticketsField.value, 10) : 0;
        const tickets = Number.isFinite(rawTickets) && rawTickets > 0 ? rawTickets : 0;
        const isStudentField = getStudentField();
        const isStudent = Boolean(isStudentField && isStudentField.checked);

        const subtotal = basePrice * tickets;
        const discount = isStudent ? subtotal * 0.1 : 0;
        const total = subtotal - discount;

        const summaryTickets = document.getElementById('summaryTickets');
        const summarySubtotal = document.getElementById('summarySubtotal');
        const summaryDiscount = document.getElementById('summaryDiscount');
        const summaryTotal = document.getElementById('summaryTotal');
        const modalTotal = document.getElementById('modalTotal');

        if (summaryTickets) summaryTickets.textContent = String(tickets);
        if (summarySubtotal) summarySubtotal.textContent = formatMoney(subtotal);
        if (summaryDiscount) summaryDiscount.textContent = formatMoney(discount);
        if (summaryTotal) summaryTotal.textContent = formatMoney(total);
        if (modalTotal) modalTotal.textContent = formatMoney(total);
    }

    function validateForm(showErrors = true) {
        if (!form) return false;
        let isValid = true;
        const requiredFields = form.querySelectorAll('[required]');
        const maxTickets = Number(form.dataset.maxTickets || '10') || 10;

        requiredFields.forEach((field) => {
            if (field.disabled) {
                return;
            }

            const formGroup = field.closest('.form-group');
            if (!field.value.trim()) {
                if (showErrors && formGroup) formGroup.classList.add('error');
                isValid = false;
            } else if (showErrors && formGroup) {
                formGroup.classList.remove('error');
            }

            if (field.type === 'email' && field.value.trim()) {
                const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
                if (!emailRegex.test(field.value.trim())) {
                    if (showErrors && formGroup) formGroup.classList.add('error');
                    isValid = false;
                }
            }

            if (field.name === 'tickets' && field.value.trim()) {
                const tickets = parseInt(field.value, 10);
                if (tickets < 1 || tickets > maxTickets) {
                    if (showErrors && formGroup) formGroup.classList.add('error');
                    isValid = false;
                }
            }
        });

        return isValid;
    }

    if (form && proceedBtn) {
        const refreshBookingUi = (showErrors = true) => {
            updateBookingTotals();
            proceedBtn.disabled = !validateForm(showErrors);
        };

        if (eventSelect) {
            eventSelect.addEventListener('change', function () {
                syncBookingSelection(readSelectedEventData());
                refreshBookingUi(true);
            });
        }

        const watchedFields = form.querySelectorAll('input, select, textarea');
        watchedFields.forEach((field) => {
            field.addEventListener('input', () => refreshBookingUi(true));
            field.addEventListener('change', () => refreshBookingUi(true));
        });

        syncBookingSelection(readFormEventData());
        refreshBookingUi(false);

        if (modal) {
            proceedBtn.addEventListener('click', function () {
                if (validateForm(true)) {
                    updateBookingTotals();
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
        if (paymentMethodField) {
            const selectedPayment = document.querySelector('input[name="payment"]:checked');
            paymentMethodField.value = selectedPayment ? selectedPayment.value : 'card';
        }
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
