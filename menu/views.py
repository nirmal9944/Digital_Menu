import json

from django.db.models import Max, Min
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone

from .models import (
    RestaurantDetail,
    RestaurantTable,
    TableSession,
    Order,
    OrderItem,
    Category,
    FoodItem,
    Bill,
    VATSetting,
    QuickItem,
    QuickRequest,
)


def landing(request, table_number):

    restaurant = RestaurantDetail.objects.first()

    context = {
        'table_number': table_number,
        'restaurant': restaurant
    }

    return render(request, 'menu/landing.html', context)


def menu_page(request, table_number):

    foods = FoodItem.objects.select_related(
        'category'
    ).prefetch_related(
        'offers'
    ).filter(
        is_available=True
    )

    # Real min/max prep time across today's available dishes, shown as an
    # "Estimated prep time" chip in the header — not a hardcoded guess.
    prep_time_range = foods.aggregate(min_time=Min('preparation_time'), max_time=Max('preparation_time'))

    context = {
        'table_number': table_number,

        'restaurant': RestaurantDetail.objects.first(),

        'categories': Category.objects.filter(
            is_active=True
        ),

        'foods': foods,

        'popular_foods': FoodItem.objects.select_related(
            'category'
        ).filter(
            is_available=True,
            is_popular=True
        ),

        'quick_items': QuickItem.objects.filter(is_active=True),

        'prep_time_min': prep_time_range['min_time'],
        'prep_time_max': prep_time_range['max_time'],
    }

    return render(request, 'menu/menu.html', context)


# ---------------------------------------------------------------------------
# ORDER TRACKING
# ---------------------------------------------------------------------------

# This MUST stay in the same order as Order.STATUS_CHOICES — the index in
# this list is used to drive the 4-step progress bar in the template.
STATUS_STEPS = ['new', 'preparing', 'ready', 'delivered']

STATUS_LABELS = {
    'new': 'Order Received',
    'preparing': 'Preparing',
    'ready': 'Ready',
    'delivered': 'Delivered',
}

STATUS_DESCRIPTIONS = {
    'new': "We've received your order. The kitchen will start shortly.",
    'preparing': "We're preparing your delicious meal.",
    'ready': "Your order is ready and on its way to your table!",
    'delivered': "Enjoy your meal. Thank you for dining with us!",
}


def _build_tracking_data(table_number):
    """
    Single source of truth for the order-tracking state of one table.
    Used by BOTH the page view (first paint) and the JSON polling
    endpoint (live updates), so the two can never drift apart.

    Returns a plain, JSON-serialisable dict.
    """

    data = {
        'state': 'empty',          # empty | active | completed
        'table_number': table_number,
    }

    table = RestaurantTable.objects.filter(
        table_number=table_number
    ).first()

    if not table:
        return data

    # The empty state should ONLY appear when this table has never placed an
    # order at all. If any order exists — even in a closed/past session —
    # we keep showing it instead of falling back to "No Active Orders".
    has_any_order = (
        Order.objects
        .filter(session__table=table)
        .exclude(status='cancelled')
        .exists()
    )

    if not has_any_order:
        return data

    # Prefer the currently active session — but only if it actually has a
    # real order in it. Otherwise, fall back to the most recent session
    # (active or closed) that has orders, so a delivered order keeps
    # showing instead of disappearing once the session is closed.
    session = None

    active_session = TableSession.objects.filter(
        table=table, status='active'
    ).order_by('-started_at').first()

    if active_session and active_session.orders.exclude(status='cancelled').exists():
        session = active_session
    else:
        for candidate in TableSession.objects.filter(table=table).order_by('-started_at'):
            if candidate.orders.exclude(status='cancelled').exists():
                session = candidate
                break

    if not session:
        return data

    orders = (
        session.orders
        .exclude(status='cancelled')
        .order_by('created_at')
    )

    if not orders.exists():
        return data

    items_qs = (
        OrderItem.objects
        .filter(order__session=session)
        .select_related('food', 'food__category', 'order')
        .order_by('order__created_at', 'id')
    )

    if not items_qs.exists():
        return data

    statuses = list(orders.values_list('status', flat=True))

    # Whole-table progress = the order that is furthest BEHIND, since the
    # table isn't "done" until every item placed in this session is delivered.
    overall_status = min(statuses, key=lambda s: STATUS_STEPS.index(s))
    current_step = STATUS_STEPS.index(overall_status)
    all_delivered = all(s == 'delivered' for s in statuses)
    state = 'completed' if all_delivered else 'active'

    # --- estimate time remaining, based on the slowest unfinished order ---
    now = timezone.now()
    remaining_seconds = 0

    if not all_delivered:
        slowest_remaining = 0
        for order in orders.exclude(status='delivered'):
            order_items = list(order.items.select_related('food').filter(is_cancelled=False))
            prep_minutes = max(
                (i.food.preparation_time for i in order_items),
                default=15,
            )
            elapsed = (now - order.created_at).total_seconds()
            remaining = max(prep_minutes * 60 - elapsed, 0)
            slowest_remaining = max(slowest_remaining, remaining)
        remaining_seconds = int(slowest_remaining)

    first_order = orders.first()

    items = []
    subtotal = 0
    for item in items_qs:
        if not item.is_cancelled:
            subtotal += float(item.subtotal)
        items.append({
            'id': item.id,
            'name': item.food.food_name,
            'category': item.food.category.category_name,
            'note': item.special_instruction,
            'quantity': item.quantity,
            'unit_price': float(item.unit_price),
            'subtotal': float(item.subtotal),
            'is_cancelled': item.is_cancelled,
            # Only cancellable while the kitchen hasn't accepted/started the
            # order yet — once any item in the order moves to preparing,
            # the whole order (and every item in it) is locked in.
            'can_cancel': (not item.is_cancelled) and item.order.status == 'new',
        })

    # Quick requests (water, tissue, pickle, cold drink...) — free ones are
    # just shown for visibility, paid ones also count toward the total so
    # this matches exactly what billing will charge once the bill is raised.
    for qr in QuickRequest.objects.filter(session=session).select_related('item').order_by('requested_at'):
        subtotal += float(qr.subtotal)
        items.append({
            'id': f'quick-{qr.id}',
            'name': qr.item.name + (' (Free)' if qr.is_free else ''),
            'category': 'Quick Request',
            'note': '' if qr.status == 'served' else 'On its way',
            'quantity': qr.quantity,
            'unit_price': 0.0 if qr.is_free else float(qr.unit_price),
            'subtotal': float(qr.subtotal),
            'is_cancelled': False,
            'can_cancel': False,
        })

    bill = Bill.objects.filter(session=session).first()
    if bill:
        vat_percent = float(bill.vat_percent)
        discount = float(bill.discount)
    else:
        vat_percent = float(VATSetting.current_percent())
        discount = 0.0

    taxable = max(subtotal - discount, 0)
    tax = round(taxable * vat_percent / 100, 2)
    grand_total = round(subtotal - discount + tax, 2)

    data.update({
        'state': state,
        'table_number': table.table_number,
        'session_id': session.id,
        'order_number': f"ORD-{first_order.id:04d}",
        'placed_at': timezone.localtime(first_order.created_at).strftime('%b %d, %Y at %I:%M %p'),
        'overall_status': overall_status,
        'current_step': current_step,
        'status_label': STATUS_LABELS.get(overall_status, overall_status.title()),
        'status_description': STATUS_DESCRIPTIONS.get(overall_status, ''),
        'remaining_seconds': remaining_seconds,
        'items': items,
        'subtotal': round(subtotal, 2),
        'discount': discount,
        'tax': tax,
        'grand_total': grand_total,
        'bill_requested': bill is not None,
        'bill_id': bill.id if bill else None,
        'payment_status': bill.payment_status if bill else None,
    })
    return data


def order_tracking(request, table_number):
    """
    Renders the order-tracking page for a table. The page itself polls
    `order_status_api` in the background (see order_tracking.html) so it
    reflects live status changes from the kitchen/admin side without the
    customer ever needing to refresh.
    """
    data = _build_tracking_data(table_number)

    context = {
        'table_number': table_number,
        'restaurant': RestaurantDetail.objects.first(),
        'tracking': data,
    }
    return render(request, 'menu/order_tracking.html', context)


def order_status_api(request, table_number):
    """
    JSON endpoint polled every few seconds by order_tracking.html.
    Returns the exact same shape `_build_tracking_data` always returns,
    so the front-end render() function can use it identically whether
    it came from the initial page load or a poll.
    """
    data = _build_tracking_data(table_number)
    return JsonResponse(data)


# ---------------------------------------------------------------------------
# CART  +  PLACE ORDER
# ---------------------------------------------------------------------------
#
# The cart itself is NOT a database table (there's no Cart model) — it
# lives in the browser's localStorage, shared between menu.html and
# cart.html via static/js/app.js. The database is only touched once,
# at the moment the customer taps "Confirm Order": that single request
# creates (or reuses) the table's active TableSession, then an Order +
# OrderItem rows for everything that was in the cart.

def cart_page(request, table_number):
    """
    Renders the cart page shell. cart.html reads the actual cart
    contents out of localStorage via JS — this view just needs to hand
    the template the table number + a couple of URLs so app.js can
    read them off `<body data-...>` (a plain .js file can't use
    Django's {% url %} tag, since static files bypass the template
    engine entirely).
    """
    context = {
        'table_number': table_number,
        'restaurant': RestaurantDetail.objects.first(),
    }
    return render(request, 'menu/cart.html', context)


@require_POST
def place_order(request, table_number):
    """
    Turns the cart the customer built client-side into real DB rows.

    Expects a JSON body like:
        {
          "items": [
            {"food_id": 3, "quantity": 2, "note": "no onions"},
            {"food_id": 7, "quantity": 1, "note": ""}
          ],
          "note": "optional note for the whole order"
        }

    Returns JSON: {"success": true, "order_number": "ORD-0007", ...}
    or            {"success": false, "error": "..."}
    """
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {'success': False, 'error': 'Could not read your order.'},
            status=400,
        )

    cart_items = payload.get('items')
    if not isinstance(cart_items, list) or not cart_items:
        return JsonResponse(
            {'success': False, 'error': 'Your cart is empty.'},
            status=400,
        )

    table = RestaurantTable.objects.filter(table_number=table_number).first()
    if not table:
        return JsonResponse(
            {'success': False, 'error': 'Table not found.'},
            status=404,
        )

    # Reuse the table's current active session (started by a previous
    # order this visit), or open a fresh one if this is the first order.
    session = TableSession.objects.filter(
        table=table, status='active'
    ).order_by('-started_at').first()

    if not session:
        session = TableSession.objects.create(table=table, status='active')
        if table.status != 'occupied':
            table.status = 'occupied'
            table.save(update_fields=['status'])

    order = Order.objects.create(
        session=session,
        status='new',
        customer_note=(payload.get('note') or '')[:255],
    )

    created_any = False
    for entry in cart_items:
        try:
            food_id = int(entry.get('food_id'))
            quantity = max(1, int(entry.get('quantity', 1)))
        except (TypeError, ValueError, AttributeError):
            continue

        food = FoodItem.objects.filter(id=food_id, is_available=True).first()
        if not food:
            continue  # skip items that no longer exist / are unavailable

        OrderItem.objects.create(
            order=order,
            food=food,
            quantity=quantity,
            unit_price=food.effective_price,
            special_instruction=(entry.get('note') or '')[:255],
        )
        created_any = True

    if not created_any:
        order.delete()
        return JsonResponse({
            'success': False,
            'error': 'None of the items in your cart are available right now.',
        }, status=400)

    return JsonResponse({
        'success': True,
        'order_number': f"ORD-{order.id:04d}",
        'order_id': order.id,
        'session_id': session.id,
    })


# ---------------------------------------------------------------------------
# CANCEL ORDER ITEM
# ---------------------------------------------------------------------------
#
# Lets a customer back out of a single food item from order_tracking.html —
# only while the kitchen hasn't accepted the order yet (status == 'new').
# Once any item in the order starts preparing, the whole order (and every
# item in it) is locked in and this is rejected.

@require_POST
def cancel_order_item(request, table_number, item_id):
    item = get_object_or_404(
        OrderItem.objects.select_related('order__session__table', 'food'),
        id=item_id,
    )

    if item.order.session.table.table_number != table_number:
        return JsonResponse({'success': False, 'error': 'Item not found.'}, status=404)

    if item.is_cancelled:
        return JsonResponse({'success': False, 'error': 'This item is already cancelled.'}, status=400)

    if item.order.status != 'new':
        return JsonResponse({
            'success': False,
            'error': 'The kitchen has already accepted this order — it can no longer be cancelled.',
        }, status=400)

    item.is_cancelled = True
    item.cancelled_at = timezone.now()
    item.save(update_fields=['is_cancelled', 'cancelled_at'])

    # If the kitchen dashboard already turned this into a ticket line (it's
    # still 'pending' since the order was still 'new'), drop it so the
    # kitchen never sees a cancelled item to prepare.
    kitchen_item = getattr(item, 'kitchen_item', None)
    if kitchen_item is not None and kitchen_item.status == 'pending':
        kitchen_item.delete()

    bill = Bill.objects.filter(session=item.order.session, payment_status='unpaid').first()
    if bill:
        bill.recalculate()

    return JsonResponse({'success': True, 'item_name': item.food.food_name})


# ---------------------------------------------------------------------------
# REQUEST BILL
# ---------------------------------------------------------------------------
#
# Called from order_tracking.html when the customer taps "Request Bill".
# Creates (or reuses) the Bill row for the table's active session, with
# totals computed from every order placed this visit. From here the Bill
# shows up on the billing staff's dashboard as "Pending" until a cashier
# marks it Paid.

@require_POST
def request_bill(request, table_number):
    table = RestaurantTable.objects.filter(table_number=table_number).first()
    if not table:
        return JsonResponse({'success': False, 'error': 'Table not found.'}, status=404)

    session = TableSession.objects.filter(
        table=table, status='active'
    ).order_by('-started_at').first()

    if not session or not session.orders.exclude(status='cancelled').exists():
        return JsonResponse(
            {'success': False, 'error': 'No active order found for this table.'},
            status=400,
        )

    bill = Bill.objects.filter(session=session).first()
    created = False
    if not bill:
        bill = Bill.create_for_session(session)
        created = True
    elif bill.payment_status == 'paid':
        return JsonResponse(
            {'success': False, 'error': 'This bill has already been paid.'},
            status=400,
        )
    else:
        bill.recalculate()

    return JsonResponse({
        'success': True,
        'bill_id': bill.id,
        'grand_total': float(bill.grand_total),
        'created': created,
    })


# ---------------------------------------------------------------------------
# QUICK ORDER  (water, tissue, pickle, cold drink... requested straight
# from the round quick-order button on the menu page, no cart involved)
# ---------------------------------------------------------------------------

@require_POST
def quick_order(request, table_number):
    """
    Expects JSON: {"item_id": 3, "quantity": 1}
    Creates a QuickRequest against the table's active session (opening one
    if needed, same as place_order). Free items are just a kitchen ping;
    priced ones also get folded into that table's bill total.
    """
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {'success': False, 'error': 'Could not read your request.'}, status=400
        )

    try:
        item_id = int(payload.get('item_id'))
        quantity = max(1, int(payload.get('quantity', 1)))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=400)

    item = QuickItem.objects.filter(id=item_id, is_active=True).first()
    if not item:
        return JsonResponse(
            {'success': False, 'error': 'This item is no longer available.'}, status=404
        )

    table = RestaurantTable.objects.filter(table_number=table_number).first()
    if not table:
        return JsonResponse({'success': False, 'error': 'Table not found.'}, status=404)

    session = TableSession.objects.filter(
        table=table, status='active'
    ).order_by('-started_at').first()

    if not session:
        session = TableSession.objects.create(table=table, status='active')
        if table.status != 'occupied':
            table.status = 'occupied'
            table.save(update_fields=['status'])

    QuickRequest.objects.create(
        session=session,
        item=item,
        quantity=quantity,
        is_free=item.is_free,
        unit_price=item.price if not item.is_free else 0,
    )

    # If a bill has already been requested for this session, keep its
    # total in sync right away rather than waiting for the next view.
    bill = Bill.objects.filter(session=session, payment_status='unpaid').first()
    if bill:
        bill.recalculate()

    return JsonResponse({
        'success': True,
        'item_name': item.name,
        'is_free': item.is_free,
    })