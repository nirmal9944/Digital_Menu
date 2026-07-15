from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db import transaction

from menu.models import Order, OrderItem, RestaurantDetail, QuickRequest, FoodItem
from .models import KitchenStaff, KitchenTicket, KitchenTicketItem


# ---------------------------------------------------------------------------
# SESSION HELPERS
# ---------------------------------------------------------------------------

def _get_logged_in_staff(request):
    """Return KitchenStaff if a valid staff session exists, else None."""
    staff_id = request.session.get('kitchen_staff_id')
    if not staff_id:
        return None
    try:
        return KitchenStaff.objects.get(id=staff_id, is_active=True)
    except KitchenStaff.DoesNotExist:
        return None


def _login_required(view_func):
    """Simple decorator: redirect to login if not authenticated."""
    def wrapper(request, *args, **kwargs):
        if not _get_logged_in_staff(request):
            return redirect('kitchen:login')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


# ---------------------------------------------------------------------------
# AUTH VIEWS
# ---------------------------------------------------------------------------

def kds_login(request):
    """Login page for kitchen staff."""
    if _get_logged_in_staff(request):
        return redirect('kitchen:kds')

    error = None
    if request.method == 'POST':
        email    = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')

        staff = KitchenStaff.objects.filter(email__iexact=email, is_active=True).first()
        if staff and staff.check_password(password):
            request.session['kitchen_staff_id']   = staff.id
            request.session['kitchen_staff_name'] = staff.name
            request.session['kitchen_staff_role'] = staff.get_role_display()
            request.session.set_expiry(60 * 60 * 12)   # 12-hour session
            return redirect('kitchen:kds')
        else:
            error = 'Invalid email or password. Please try again.'

    return render(request, 'kitchen/kds_login.html', {'error': error})


def kds_logout(request):
    """Log out kitchen staff and redirect to login."""
    request.session.flush()
    return redirect('kitchen:login')


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _ensure_ticket_exists(order):
    """Create KitchenTicket + KitchenTicketItems for an order if not yet present."""
    ticket, _ = KitchenTicket.objects.get_or_create(
        order=order,
        defaults={'customer_note': order.customer_note or ''},
    )
    for item in order.items.select_related('food').filter(is_cancelled=False):
        KitchenTicketItem.objects.get_or_create(
            ticket=ticket,
            order_item=item,
            defaults={'preparation_time': item.food.preparation_time},
        )
    return ticket


def _ticket_to_dict(ticket):
    """Serialise a KitchenTicket to a JSON-safe dict."""
    order = ticket.order
    now   = timezone.now()

    ticket_items = (
        ticket.ticket_items
        .select_related('order_item__food__category')
        .order_by('id')
    )

    # Remaining seconds = time left for the slowest unfinished item
    remaining_seconds = 0
    if order.status not in ('delivered', 'cancelled'):
        max_remaining = 0
        for ti in ticket_items:
            if ti.status != 'done':
                prep_sec = ti.preparation_time * 60
                elapsed  = (now - ticket.received_at).total_seconds()
                remaining = max(prep_sec - elapsed, 0)
                max_remaining = max(max_remaining, remaining)
        remaining_seconds = int(max_remaining)

    items = []
    completed_times = []
    for ti in ticket_items:
        items.append({
            'id':                  ti.id,
            'order_item_id':       ti.order_item.id,
            'food_name':           ti.food_name,
            'category':            ti.category,
            'quantity':            ti.quantity,
            'special_instruction': ti.special_instruction,
            'preparation_time':    ti.preparation_time,
            'status':              ti.status,
            'started_at': (
                timezone.localtime(ti.started_at).strftime('%H:%M:%S')
                if ti.started_at else None
            ),
            'completed_at': (
                timezone.localtime(ti.completed_at).strftime('%H:%M:%S')
                if ti.completed_at else None
            ),
        })
        if ti.completed_at:
            completed_times.append(ti.completed_at)

    # The moment the order was fully delivered — the latest of every
    # item's completed_at. Only meaningful once the order is delivered.
    delivered_at_iso = None
    if order.status == 'delivered' and completed_times:
        delivered_at_iso = timezone.localtime(max(completed_times)).isoformat()

    return {
        'ticket_id':        ticket.id,
        'order_id':         order.id,
        'order_number':     f"ORD-{order.id:04d}",
        'table_number':     ticket.table_number,
        'status':           order.status,
        'priority':         ticket.priority,
        'customer_note':    ticket.customer_note,
        'kitchen_note':     ticket.kitchen_note,
        'is_acknowledged':  ticket.is_acknowledged,
        'received_at':      timezone.localtime(ticket.received_at).strftime('%H:%M'),
        # ISO 8601 with UTC offset so the browser's Date parser reads the
        # exact same instant regardless of server/browser timezone —
        # a naive "YYYY-MM-DD HH:MM:SS" string gets silently reinterpreted
        # as browser-local time by `new Date()`, throwing "time ago" off
        # by the server/browser UTC offset.
        'received_at_full': timezone.localtime(ticket.received_at).isoformat(),
        'delivered_at':      delivered_at_iso,
        'delivered_at_full': delivered_at_iso,
        'remaining_seconds': remaining_seconds,
        'items':            items,
        'all_items_done':   all(i['status'] == 'done' for i in items),
    }


def _build_kds_data():
    """
    Build the full KDS ticket list.
    Includes new / preparing / ready orders, plus every delivered one —
    the Completed tab's own time filter (Today / 7 days / 30 days / Year /
    All Time) is what narrows this down client-side, so the server must
    hand over the full history rather than an arbitrary recent slice.
    """
    # Ensure tickets exist for all active orders
    active_orders = (
        Order.objects
        .select_related('session__table')
        .prefetch_related('items__food__category')
        .filter(status__in=['new', 'preparing', 'ready'])
        .order_by('created_at')
    )
    for order in active_orders:
        _ensure_ticket_exists(order)

    # Active tickets. A ticket with no ticket_items left means every item on
    # that order was cancelled by the customer before the kitchen accepted
    # it (see menu.views.cancel_order_item) — it should just disappear from
    # the board rather than sit there as an empty "New" request.
    active_tickets = (
        KitchenTicket.objects
        .select_related('order__session__table')
        .filter(order__status__in=['new', 'preparing', 'ready'])
        .exclude(ticket_items__isnull=True)
        .order_by('-priority', 'received_at')
    )

    # Every delivered ticket — not capped, so the Completed tab's "All Time"
    # filter (and "Today" if a busy day has more than a handful of orders)
    # actually shows everything instead of silently truncating.
    delivered_tickets = (
        KitchenTicket.objects
        .select_related('order__session__table')
        .filter(order__status='delivered')
        .order_by('-received_at')
    )

    all_tickets = list(active_tickets) + list(delivered_tickets)
    return [_ticket_to_dict(t) for t in all_tickets]


# ---------------------------------------------------------------------------
# QUICK REQUESTS  (water, tissue, pickle, cold drink... tapped from the
# menu page's quick-order button — shown in their own KDS section)
# ---------------------------------------------------------------------------

def _quick_request_to_dict(qr):
    return {
        'id':                qr.id,
        'table_number':      qr.table_number,
        'item_name':         qr.item.name,
        'icon':              qr.item.icon,
        'quantity':          qr.quantity,
        'is_free':           qr.is_free,
        'unit_price':        float(qr.unit_price),
        'status':            qr.status,
        'requested_at':      timezone.localtime(qr.requested_at).strftime('%H:%M'),
        'requested_at_full': timezone.localtime(qr.requested_at).isoformat(),
        'served_at_full': (
            timezone.localtime(qr.served_at).isoformat() if qr.served_at else None
        ),
    }


def _build_quick_requests_data():
    """Pending quick requests, plus the last 20 already served (for history)."""
    pending = (
        QuickRequest.objects
        .select_related('item', 'session__table')
        .filter(status='pending')
        .order_by('requested_at')
    )
    served = (
        QuickRequest.objects
        .select_related('item', 'session__table')
        .filter(status='served')
        .order_by('-served_at')[:20]
    )
    all_requests = list(pending) + list(served)
    return [_quick_request_to_dict(q) for q in all_requests]


# ---------------------------------------------------------------------------
# PAGE VIEW
# ---------------------------------------------------------------------------

@_login_required
def kds_display(request):
    staff        = _get_logged_in_staff(request)
    restaurant   = RestaurantDetail.objects.first()
    tickets_data = _build_kds_data()
    quick_data   = _build_quick_requests_data()

    context = {
        'restaurant': restaurant,
        'tickets':    tickets_data,   # plain Python list — serialised in the template via |json_script
        'quick_requests': quick_data,
        'now':        timezone.localtime(timezone.now()).strftime('%H:%M:%S'),
        'staff':      staff,
    }
    return render(request, 'kitchen/kds.html', context)


# ---------------------------------------------------------------------------
# JSON POLL  (called every ~5 s by the frontend)
# ---------------------------------------------------------------------------

@_login_required
def kds_poll(request):
    tickets_data = _build_kds_data()
    return JsonResponse({
        'tickets':        tickets_data,
        'quick_requests': _build_quick_requests_data(),
        'server_time':    timezone.localtime(timezone.now()).strftime('%H:%M:%S'),
    })


# ---------------------------------------------------------------------------
# AJAX ACTIONS
# ---------------------------------------------------------------------------

@_login_required
@require_POST
def update_item_status(request, item_id):
    ti = get_object_or_404(
        KitchenTicketItem.objects.select_related(
            'ticket__order__session__table',
            'order_item__food',
        ),
        id=item_id,
    )

    new_status = request.POST.get('status')
    if new_status not in ['pending', 'in_progress', 'done']:
        return JsonResponse({'error': 'Invalid status'}, status=400)

    now = timezone.now()

    with transaction.atomic():
        ti.status = new_status
        if new_status == 'in_progress' and not ti.started_at:
            ti.started_at = now
        if new_status == 'done':
            ti.completed_at = now
        ti.save()

        order     = ti.ticket.order
        all_items = ti.ticket.ticket_items.all()
        statuses  = list(all_items.values_list('status', flat=True))

        if all(s == 'done' for s in statuses):
            if order.status in ('new', 'preparing'):
                order.status = 'ready'
                order.save()
        elif any(s in ('in_progress', 'done') for s in statuses):
            if order.status == 'new':
                order.status = 'preparing'
                order.save()

    return JsonResponse({
        'ok':           True,
        'item_id':      ti.id,
        'item_status':  ti.status,
        'order_status': ti.ticket.order.status,
    })


@_login_required
@require_POST
def set_order_status(request, ticket_id):
    ticket = get_object_or_404(
        KitchenTicket.objects.select_related('order'),
        id=ticket_id,
    )

    new_status = request.POST.get('status')
    if new_status not in ['new', 'preparing', 'ready', 'delivered']:
        return JsonResponse({'error': 'Invalid status'}, status=400)

    with transaction.atomic():
        ticket.order.status = new_status
        ticket.order.save()

        if new_status == 'delivered':
            ticket.ticket_items.exclude(status='done').update(
                status='done',
                completed_at=timezone.now(),
            )

    return JsonResponse({
        'ok':           True,
        'ticket_id':    ticket.id,
        'order_status': ticket.order.status,
    })


@_login_required
@require_POST
def set_priority(request, ticket_id):
    ticket   = get_object_or_404(KitchenTicket, id=ticket_id)
    priority = request.POST.get('priority')
    if priority not in ('normal', 'rush', 'hold'):
        return JsonResponse({'error': 'Invalid priority'}, status=400)
    ticket.priority = priority
    ticket.save()
    return JsonResponse({'ok': True, 'priority': priority})


@_login_required
@require_POST
def acknowledge_ticket(request, ticket_id):
    ticket = get_object_or_404(KitchenTicket, id=ticket_id)
    if not ticket.is_acknowledged:
        ticket.is_acknowledged = True
        ticket.acknowledged_at = timezone.now()
        ticket.save()
    return JsonResponse({'ok': True})


@_login_required
@require_POST
def save_kitchen_note(request, ticket_id):
    ticket = get_object_or_404(KitchenTicket, id=ticket_id)
    note   = request.POST.get('note', '').strip()
    ticket.kitchen_note = note
    ticket.save()
    return JsonResponse({'ok': True, 'note': note})


@_login_required
@require_POST
def serve_quick_request(request, request_id):
    qr = get_object_or_404(QuickRequest, id=request_id)
    if qr.status != 'served':
        qr.status = 'served'
        qr.served_at = timezone.now()
        qr.save()
    return JsonResponse({'ok': True, 'id': qr.id, 'status': qr.status})


# ---------------------------------------------------------------------------
# FOOD ITEM MANAGEMENT  (kitchen staff can mark an item out of stock, or
# adjust how long it takes to prepare, straight from the KDS)
# ---------------------------------------------------------------------------

def _food_item_to_dict(food):
    return {
        'id':                food.id,
        'food_name':         food.food_name,
        'category':          food.category.category_name,
        'price':             float(food.price),
        'is_available':      food.is_available,
        'preparation_time':  food.preparation_time,
        'image_url':         food.image.url if food.image else None,
    }


@_login_required
def food_items_data(request):
    """JSON list of every food item, for the KDS 'Menu Items' panel."""
    foods = (
        FoodItem.objects
        .select_related('category')
        .order_by('category__category_name', 'food_name')
    )
    return JsonResponse({'foods': [_food_item_to_dict(f) for f in foods]})


@_login_required
@require_POST
def update_food_item(request, food_id):
    """
    Kitchen staff toggling stock / editing prep time from the KDS.

    Changing preparation_time here only affects FUTURE tickets — orders
    already placed keep the prep time that was frozen onto their
    KitchenTicketItem when the ticket was created (see
    kitchen.models.KitchenTicketItem.preparation_time), so an order
    already running is never re-timed mid-flight.
    """
    food = get_object_or_404(FoodItem, id=food_id)

    if 'is_available' in request.POST:
        food.is_available = request.POST.get('is_available') in ('1', 'true', 'True')

    if 'preparation_time' in request.POST:
        try:
            minutes = int(request.POST.get('preparation_time'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Invalid preparation time'}, status=400)
        if minutes < 1:
            return JsonResponse({'error': 'Preparation time must be at least 1 minute'}, status=400)
        food.preparation_time = minutes

    food.save()
    return JsonResponse({'ok': True, 'food': _food_item_to_dict(food)})