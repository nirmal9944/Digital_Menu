from datetime import datetime, timedelta
from decimal import Decimal

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db import transaction

from menu.models import Bill, TableSession, RestaurantTable, OrderItem, RestaurantDetail, QuickRequest
from .models import BillingStaff


# ---------------------------------------------------------------------------
# SESSION HELPERS
# ---------------------------------------------------------------------------

def _get_logged_in_staff(request):
    """Return BillingStaff if a valid staff session exists, else None."""
    staff_id = request.session.get('billing_staff_id')
    if not staff_id:
        return None
    try:
        return BillingStaff.objects.get(id=staff_id, is_active=True)
    except BillingStaff.DoesNotExist:
        return None


def _login_required(view_func):
    """Simple decorator: redirect to login if not authenticated."""
    def wrapper(request, *args, **kwargs):
        if not _get_logged_in_staff(request):
            return redirect('billing:login')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


# ---------------------------------------------------------------------------
# AUTH VIEWS
# ---------------------------------------------------------------------------

def billing_login(request):
    """Login page for billing / cashier staff."""
    if _get_logged_in_staff(request):
        return redirect('billing:dashboard')

    error = None
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')

        staff = BillingStaff.objects.filter(email__iexact=email, is_active=True).first()
        if staff and staff.check_password(password):
            request.session['billing_staff_id'] = staff.id
            request.session['billing_staff_name'] = staff.name
            request.session['billing_staff_role'] = staff.get_role_display()
            request.session.set_expiry(60 * 60 * 12)   # 12-hour session
            return redirect('billing:dashboard')
        else:
            error = 'Invalid email or password. Please try again.'

    return render(request, 'billing/login.html', {'error': error})


def billing_logout(request):
    """Log out billing staff and redirect to login."""
    request.session.flush()
    return redirect('billing:login')


# ---------------------------------------------------------------------------
# SERIALISERS
# ---------------------------------------------------------------------------

def _bill_to_dict(bill):
    """Serialise a Bill (+ its session's items) to a JSON-safe dict."""
    session = bill.session

    # Pending bills stay live: if the table adds more items (or the kitchen
    # cancels one) after the bill was first requested, refresh subtotal /
    # grand_total from the current items x quantity before showing it to
    # staff. Paid bills are historical records and are left untouched.
    if bill.payment_status == 'unpaid':
        bill.recalculate()

    items_qs = (
        OrderItem.objects
        .filter(order__session=session)
        .exclude(order__status='cancelled')
        .select_related('food', 'food__category', 'order')
        .order_by('order__created_at', 'id')
    )

    items = []
    for item in items_qs:
        items.append({
            'id': item.id,
            'name': item.food.food_name if item.food_id else 'Unknown item',
            'category': item.food.category.category_name if (item.food_id and item.food.category_id) else '',
            'quantity': item.quantity,
            'unit_price': float(item.unit_price),
            'subtotal': float(item.subtotal),
            'note': item.special_instruction,
        })

    # Quick requests (water, tissue, pickle, cold drink...) — free ones show
    # up for visibility but never add to the total; paid ones do both.
    quick_qs = (
        QuickRequest.objects
        .filter(session=session)
        .select_related('item')
        .order_by('requested_at')
    )
    for qr in quick_qs:
        items.append({
            'id': f'quick-{qr.id}',
            'name': qr.item.name + (' (Free)' if qr.is_free else ''),
            'category': 'Quick Request',
            'quantity': qr.quantity,
            'unit_price': 0.0 if qr.is_free else float(qr.unit_price),
            'subtotal': float(qr.subtotal),
            'note': '' if qr.status == 'served' else 'Pending in kitchen',
        })

    orders = session.orders.exclude(status='cancelled').order_by('created_at')
    order_numbers = [f"ORD-{o.id:04d}" for o in orders]

    taxable = bill.subtotal - bill.discount
    tax = bill.grand_total - taxable

    return {
        'bill_id': bill.id,
        'table_number': bill.table_number,
        'session_id': session.id,
        'orders': order_numbers,
        'order_count': len(order_numbers),
        'items': items,
        'item_count': sum(i['quantity'] for i in items),
        'subtotal': float(bill.subtotal),
        'discount': float(bill.discount),
        'vat_percent': float(bill.vat_percent),
        'tax': float(tax),
        'grand_total': float(bill.grand_total),
        'payment_status': bill.payment_status,
        'requested_at': timezone.localtime(bill.requested_at).strftime('%b %d, %Y at %I:%M %p'),
        'requested_at_short': timezone.localtime(bill.requested_at).strftime('%I:%M %p'),
        'paid_at': (
            timezone.localtime(bill.paid_at).strftime('%b %d, %Y at %I:%M %p')
            if bill.paid_at else None
        ),
        'minutes_ago': int((timezone.now() - bill.requested_at).total_seconds() // 60),
    }


def _build_dashboard_data():
    """Single source of truth for the billing dashboard — used by both the
    page view (first paint) and the JSON poll endpoint."""

    pending_bills = (
        Bill.objects.filter(payment_status='unpaid')
        .select_related('session__table')
        .order_by('requested_at')
    )

    today = timezone.localdate()
    paid_today = (
        Bill.objects.filter(payment_status='paid', paid_at__date=today)
        .select_related('session__table')
        .order_by('-paid_at')
    )

    # Occupied tables with an active session + real orders, but no bill
    # requested yet — lets billing staff raise the bill themselves.
    awaiting = []
    active_sessions = (
        TableSession.objects.filter(status='active')
        .select_related('table')
        .prefetch_related('orders')
        .order_by('table__table_number')
    )
    for session in active_sessions:
        if hasattr(session, 'bill'):
            continue
        order_count = session.orders.exclude(status='cancelled').count()
        if not order_count:
            continue
        awaiting.append({
            'table_number': session.table.table_number,
            'table_id': session.table.id,
            'session_id': session.id,
            'order_count': order_count,
        })

    pending_list = [_bill_to_dict(b) for b in pending_bills]
    paid_today_list = [_bill_to_dict(b) for b in paid_today]

    return {
        'pending': pending_list,
        'paid_today': paid_today_list,
        'awaiting': awaiting,
        'stats': {
            'pending_count': len(pending_list),
            'pending_total': round(sum(b['grand_total'] for b in pending_list), 2),
            'paid_today_count': len(paid_today_list),
            'paid_today_total': round(sum(b['grand_total'] for b in paid_today_list), 2),
            'awaiting_count': len(awaiting),
        },
        'server_time': timezone.localtime(timezone.now()).strftime('%H:%M:%S'),
    }


# ---------------------------------------------------------------------------
# PAGE VIEWS
# ---------------------------------------------------------------------------

@_login_required
def dashboard(request):
    staff = _get_logged_in_staff(request)
    context = {
        'restaurant': RestaurantDetail.objects.first(),
        'staff': staff,
        'data': _build_dashboard_data(),
    }
    return render(request, 'billing/dashboard.html', context)


@_login_required
def dashboard_poll(request):
    return JsonResponse(_build_dashboard_data())


@_login_required
def bill_detail(request, bill_id):
    bill = get_object_or_404(Bill.objects.select_related('session__table'), id=bill_id)
    return JsonResponse(_bill_to_dict(bill))


@_login_required
def invoice(request, bill_id):
    bill = get_object_or_404(Bill.objects.select_related('session__table'), id=bill_id)
    context = {
        'restaurant': RestaurantDetail.objects.first(),
        'bill': _bill_to_dict(bill),
    }
    return render(request, 'billing/invoice.html', context)


@_login_required
def report(request):
    staff = _get_logged_in_staff(request)
    context = {
        'restaurant': RestaurantDetail.objects.first(),
        'staff': staff,
    }
    return render(request, 'billing/report.html', context)


# ---------------------------------------------------------------------------
# AJAX ACTIONS
# ---------------------------------------------------------------------------

@_login_required
@require_POST
def set_bill_status(request, bill_id):
    bill = get_object_or_404(Bill.objects.select_related('session__table'), id=bill_id)

    new_status = request.POST.get('status')
    if new_status not in ('unpaid', 'paid'):
        return JsonResponse({'error': 'Invalid status'}, status=400)

    with transaction.atomic():
        bill.payment_status = new_status

        if new_status == 'paid':
            bill.paid_at = timezone.now()
            bill.save()

            # Closing out the bill frees the table for the next guests.
            session = bill.session
            session.status = 'closed'
            session.ended_at = timezone.now()
            session.save(update_fields=['status', 'ended_at'])

            table = session.table
            if table.status != 'available':
                table.status = 'available'
                table.save(update_fields=['status'])
        else:
            bill.paid_at = None
            bill.save()

    return JsonResponse({
        'ok': True,
        'bill_id': bill.id,
        'payment_status': bill.payment_status,
    })


@_login_required
@require_POST
def create_bill(request, table_id):
    """Staff-initiated bill for a table that hasn't requested one yet."""
    table = get_object_or_404(RestaurantTable, id=table_id)
    session = TableSession.objects.filter(
        table=table, status='active'
    ).order_by('-started_at').first()

    if not session or not session.orders.exclude(status='cancelled').exists():
        return JsonResponse({'error': 'No active order for this table.'}, status=400)

    bill = Bill.objects.filter(session=session).first()
    if not bill:
        bill = Bill.create_for_session(session)
    elif bill.payment_status != 'paid':
        bill.recalculate()

    return JsonResponse({'ok': True, 'bill_id': bill.id})


# ---------------------------------------------------------------------------
# REPORTS
# ---------------------------------------------------------------------------

@_login_required
def report_data(request):
    """
    JSON stats for the report page.
    Accepts ?range=today|7d|30d|all  OR  ?start=YYYY-MM-DD&end=YYYY-MM-DD
    """
    range_key = request.GET.get('range', '7d')
    today = timezone.localdate()

    start_param = request.GET.get('start')
    end_param = request.GET.get('end')

    start_date = end_date = None
    if start_param and end_param:
        try:
            start_date = datetime.strptime(start_param, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_param, '%Y-%m-%d').date()
            range_key = 'custom'
        except ValueError:
            start_date = end_date = None

    if start_date is None:
        if range_key == 'today':
            start_date = end_date = today
        elif range_key == '30d':
            start_date, end_date = today - timedelta(days=29), today
        elif range_key == 'all':
            start_date = end_date = None
        else:
            range_key = '7d'
            start_date, end_date = today - timedelta(days=6), today

    paid_qs = Bill.objects.filter(payment_status='paid').select_related('session__table')
    if start_date and end_date:
        paid_qs = paid_qs.filter(paid_at__date__gte=start_date, paid_at__date__lte=end_date)
    paid_qs = list(paid_qs.order_by('-paid_at'))

    total_revenue = sum((b.grand_total for b in paid_qs), Decimal('0'))
    total_discount = sum((b.discount for b in paid_qs), Decimal('0'))
    total_tax = sum(((b.grand_total - (b.subtotal - b.discount)) for b in paid_qs), Decimal('0'))
    total_bills = len(paid_qs)
    avg_bill = (total_revenue / total_bills) if total_bills else Decimal('0')

    daily = {}
    for b in paid_qs:
        d = timezone.localtime(b.paid_at).date().isoformat()
        daily[d] = daily.get(d, 0) + float(b.grand_total)
    daily_series = sorted(daily.items())

    bills_list = [{
        'bill_id': b.id,
        'table_number': b.table_number,
        'item_count': OrderItem.objects.filter(
            order__session=b.session
        ).exclude(order__status='cancelled').count(),
        'subtotal': float(b.subtotal),
        'discount': float(b.discount),
        'grand_total': float(b.grand_total),
        'paid_at': (
            timezone.localtime(b.paid_at).strftime('%b %d, %Y · %I:%M %p')
            if b.paid_at else None
        ),
    } for b in paid_qs[:300]]

    return JsonResponse({
        'range': range_key,
        'start': start_date.isoformat() if start_date else None,
        'end': end_date.isoformat() if end_date else None,
        'total_revenue': round(float(total_revenue), 2),
        'total_discount': round(float(total_discount), 2),
        'total_tax': round(float(total_tax), 2),
        'total_bills': total_bills,
        'avg_bill': round(float(avg_bill), 2),
        'daily_series': daily_series,
        'bills': bills_list,
    })
