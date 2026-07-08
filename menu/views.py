import json

from django.shortcuts import render
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
)


def landing(request, table_number):

    restaurant = RestaurantDetail.objects.first()

    context = {
        'table_number': table_number,
        'restaurant': restaurant
    }

    return render(request, 'menu/landing.html', context)


def menu_page(request, table_number):

    context = {
        'table_number': table_number,

        'restaurant': RestaurantDetail.objects.first(),

        'categories': Category.objects.filter(
            is_active=True
        ),

        'foods': FoodItem.objects.select_related(
            'category'
        ).filter(
            is_available=True
        ),

        'popular_foods': FoodItem.objects.select_related(
            'category'
        ).filter(
            is_available=True,
            is_popular=True
        )
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
            order_items = list(order.items.select_related('food'))
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
        subtotal += float(item.subtotal)
        items.append({
            'id': item.id,
            'name': item.food.food_name,
            'category': item.food.category.category_name,
            'note': item.special_instruction,
            'quantity': item.quantity,
            'unit_price': float(item.unit_price),
            'subtotal': float(item.subtotal),
        })

    try:
        bill = session.bill
        vat_percent = float(bill.vat_percent)
        discount = float(bill.discount)
    except Bill.DoesNotExist:
        vat_percent = 13.0
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
            unit_price=food.price,
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