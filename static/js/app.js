/* ============================================================================
   Flavoria Kitchen & Bar — shared front-end logic
   Used by BOTH menu.html and cart.html (loaded as a plain static file).

   Because this is a static asset, it never passes through Django's
   template engine — so it can't contain {% url %} or {{ table_number }}
   tags. Instead, each template exposes what this file needs as
   data-* attributes on <body>, which we read once at the top.

   The cart itself lives in localStorage (shared across both pages,
   since they load this same file). Nothing touches the database
   until the customer taps "Confirm Order" on the cart page — that
   single request creates the Order + OrderItem rows server-side.
============================================================================ */


(function() {
    "use strict";

    /* --------------------------------------------------------------------
       PAGE CONFIG — read once from <body data-...> (set by each template)
    -------------------------------------------------------------------- */
    var PAGE = {
        tableNumber: document.body.dataset.tableNumber || '',
        menuUrl: document.body.dataset.menuUrl || '#',
        orderTrackingUrl: document.body.dataset.orderTrackingUrl || '#',
        placeOrderUrl: document.body.dataset.placeOrderUrl || '#',
    };

    var CART_KEY = 'flavoria_cart_items_v1';
    var FAV_KEY = 'flavoria_favorites';
    var LANG_KEY = 'flavoria_selected_language';
    var NOTE_KEY = 'flavoria_order_note'; /* NEW: special instructions key */

    var ICON_MAP = {
        pizza: 'fa-pizza-slice',
        burger: 'fa-burger',
        momo: 'fa-bowl-rice',
        coffee: 'fa-mug-saucer',
    };

    /* --------------------------------------------------------------------
       HELPERS
    -------------------------------------------------------------------- */
    function formatRs(amount) {
        return 'Rs. ' + (Number(amount) || 0).toLocaleString('en-IN');
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function getCookie(name) {
        var match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    /* --------------------------------------------------------------------
       TOAST — shared #toast / #toastMsg markup on both pages
    -------------------------------------------------------------------- */
    var toastEl = document.getElementById('toast');
    var toastMsgEl = document.getElementById('toastMsg');
    var toastTimer = null;

    function showToast(message) {
        if (!toastEl) return;
        if (toastMsgEl) {
            toastMsgEl.textContent = message;
        } else {
            toastEl.textContent = message;
        }
        toastEl.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function() {
            toastEl.classList.remove('show');
        }, 2200);
    }

    /* --------------------------------------------------------------------
       RIPPLE — delegated so it works on dynamically created buttons too
    -------------------------------------------------------------------- */
    document.addEventListener('click', function(e) {
        var btn = e.target.closest('.ripple');
        if (!btn) return;

        var rect = btn.getBoundingClientRect();
        var diameter = Math.max(rect.width, rect.height);
        var circle = document.createElement('span');
        circle.className = 'ripple-effect';
        circle.style.width = circle.style.height = diameter + 'px';
        circle.style.left = (e.clientX - rect.left - diameter / 2) + 'px';
        circle.style.top = (e.clientY - rect.top - diameter / 2) + 'px';
        circle.style.background = btn.dataset.ripple === 'dark' ?
            'rgba(239,111,31,.20)' :
            'rgba(255,255,255,.45)';

        var old = btn.querySelector('.ripple-effect');
        if (old) old.remove();

        var computed = getComputedStyle(btn);
        if (computed.position === 'static') btn.style.position = 'relative';
        btn.style.overflow = 'hidden';

        btn.appendChild(circle);
        setTimeout(function() { circle.remove(); }, 600);
    });

    /* --------------------------------------------------------------------
       CART STORE — localStorage, shared between menu.html and cart.html
    -------------------------------------------------------------------- */
    var Cart = {
        read: function() {
            try {
                var raw = JSON.parse(localStorage.getItem(CART_KEY));
                return Array.isArray(raw) ? raw : [];
            } catch (e) {
                return [];
            }
        },

        write: function(items) {
            localStorage.setItem(CART_KEY, JSON.stringify(items));
            document.dispatchEvent(new CustomEvent('cart:changed', { detail: { items: items } }));
            // Also refresh badge immediately
            refreshCartBadge();
        },

        count: function() {
            return this.read().reduce(function(sum, it) { return sum + it.quantity; }, 0);
        },

        subtotal: function() {
            return this.read().reduce(function(sum, it) { return sum + it.price * it.quantity; }, 0);
        },

        // food: { id, name, price, note, icon }
        add: function(food) {
            var items = this.read();
            var note = food.note || '';
            var existing = items.find(function(it) {
                return String(it.id) === String(food.id) && (it.note || '') === note;
            });

            if (existing) {
                existing.quantity += 1;
            } else {
                items.push({
                    id: food.id,
                    name: food.name,
                    price: Number(food.price) || 0,
                    quantity: 1,
                    note: note,
                    icon: food.icon || 'fa-utensils',
                });
            }
            this.write(items);
        },

        setQuantity: function(id, quantity) {
            var items = this.read();
            if (quantity <= 0) {
                items = items.filter(function(it) { return String(it.id) !== String(id); });
            } else {
                var it = items.find(function(it) { return String(it.id) === String(id); });
                if (it) it.quantity = quantity;
            }
            this.write(items);
        },

        remove: function(id) {
            var items = this.read().filter(function(it) { return String(it.id) !== String(id); });
            this.write(items);
        },

        clear: function() {
            this.write([]);
        },
    };

    window.FlavoriaCart = Cart;

    /* --------------------------------------------------------------------
       CART BADGE — present on menu.html's bottom nav AND header
    -------------------------------------------------------------------- */
    function refreshCartBadge() {
        var count = Cart.count();
        var badges = document.querySelectorAll('.cart-badge');

        badges.forEach(function(badge) {
            var currentText = badge.textContent;
            badge.textContent = count;

            // Only animate if the count changed
            if (currentText !== String(count)) {
                badge.classList.add('bump');
                setTimeout(function() {
                    badge.classList.remove('bump');
                }, 220);
            }
        });
    }

    // Listen for cart changes
    document.addEventListener('cart:changed', function() {
        refreshCartBadge();
    });

    /* ======================================================================
       MENU PAGE — only runs if .food-card elements exist on this page
    ====================================================================== */
    function initMenuPage() {
        var foodCards = Array.prototype.slice.call(document.querySelectorAll('.food-card'));
        if (!foodCards.length) return;

        var emptyState = document.getElementById('emptyState');
        var favorites = [];
        try { favorites = JSON.parse(localStorage.getItem(FAV_KEY)) || []; } catch (e) { favorites = []; }

        // Initial badge refresh
        refreshCartBadge();

        function syncFavorites() {
            foodCards.forEach(function(card) {
                var id = card.dataset.id;
                var btn = card.querySelector('.wishlist-btn');
                if (!btn) return;
                var icon = btn.querySelector('i');
                var isFav = favorites.indexOf(id) !== -1;
                btn.classList.toggle('active', isFav);
                btn.setAttribute('aria-pressed', String(isFav));
                icon.className = isFav ? 'fa-solid fa-heart' : 'fa-regular fa-heart';
            });
        }
        syncFavorites();

        /* ---- entrance animation ---- */
        function revealVisibleCards() {
            var i = 0;
            foodCards.forEach(function(card) {
                if (card.classList.contains('hidden-by-filter')) return;
                card.classList.remove('is-visible');
                setTimeout(function() { card.classList.add('is-visible'); }, 80 + i * 90);
                i++;
            });
        }
        revealVisibleCards();

        /* ---- search + category filter ---- */
        var searchInput = document.getElementById('searchInput');
        var categoryButtons = Array.prototype.slice.call(document.querySelectorAll('.category-item'));
        var activeCategory = 'all';

        function applyFilters() {
            var query = (searchInput ? searchInput.value : '').trim().toLowerCase();
            var visibleCount = 0;

            foodCards.forEach(function(card) {
                var matchesCategory = activeCategory === 'all' || card.dataset.category === activeCategory;
                var matchesSearch = (card.dataset.name || '').toLowerCase().indexOf(query) !== -1;
                var visible = matchesCategory && matchesSearch;
                card.classList.toggle('hidden-by-filter', !visible);
                if (visible) visibleCount++;
            });

            if (emptyState) emptyState.classList.toggle('show', visibleCount === 0);
            revealVisibleCards();
        }

        if (searchInput) searchInput.addEventListener('input', applyFilters);

        categoryButtons.forEach(function(btn) {
            btn.addEventListener('click', function() {
                categoryButtons.forEach(function(b) { b.classList.remove('active'); });
                btn.classList.add('active');
                activeCategory = btn.dataset.category;
                btn.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
                applyFilters();
            });
        });

        /* ---- add to cart ---- */
        function addToCart(card, btn) {
            Cart.add({
                id: card.dataset.id,
                name: card.dataset.name,
                price: card.dataset.price,
                icon: ICON_MAP[card.dataset.category] || 'fa-utensils',
            });
            showToast(card.dataset.name + ' added to cart');

            if (btn) {
                btn.classList.add('added');
                var original = btn.innerHTML;
                btn.innerHTML = '<i class="fa-solid fa-check" aria-hidden="true"></i> Added';
                setTimeout(function() {
                    btn.innerHTML = original;
                    btn.classList.remove('added');
                }, 1100);
            }
        }

        document.querySelectorAll('.btn-add').forEach(function(btn) {
            btn.addEventListener('click', function() {
                addToCart(btn.closest('.food-card'), btn);
            });
        });

        /* ---- wishlist toggle ---- */
        document.querySelectorAll('.wishlist-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var card = btn.closest('.food-card');
                var id = card.dataset.id;
                var icon = btn.querySelector('i');
                var isFav = favorites.indexOf(id) !== -1;

                if (isFav) {
                    favorites = favorites.filter(function(f) { return f !== id; });
                    btn.classList.remove('active');
                    btn.setAttribute('aria-pressed', 'false');
                    icon.className = 'fa-regular fa-heart';
                } else {
                    favorites.push(id);
                    btn.classList.add('active');
                    btn.setAttribute('aria-pressed', 'true');
                    icon.className = 'fa-solid fa-heart';
                    showToast(card.dataset.name + ' saved to wishlist');
                }
                localStorage.setItem(FAV_KEY, JSON.stringify(favorites));
            });
        });

        /* ---- view details modal ---- */
        var modalOverlay = document.getElementById('modalOverlay');
        var modalTitle = document.getElementById('modalTitle');
        var modalMeta = document.getElementById('modalMeta');
        var modalDesc = document.getElementById('modalDesc');
        var modalPrice = document.getElementById('modalPrice');
        var modalImage = document.getElementById('modalImage');
        var modalAddBtn = document.getElementById('modalAddBtn');
        var currentModalCard = null;

        function openFoodModal(card) {
            if (!modalOverlay) return;
            currentModalCard = card;
            modalTitle.textContent = card.dataset.name;
            modalDesc.textContent = card.dataset.desc;
            modalPrice.textContent = formatRs(card.dataset.price);
            modalMeta.innerHTML =
                '<span>' + escapeHtml(card.dataset.rating) + '</span>' +
                '<span class="stars">' +
                '<i class="fa-solid fa-star"></i><i class="fa-solid fa-star"></i>' +
                '<i class="fa-solid fa-star"></i><i class="fa-solid fa-star"></i>' +
                '<i class="fa-solid fa-star"></i>' +
                '</span>' +
                '<span class="reviews">(' + escapeHtml(card.dataset.reviews) + ')</span>' +
                '<span class="meta-sep">|</span>' +
                '<span class="duration"><i class="fa-regular fa-clock" aria-hidden="true"></i> ' +
                escapeHtml(card.dataset.duration) + '</span>';
            var iconClass = ICON_MAP[card.dataset.category] || 'fa-utensils';
            modalImage.innerHTML = '<i class="fa-solid ' + iconClass + '"></i>';

            modalOverlay.classList.add('open');
            document.body.style.overflow = 'hidden';
        }

        function closeFoodModal() {
            if (!modalOverlay) return;
            modalOverlay.classList.remove('open');
            document.body.style.overflow = '';
            currentModalCard = null;
        }

        document.querySelectorAll('.btn-view').forEach(function(btn) {
            btn.addEventListener('click', function() { openFoodModal(btn.closest('.food-card')); });
        });

        if (modalOverlay) {
            var modalCloseBtn = document.getElementById('modalClose');
            if (modalCloseBtn) modalCloseBtn.addEventListener('click', closeFoodModal);
            modalOverlay.addEventListener('click', function(e) {
                if (e.target === modalOverlay) closeFoodModal();
            });
        }

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && modalOverlay && modalOverlay.classList.contains('open')) closeFoodModal();
        });

        if (modalAddBtn) {
            modalAddBtn.addEventListener('click', function() {
                if (currentModalCard) addToCart(currentModalCard, null);
                closeFoodModal();
            });
        }

        /* ---- bottom nav ---- */
        document.querySelectorAll('.nav-item').forEach(function(btn) {
            btn.addEventListener('click', function() {
                document.querySelectorAll('.nav-item').forEach(function(b) { b.classList.remove('active'); });
                btn.classList.add('active');
            });
        });

        var ordersBtn = document.getElementById('ordersBtn');
        if (ordersBtn) {
            ordersBtn.addEventListener('click', function() {
                window.location.href = PAGE.orderTrackingUrl;
            });
        }

        var cartBtn = document.getElementById('cartBtn');
        if (cartBtn) {
            cartBtn.addEventListener('click', function() {
                window.location.href = document.body.dataset.cartUrl || '#';
            });
        }

        /* ---- language selector ---- */
        var langWrap = document.getElementById('langWrap');
        var langBtn = document.getElementById('langBtn');
        var langLabel = document.getElementById('langLabel');

        if (langWrap && langBtn && langLabel) {
            var savedLang = localStorage.getItem(LANG_KEY);
            if (savedLang) {
                var match = document.querySelector('.lang-option[data-lang="' + savedLang + '"]');
                if (match) {
                    document.querySelectorAll('.lang-option').forEach(function(o) { o.classList.remove('active'); });
                    match.classList.add('active');
                    langLabel.textContent = match.dataset.label;
                }
            }

            langBtn.addEventListener('click', function() {
                var isOpen = langWrap.classList.toggle('open');
                langBtn.setAttribute('aria-expanded', String(isOpen));
            });

            document.querySelectorAll('.lang-option').forEach(function(opt) {
                opt.addEventListener('click', function() {
                    document.querySelectorAll('.lang-option').forEach(function(o) { o.classList.remove('active'); });
                    opt.classList.add('active');
                    langLabel.textContent = opt.dataset.label;
                    localStorage.setItem(LANG_KEY, opt.dataset.lang);
                    langWrap.classList.remove('open');
                    langBtn.setAttribute('aria-expanded', 'false');
                });
            });

            document.addEventListener('click', function(e) {
                if (!langWrap.contains(e.target)) langWrap.classList.remove('open');
            });
        }

        /* ---- filter button (placeholder) ---- */
        var filterBtn = document.getElementById('filterBtn');
        if (filterBtn) {
            filterBtn.addEventListener('click', function() { showToast('Filters coming soon'); });
        }
    }

    /* ======================================================================
       SPECIAL INSTRUCTIONS HANDLER
       NEW: Manages the textarea in the order confirmation modal.
            - Live character counter with colour feedback
            - Auto-resize textarea to content
            - Persists to localStorage on blur
            - Restores saved value when modal opens
    ====================================================================== */
    function SpecialInstructions() {
        this.textarea = document.getElementById('specialInstructions');
        this.counter = document.getElementById('charCount');
        this.maxLength = 500;
        if (this.textarea) this._init();
    }

    SpecialInstructions.prototype._init = function() {
        var self = this;

        /* Live counter + auto-resize on every keystroke */
        self.textarea.addEventListener('input', function() {
            self._updateCounter();
            self._autoResize();
        });

        /* Save to localStorage when focus leaves the field */
        self.textarea.addEventListener('blur', function() {
            self._saveToStorage();
        });
    };

    SpecialInstructions.prototype._updateCounter = function() {
        var used = this.textarea.value.length;
        var remaining = this.maxLength - used;

        if (this.counter) {
            this.counter.textContent = used + '/' + this.maxLength;
            this.counter.classList.remove('warning', 'danger');
            if (remaining < 20) {
                this.counter.classList.add('danger');
            } else if (remaining < 50) {
                this.counter.classList.add('warning');
            }
        }
    };

    SpecialInstructions.prototype._autoResize = function() {
        this.textarea.style.height = 'auto';
        this.textarea.style.height = this.textarea.scrollHeight + 'px';
    };

    SpecialInstructions.prototype._saveToStorage = function() {
        try {
            localStorage.setItem(NOTE_KEY, this.textarea.value);
        } catch (e) {
            /* quota exceeded or private mode — silently ignore */
        }
    };

    /* Call this when opening the order modal to restore any saved text */
    SpecialInstructions.prototype.restore = function() {
        if (!this.textarea) return;
        try {
            var saved = localStorage.getItem(NOTE_KEY);
            if (saved) {
                this.textarea.value = saved;
                this._updateCounter();
                this._autoResize();
            }
        } catch (e) { /* ignore */ }
    };

    /* Returns trimmed instructions string (used by submitOrder) */
    SpecialInstructions.prototype.getValue = function() {
        return this.textarea ? this.textarea.value.trim() : '';
    };

    /* Wipe text + storage after a successful order */
    SpecialInstructions.prototype.clear = function() {
        if (!this.textarea) return;
        this.textarea.value = '';
        this._updateCounter();
        this.textarea.style.height = '';
        try { localStorage.removeItem(NOTE_KEY); } catch (e) { /* ignore */ }
    };

    /* ======================================================================
       CART PAGE — only runs if #cartList exists on this page
    ====================================================================== */
    function initCartPage() {
        var cartList = document.getElementById('cartList');
        if (!cartList) return;

        var statusTitle = document.getElementById('statusTitle');
        var statusCard = document.querySelector('.status-card');
        var emptyCart = document.getElementById('emptyCart');
        var cartGrid = document.getElementById('cartGrid');
        var summaryCard = document.getElementById('summaryCard');
        var stickyBar = document.getElementById('stickyBar');

        var detailsOverlay = document.getElementById('detailsModalOverlay');
        var orderOverlay = document.getElementById('orderModalOverlay');
        var orderModalIcon = document.getElementById('orderModalIcon');
        var orderModalTitle = document.getElementById('orderModalTitle');
        var orderModalSub = document.getElementById('orderModalSub');
        var confirmBtn = document.getElementById('orderModalConfirmBtn');

        /* NEW: initialise special instructions handler */
        var specialInstructions = new SpecialInstructions();

        var hasRenderedOnce = false;

        /* ---- building markup for one cart item ---- */
        function buildCartItemHTML(item) {
            var lineTotal = item.price * item.quantity;
            var noteHtml = item.note ?
                '<span class="item-note">' +
                '<i class="fa-solid fa-pen" aria-hidden="true"></i>' +
                '<strong>Note:</strong>' +
                '<span class="note-text">' + escapeHtml(item.note) + '</span>' +
                '</span>' :
                '<span></span>';

            return (
                '<article class="cart-item" data-id="' + escapeHtml(item.id) + '">' +
                '<div class="item-main">' +
                '<div class="item-image" aria-hidden="true">' +
                '<i class="fa-solid ' + (item.icon || 'fa-utensils') + '"></i>' +
                '</div>' +
                '<div class="item-body">' +
                '<div class="item-body-top">' +
                '<div class="item-title-desc">' +
                '<h3 class="item-title">' + escapeHtml(item.name) + '</h3>' +
                '</div>' +
                '<div class="item-price-delete">' +
                '<span class="item-price">' + formatRs(lineTotal) + '</span>' +
                '<button type="button" class="delete-btn ripple" data-ripple="dark" data-action="remove" ' +
                'aria-label="Remove ' + escapeHtml(item.name) + ' from cart">' +
                '<i class="fa-solid fa-trash-can" aria-hidden="true"></i>' +
                '</button>' +
                '</div>' +
                '</div>' +
                '<div class="item-footer-row">' +
                noteHtml +
                '<div class="qty-stepper">' +
                '<button type="button" class="qty-btn qty-minus" data-action="minus" aria-label="Decrease quantity">' +
                '<i class="fa-solid fa-minus"></i>' +
                '</button>' +
                '<span class="qty-value">' + item.quantity + '</span>' +
                '<button type="button" class="qty-btn qty-plus" data-action="plus" aria-label="Increase quantity">' +
                '<i class="fa-solid fa-plus"></i>' +
                '</button>' +
                '</div>' +
                '</div>' +
                '</div>' +
                '</div>' +
                '</article>'
            );
        }

        function buildModalRowsHTML(items) {
            return items.map(function(item) {
                return (
                    '<div class="modal-list-row">' +
                    '<span class="name">' + escapeHtml(item.name) +
                    ' <span class="qty-tag">x' + item.quantity + '</span>' +
                    '</span>' +
                    '<span class="amt">' + formatRs(item.price * item.quantity) + '</span>' +
                    '</div>'
                );
            }).join('');
        }

        /* ---- main render: rebuild everything from Cart.read() ---- */
        function renderCart() {
            var items = Cart.read();
            var subtotal = Cart.subtotal();
            var isEmpty = items.length === 0;

            cartList.innerHTML = items.map(buildCartItemHTML).join('');

            var formatted = formatRs(subtotal);
            var subtotalEl = document.getElementById('subtotalValue');
            var totalEl = document.getElementById('totalValue');
            var stickyTotalEl = document.getElementById('stickyTotal');
            if (subtotalEl) subtotalEl.textContent = formatted;
            if (totalEl) totalEl.textContent = formatted;
            if (stickyTotalEl) stickyTotalEl.textContent = formatted;

            if (statusTitle) {
                var count = items.reduce(function(s, it) { return s + it.quantity; }, 0);
                statusTitle.textContent = isEmpty ?
                    'Your cart is empty' :
                    'You have ' + count + ' item' + (count === 1 ? '' : 's') + ' in your cart';
            }

            if (emptyCart) emptyCart.classList.toggle('show', isEmpty);
            cartList.style.display = isEmpty ? 'none' : 'flex';
            if (statusCard) statusCard.style.display = isEmpty ? 'none' : 'flex';
            if (cartGrid) cartGrid.style.display = isEmpty ? 'none' : '';
            if (summaryCard) summaryCard.style.display = isEmpty ? 'none' : 'block';
            if (stickyBar) stickyBar.classList.toggle('hide', isEmpty);

            /* entrance animation only on first render */
            if (!hasRenderedOnce) {
                var rows = Array.prototype.slice.call(cartList.querySelectorAll('.cart-item'));
                rows.forEach(function(row, i) {
                    setTimeout(function() { row.classList.add('is-visible'); }, 80 + i * 100);
                });
                if (summaryCard) {
                    setTimeout(function() { summaryCard.classList.add('is-visible'); }, 120 + rows.length * 100 + 60);
                }
                hasRenderedOnce = true;
            } else {
                cartList.querySelectorAll('.cart-item').forEach(function(row) {
                    row.classList.add('is-visible');
                });
            }

            // Refresh badge after render
            refreshCartBadge();

            return { items: items, subtotal: subtotal, formatted: formatted };
        }

        document.addEventListener('cart:changed', renderCart);

        /* ---- quantity / remove (event delegation) ---- */
        cartList.addEventListener('click', function(e) {
            var card = e.target.closest('.cart-item');
            if (!card) return;
            var id = card.dataset.id;
            var current = Cart.read().find(function(it) { return String(it.id) === String(id); });
            if (!current) return;

            if (e.target.closest('[data-action="plus"]')) {
                if (current.quantity >= 20) return;
                Cart.setQuantity(id, current.quantity + 1);
            } else if (e.target.closest('[data-action="minus"]')) {
                if (current.quantity <= 1) return;
                Cart.setQuantity(id, current.quantity - 1);
            } else if (e.target.closest('[data-action="remove"]')) {
                card.classList.add('removing');
                setTimeout(function() { Cart.remove(id); }, 280);
            }
        });

        /* ---- modal helpers ---- */
        function openModal(overlay) {
            overlay.classList.add('open');
            document.body.style.overflow = 'hidden';
        }

        function closeModal(overlay) {
            overlay.classList.remove('open');
            document.body.style.overflow = '';
        }

        /* ---- view details modal ---- */
        var viewDetailsBtn = document.getElementById('viewDetailsBtn');
        if (viewDetailsBtn && detailsOverlay) {
            viewDetailsBtn.addEventListener('click', function() {
                var items = Cart.read();
                document.getElementById('detailsModalList').innerHTML = buildModalRowsHTML(items);
                document.getElementById('detailsModalTotal').textContent = formatRs(Cart.subtotal());
                openModal(detailsOverlay);
            });

            var detailsCloseBtn = document.getElementById('detailsModalClose');
            var detailsCloseBtn2 = document.getElementById('detailsModalCloseBtn');
            if (detailsCloseBtn) detailsCloseBtn.addEventListener('click', function() { closeModal(detailsOverlay); });
            if (detailsCloseBtn2) detailsCloseBtn2.addEventListener('click', function() { closeModal(detailsOverlay); });
            detailsOverlay.addEventListener('click', function(e) {
                if (e.target === detailsOverlay) closeModal(detailsOverlay);
            });
        }

        /* ---- place order: confirm → submit → success → redirect ---- */
        function resetOrderModalToConfirmState() {
            orderModalTitle.textContent = 'Confirm Your Order';
            orderModalSub.textContent =
                'Review your order before sending it to the kitchen for Table ' + PAGE.tableNumber + '.';
            orderModalIcon.classList.add('is-pending');
            orderModalIcon.innerHTML = '<i class="fa-solid fa-receipt"></i>';
            confirmBtn.innerHTML = '<i class="fa-solid fa-check-circle" aria-hidden="true"></i> Confirm Order';
            confirmBtn.disabled = false;
        }

        function showOrderSuccess(orderNumber) {
            orderModalTitle.textContent = 'Order Placed Successfully!';
            orderModalSub.textContent =
                'Order #' + orderNumber + ' has been sent to the kitchen for Table ' +
                PAGE.tableNumber + '. Taking you back to the menu…';
            orderModalIcon.classList.remove('is-pending');
            orderModalIcon.innerHTML = '<i class="fa-solid fa-check"></i>';
            confirmBtn.innerHTML = 'Done';
            confirmBtn.disabled = true;

            /* Clear cart AND special instructions */
            Cart.clear();
            specialInstructions.clear();

            // Refresh badge after clearing
            refreshCartBadge();

            setTimeout(function() {
                window.location.href = PAGE.menuUrl;
            }, 1800);
        }

        /* NEW: submitOrder sends the special instructions note to the backend.
           The Django view reads it as payload.get('note') → order.customer_note */
        function submitOrder() {
            var items = Cart.read();
            if (!items.length) return;

            var note = specialInstructions.getValue(); /* NEW */

            confirmBtn.disabled = true;
            confirmBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Placing order…';

            fetch(PAGE.placeOrderUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken'),
                    },
                    body: JSON.stringify({
                        items: items.map(function(it) {
                            return {
                                food_id: it.id,
                                quantity: it.quantity,
                                note: it.note || '',
                            };
                        }),
                        note: note,
                        /* NEW: whole-order special instructions → customer_note in Django */
                    }),
                })
                .then(function(res) {
                    return res.json().then(function(data) { return { ok: res.ok, data: data }; });
                })
                .then(function(result) {
                    if (result.ok && result.data && result.data.success) {
                        showOrderSuccess(result.data.order_number);
                    } else {
                        confirmBtn.disabled = false;
                        confirmBtn.innerHTML = '<i class="fa-solid fa-check-circle" aria-hidden="true"></i> Confirm Order';
                        showToast((result.data && result.data.error) || 'Could not place your order. Please try again.');
                    }
                })
                .catch(function() {
                    confirmBtn.disabled = false;
                    confirmBtn.innerHTML = '<i class="fa-solid fa-check-circle" aria-hidden="true"></i> Confirm Order';
                    showToast('Network error — please check your connection and try again.');
                });
        }

        /* ---- open order modal: populate items + restore saved instructions ---- */
        var placeOrderBtn = document.getElementById('placeOrderBtn');
        if (placeOrderBtn && orderOverlay) {
            placeOrderBtn.addEventListener('click', function() {
                var items = Cart.read();
                if (!items.length) return;
                resetOrderModalToConfirmState();
                document.getElementById('orderModalList').innerHTML = buildModalRowsHTML(items);
                document.getElementById('orderModalTotal').textContent = formatRs(Cart.subtotal());

                /* NEW: restore any saved instructions when the modal opens */
                specialInstructions.restore();

                openModal(orderOverlay);

                /* Focus the textarea after the modal slides in */
                setTimeout(function() {
                    var ta = document.getElementById('specialInstructions');
                    if (ta) ta.focus({ preventScroll: true });
                }, 360);
            });

            confirmBtn.addEventListener('click', submitOrder);

            var orderCloseBtn = document.getElementById('orderModalClose');
            if (orderCloseBtn) {
                orderCloseBtn.addEventListener('click', function() {
                    if (!confirmBtn.disabled) {
                        /* Save whatever the customer has typed before closing */
                        specialInstructions._saveToStorage();
                        closeModal(orderOverlay);
                    }
                });
            }
            orderOverlay.addEventListener('click', function(e) {
                if (e.target === orderOverlay && !confirmBtn.disabled) {
                    specialInstructions._saveToStorage();
                    closeModal(orderOverlay);
                }
            });
        }

        document.addEventListener('keydown', function(e) {
            if (e.key !== 'Escape') return;
            if (detailsOverlay && detailsOverlay.classList.contains('open')) closeModal(detailsOverlay);
            if (orderOverlay && orderOverlay.classList.contains('open') && confirmBtn && !confirmBtn.disabled) {
                specialInstructions._saveToStorage();
                closeModal(orderOverlay);
            }
        });

        /* ---- navigation ---- */
        function goToMenu() { window.location.href = PAGE.menuUrl; }

        var backBtn = document.getElementById('backBtn');
        var backToMenuBtn = document.getElementById('backToMenuBtn');
        var emptyBrowseBtn = document.getElementById('emptyBrowseBtn');
        if (backBtn) backBtn.addEventListener('click', goToMenu);
        if (backToMenuBtn) backToMenuBtn.addEventListener('click', goToMenu);
        if (emptyBrowseBtn) emptyBrowseBtn.addEventListener('click', goToMenu);

        /* ---- init ---- */
        renderCart();
    }

    /* ======================================================================
       AUTO-HIDE HEADER (menu page only)
    ====================================================================== */
    function initAutoHideHeader() {
        var topShell = document.getElementById('topShell');
        if (!topShell) return;

        var lastScrollY = window.scrollY;
        var ticking = false;
        var headerHeight = topShell.offsetHeight;

        function updateHeader() {
            var currentScrollY = window.scrollY;

            // Only hide if we've scrolled past the header height
            if (currentScrollY > headerHeight) {
                if (currentScrollY > lastScrollY) {
                    // Scrolling down - hide header
                    topShell.classList.add('hide-header');
                } else {
                    // Scrolling up - show header
                    topShell.classList.remove('hide-header');
                }
            } else {
                // At top of page - show header
                topShell.classList.remove('hide-header');
            }

            lastScrollY = currentScrollY;
            ticking = false;
        }

        function handleScroll() {
            if (!ticking) {
                window.requestAnimationFrame(function() {
                    updateHeader();
                });
                ticking = true;
            }
        }

        // Update header height on resize
        function updateHeaderHeight() {
            headerHeight = topShell.offsetHeight;
        }

        window.addEventListener('scroll', handleScroll, { passive: true });
        window.addEventListener('resize', updateHeaderHeight, { passive: true });

        // Initial call to set correct state
        updateHeaderHeight();
    }

    /* ======================================================================
       BOOT
    ====================================================================== */
    document.addEventListener('DOMContentLoaded', function() {
        // Initialize all components
        refreshCartBadge();
        initAutoHideHeader();
        initMenuPage();
        initCartPage();
    });

})();