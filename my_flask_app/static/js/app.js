(function () {
    const hamburger = document.querySelector('.hamburger');
    const navList = document.querySelector('nav ul');
    const flashMessages = Array.from(document.querySelectorAll('[data-flash-message]'));
    const categoryDropdown = document.querySelector('[data-category-dropdown]');

    if (hamburger && navList) {
        hamburger.addEventListener('click', function () {
            navList.classList.toggle('show');
        });
    }

    if (categoryDropdown) {
        const trigger = categoryDropdown.querySelector('[data-category-trigger]');
        const menu = categoryDropdown.querySelector('[data-category-menu]');
        const input = categoryDropdown.querySelector('[data-category-input]');
        const label = categoryDropdown.querySelector('[data-category-label]');
        const options = Array.from(categoryDropdown.querySelectorAll('[data-category-option]'));

        function setExpanded(expanded) {
            if (!trigger || !menu) return;
            trigger.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            menu.hidden = !expanded;
        }

        function closeDropdown() {
            setExpanded(false);
        }

        if (trigger && menu) {
            trigger.addEventListener('click', () => {
                const isExpanded = trigger.getAttribute('aria-expanded') === 'true';
                setExpanded(!isExpanded);
            });
        }

        options.forEach((option) => {
            option.addEventListener('click', () => {
                const value = option.dataset.value || '';
                const optionLabel = option.dataset.label || option.textContent.trim();

                if (input) {
                    input.value = value;
                }
                if (label) {
                    label.textContent = optionLabel;
                }

                options.forEach((item) => {
                    const selected = item === option;
                    item.classList.toggle('is-selected', selected);
                    item.setAttribute('aria-selected', selected ? 'true' : 'false');
                });

                closeDropdown();
            });
        });

        document.addEventListener('click', (event) => {
            if (!categoryDropdown.contains(event.target)) {
                closeDropdown();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                closeDropdown();
                if (trigger) {
                    trigger.focus();
                }
            }
        });
    }

    flashMessages.forEach((flashMessage) => {
        const closeButton = flashMessage.querySelector('.flash-close');
        let dismissTimer = null;

        function dismissFlash() {
            if (flashMessage.classList.contains('is-dismissed')) {
                return;
            }

            if (dismissTimer) {
                window.clearTimeout(dismissTimer);
                dismissTimer = null;
            }

            flashMessage.classList.add('is-dismissed');
            window.setTimeout(() => {
                flashMessage.remove();
            }, 280);
        }

        if (closeButton) {
            closeButton.addEventListener('click', dismissFlash);
        }

        dismissTimer = window.setTimeout(dismissFlash, 4000);

        flashMessage.addEventListener('mouseenter', () => {
            if (dismissTimer) {
                window.clearTimeout(dismissTimer);
                dismissTimer = null;
            }
        });

        flashMessage.addEventListener('mouseleave', () => {
            if (!dismissTimer && !flashMessage.classList.contains('is-dismissed')) {
                dismissTimer = window.setTimeout(dismissFlash, 1500);
            }
        });
    });

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

    function getBookingDaysField() {
        return form ? form.querySelector('[name="booking_days"]') : null;
    }

    function getStudentField() {
        return form ? form.querySelector('[name="is_student"]') : null;
    }

    function syncStudentDisclaimerToggle(checkbox) {
        if (!checkbox) return;

        const formGroup = checkbox.closest('.form-group');
        const disclaimer = formGroup ? formGroup.querySelector('[data-student-disclaimer]') : null;
        if (disclaimer) {
            disclaimer.hidden = !checkbox.checked;
        }
    }

    function initStudentDisclaimerToggles() {
        document.querySelectorAll('input[data-student-disclaimer-checkbox]').forEach((checkbox) => {
            syncStudentDisclaimerToggle(checkbox);
            checkbox.addEventListener('change', function () {
                syncStudentDisclaimerToggle(this);
            });
        });
    }

    function readFormEventData() {
        if (!form || !form.dataset.eventName) {
            return null;
        }

        return {
            price: form.dataset.price || '0.00',
            name: form.dataset.eventName || '',
            date: form.dataset.eventDate || '',
            startDate: form.dataset.eventStartDate || '',
            endDate: form.dataset.eventEndDate || '',
            duration: form.dataset.eventDuration || '1',
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
            startDate: option.dataset.eventStartDate || '',
            endDate: option.dataset.eventEndDate || '',
            duration: option.dataset.eventDuration || '1',
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

    function applyBookingDayOptions(maxDays, preserveValue) {
        const bookingDaysField = getBookingDaysField();
        if (!bookingDaysField) return;

        const max = Math.max(0, Number(maxDays) || 0);
        const selectedValue = preserveValue && Number(preserveValue) <= max ? String(preserveValue) : '1';

        bookingDaysField.innerHTML = '';

        if (max < 1) {
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'Select an event first';
            bookingDaysField.appendChild(placeholder);
            bookingDaysField.disabled = true;
            bookingDaysField.value = '';
            return;
        }

        bookingDaysField.disabled = false;
        for (let i = 1; i <= max; i += 1) {
            const option = document.createElement('option');
            option.value = String(i);
            option.textContent = String(i);
            if (String(i) === selectedValue) {
                option.selected = true;
            }
            bookingDaysField.appendChild(option);
        }
        bookingDaysField.value = selectedValue;
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
            form.dataset.eventStartDate = '';
            form.dataset.eventEndDate = '';
            form.dataset.eventDuration = '1';
            form.dataset.venueName = '';
            form.dataset.remainingSeats = '';
            form.dataset.maxTickets = '0';
            applyTicketOptions(0, '');
            applyBookingDayOptions(0, '');
            syncEventSummary(null);
            return;
        }

        form.dataset.price = eventData.price || '0.00';
        form.dataset.eventName = eventData.name || '';
        form.dataset.eventDate = eventData.date || '';
        form.dataset.eventStartDate = eventData.startDate || '';
        form.dataset.eventEndDate = eventData.endDate || '';
        form.dataset.eventDuration = eventData.duration || '1';
        form.dataset.venueName = eventData.venue || '';
        form.dataset.remainingSeats = eventData.remainingSeats || '';
        form.dataset.maxTickets = String(eventData.maxTickets || 0);

        const ticketsField = getTicketsField();
        const currentTickets = ticketsField && ticketsField.value ? ticketsField.value : '1';
        const bookingDaysField = getBookingDaysField();
        const currentBookingDays = bookingDaysField && bookingDaysField.value ? bookingDaysField.value : '1';
        applyTicketOptions(eventData.maxTickets || 0, currentTickets);
        applyBookingDayOptions(eventData.duration || 1, currentBookingDays);
        syncEventSummary(eventData);
    }

    function advanceDiscountRateForDays(daysBeforeEvent) {
        if (!Number.isFinite(daysBeforeEvent)) return 0;
        if (daysBeforeEvent >= 50 && daysBeforeEvent <= 60) return 0.2;
        if (daysBeforeEvent >= 35 && daysBeforeEvent < 50) return 0.15;
        if (daysBeforeEvent >= 25 && daysBeforeEvent < 35) return 0.1;
        if (daysBeforeEvent >= 15 && daysBeforeEvent < 25) return 0.05;
        return 0;
    }

    function updateBookingTotals() {
        if (!form) return;

        const basePrice = Number(form.dataset.price || '0');
        const eventDuration = Math.max(1, Number(form.dataset.eventDuration || '1') || 1);
        const ticketsField = getTicketsField();
        const rawTickets = ticketsField && ticketsField.value ? parseInt(ticketsField.value, 10) : 0;
        const tickets = Number.isFinite(rawTickets) && rawTickets > 0 ? rawTickets : 0;
        const bookingDaysField = getBookingDaysField();
        const rawBookingDays = bookingDaysField && bookingDaysField.value ? parseInt(bookingDaysField.value, 10) : 0;
        const bookingDays = Number.isFinite(rawBookingDays) && rawBookingDays > 0 ? rawBookingDays : 0;
        const isStudentField = getStudentField();
        const isStudent = Boolean(isStudentField && isStudentField.checked);
        syncStudentDisclaimerToggle(isStudentField);
        const eventStartDate = form.dataset.eventStartDate || '';
        const bookingDateText = form.dataset.bookingDatetime || '';
        const perDayPrice = eventDuration > 1 ? basePrice / eventDuration : basePrice;
        const subtotal = perDayPrice * tickets * bookingDays;
        const studentDiscount = isStudent ? subtotal * 0.1 : 0;

        let advanceDiscount = 0;
        if (eventStartDate && bookingDateText) {
            const startDate = new Date(`${eventStartDate}T00:00:00`);
            const bookedAt = new Date(bookingDateText);
            if (!Number.isNaN(startDate.getTime()) && !Number.isNaN(bookedAt.getTime())) {
                const diffMs = startDate.getTime() - bookedAt.getTime();
                const daysBeforeEvent = Math.floor(diffMs / (1000 * 60 * 60 * 24));
                advanceDiscount = subtotal * advanceDiscountRateForDays(daysBeforeEvent);
            }
        }

        const total = Math.max(subtotal - studentDiscount - advanceDiscount, 0);

        const summaryTickets = document.getElementById('summaryTickets');
        const summaryBookingDays = document.getElementById('summaryBookingDays');
        const summarySubtotal = document.getElementById('summarySubtotal');
        const summaryStudentDiscount = document.getElementById('summaryStudentDiscount');
        const summaryAdvanceDiscount = document.getElementById('summaryAdvanceDiscount');
        const summaryTotal = document.getElementById('summaryTotal');
        const modalTotal = document.getElementById('modalTotal');

        if (summaryTickets) summaryTickets.textContent = String(tickets);
        if (summaryBookingDays) summaryBookingDays.textContent = String(bookingDays);
        if (summarySubtotal) summarySubtotal.textContent = formatMoney(subtotal);
        if (summaryStudentDiscount) summaryStudentDiscount.textContent = formatMoney(studentDiscount);
        if (summaryAdvanceDiscount) summaryAdvanceDiscount.textContent = formatMoney(advanceDiscount);
        if (summaryTotal) summaryTotal.textContent = formatMoney(total);
        if (modalTotal) modalTotal.textContent = formatMoney(total);
    }

    function validateForm(showErrors = true) {
        if (!form) return false;
        let isValid = true;
        const requiredFields = form.querySelectorAll('[required]');
        const maxTickets = Number(form.dataset.maxTickets || '10') || 10;
        const maxBookingDays = Number(form.dataset.eventDuration || '1') || 1;

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

            if (field.name === 'booking_days' && field.value.trim()) {
                const bookingDays = parseInt(field.value, 10);
                if (bookingDays < 1 || bookingDays > maxBookingDays) {
                    if (showErrors && formGroup) formGroup.classList.add('error');
                    isValid = false;
                }
            }
        });

        return isValid;
    }

    initStudentDisclaimerToggles();

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

    const paymentDetailsForm = document.getElementById('paymentDetailsForm');
    const paymentMethodSelect = document.getElementById('paymentMethodSelect');
    const paymentMethodValue = document.getElementById('paymentMethodValue');
    const selectedPaymentMethodLabel = document.getElementById('selectedPaymentMethodLabel');
    const paymentMethodPanels = paymentDetailsForm
        ? Array.from(paymentDetailsForm.querySelectorAll('[data-method-panel]'))
        : [];
    const paymentMethodRequiredFields = {
        card: ['card_name', 'card_number', 'card_expiry', 'card_cvv'],
        paypal: ['paypal_email', 'paypal_password'],
        bank: ['bank_holder', 'bank_account_number', 'bank_sort_code_or_iban'],
    };

    function syncPaymentPanels(method) {
        if (!paymentDetailsForm || !paymentMethodValue) {
            return;
        }

        const activeMethod = paymentMethodRequiredFields[method] ? method : 'card';
        paymentMethodValue.value = activeMethod;

        if (paymentMethodSelect && paymentMethodSelect.value !== activeMethod) {
            paymentMethodSelect.value = activeMethod;
        }

        if (selectedPaymentMethodLabel && paymentMethodSelect) {
            const selectedOption = paymentMethodSelect.options[paymentMethodSelect.selectedIndex];
            selectedPaymentMethodLabel.textContent = selectedOption ? selectedOption.textContent : activeMethod;
        }

        paymentMethodPanels.forEach((panel) => {
            const isActive = panel.dataset.methodPanel === activeMethod;
            panel.hidden = !isActive;

            panel.querySelectorAll('input, select, textarea').forEach((field) => {
                if (field === paymentMethodSelect || field === paymentMethodValue) {
                    return;
                }

                field.disabled = !isActive;

                const requiredNames = paymentMethodRequiredFields[activeMethod] || [];
                if (isActive && requiredNames.includes(field.name)) {
                    field.required = true;
                } else {
                    field.required = false;
                }
            });
        });
    }

    if (paymentDetailsForm && paymentMethodSelect && paymentMethodValue) {
        syncPaymentPanels(paymentMethodSelect.value || paymentMethodValue.value || 'card');

        paymentMethodSelect.addEventListener('change', function () {
            syncPaymentPanels(this.value);
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
