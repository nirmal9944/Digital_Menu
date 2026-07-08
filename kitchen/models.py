from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone
from menu.models import Order, OrderItem


# ---------------------------------------------------------------------------
# KITCHEN STAFF
# ---------------------------------------------------------------------------

class KitchenStaff(models.Model):
    ROLE_CHOICES = [
        ('head_chef',   'Head Chef'),
        ('chef',        'Chef'),
        ('sous_chef',   'Sous Chef'),
        ('line_cook',   'Line Cook'),
        ('kitchen_mgr', 'Kitchen Manager'),
    ]

    name       = models.CharField(max_length=120)
    email      = models.EmailField(unique=True)
    role       = models.CharField(max_length=20, choices=ROLE_CHOICES, default='chef')
    password   = models.CharField(max_length=256)          # stored hashed
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Kitchen Staff'
        verbose_name_plural = 'Kitchen Staff'
        ordering            = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)


# ---------------------------------------------------------------------------
# KITCHEN TICKET
# ---------------------------------------------------------------------------

class KitchenTicket(models.Model):
    PRIORITY_CHOICES = [
        ('normal', 'Normal'),
        ('rush',   'Rush'),
        ('hold',   'Hold'),
    ]

    order           = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='kitchen_ticket')
    received_at     = models.DateTimeField(auto_now_add=True)
    priority        = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='normal')
    is_acknowledged = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    kitchen_note    = models.TextField(blank=True, default='')
    customer_note   = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-priority', 'received_at']

    def __str__(self):
        return f"Ticket for Order #{self.order.id}"

    @property
    def table_number(self):
        try:
            return self.order.session.table.table_number
        except Exception:
            return '—'


# ---------------------------------------------------------------------------
# KITCHEN TICKET ITEM
# ---------------------------------------------------------------------------

class KitchenTicketItem(models.Model):
    STATUS_CHOICES = [
        ('pending',     'Pending'),
        ('in_progress', 'In Progress'),
        ('done',        'Done'),
    ]

    ticket             = models.ForeignKey(KitchenTicket, on_delete=models.CASCADE, related_name='ticket_items')
    order_item         = models.OneToOneField(OrderItem,   on_delete=models.CASCADE, related_name='kitchen_item')
    status             = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    started_at         = models.DateTimeField(null=True, blank=True)
    completed_at       = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.food_name} x{self.quantity} [{self.status}]"

    # ---- denormalised helpers so the ticket never breaks if food is deleted ----
    @property
    def food_name(self):
        try:
            return self.order_item.food.food_name
        except Exception:
            return 'Unknown Item'

    @property
    def category(self):
        try:
            return self.order_item.food.category.category_name
        except Exception:
            return ''

    @property
    def quantity(self):
        return self.order_item.quantity

    @property
    def special_instruction(self):
        return self.order_item.special_instruction or ''

    @property
    def preparation_time(self):
        try:
            return self.order_item.food.preparation_time
        except Exception:
            return 15