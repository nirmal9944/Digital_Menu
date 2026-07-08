from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db import transaction

from menu.models import Order, OrderItem, RestaurantDetail
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
    for item in order.items.select_related('food'):
        KitchenTicketItem.objects.get_or_create(
            ticket=ticket,
            order_item=item,
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
        'received_at_full': timezone.localtime(ticket.received_at).strftime('%Y-%m-%d %H:%M:%S'),
        'remaining_seconds': remaining_seconds,
        'items':            items,
        'all_items_done':   all(i['status'] == 'done' for i in items),
    }


def _build_kds_data():
    """
    Build the full KDS ticket list.
    Includes new / preparing / ready orders, plus the last 20 delivered ones.
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

    # Active tickets
    active_tickets = (
        KitchenTicket.objects
        .select_related('order__session__table')
        .filter(order__status__in=['new', 'preparing', 'ready'])
        .order_by('-priority', 'received_at')
    )

    # Recently delivered tickets (last 20)
    delivered_tickets = (
        KitchenTicket.objects
        .select_related('order__session__table')
        .filter(order__status='delivered')
        .order_by('-received_at')[:20]
    )

    all_tickets = list(active_tickets) + list(delivered_tickets)
    return [_ticket_to_dict(t) for t in all_tickets]


# ---------------------------------------------------------------------------
# PAGE VIEW
# ---------------------------------------------------------------------------

@_login_required
def kds_display(request):
    staff        = _get_logged_in_staff(request)
    restaurant   = RestaurantDetail.objects.first()
    tickets_data = _build_kds_data()

    context = {
        'restaurant': restaurant,
        'tickets':    tickets_data,   # plain Python list — serialised in the template via |json_script
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
        'tickets':     tickets_data,
        'server_time': timezone.localtime(timezone.now()).strftime('%H:%M:%S'),
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