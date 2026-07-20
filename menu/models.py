from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

class RestaurantDetail(models.Model):
    restaurant_name = models.CharField(max_length=200)
    sub_name = models.CharField(max_length=200)
    logo = models.ImageField(upload_to='logo/')

    def __str__(self):
        return self.restaurant_name

class RestaurantTable(models.Model):

    STATUS_CHOICES = [
        ('available', 'Available'),
        ('occupied', 'Occupied'),
        ('reserved', 'Reserved'),
    ]

    table_number = models.PositiveIntegerField(unique=True)

    capacity = models.PositiveIntegerField(default=4)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='available'
    )

    qr_code = models.ImageField(
        upload_to='qr/',
        blank=True,
        null=True
    )

    def __str__(self):
        return f"Table {self.table_number}"


class Category(models.Model):

    category_name = models.CharField(
        max_length=100,
        unique=True
    )

    image = models.ImageField(
        upload_to='categories/',
        blank=True,
        null=True
    )

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.category_name


class FoodItem(models.Model):

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='foods'
    )

    food_name = models.CharField(max_length=200)

    description = models.TextField(blank=True)

    image = models.ImageField(
        upload_to='foods/',
        blank=True,
        null=True
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    is_available = models.BooleanField(default=True)

    is_popular = models.BooleanField(default=False)

    is_vegetarian = models.BooleanField(
        default=False,
        help_text=(
            'Shown as the veg/non-veg indicator on the menu. Defaults to False '
            'for existing dishes — review and set correctly per dish before relying on it.'
        ),
    )

    is_chef_choice = models.BooleanField(default=False)

    is_new = models.BooleanField(default=False)

    rating = models.DecimalField(
        max_digits=2,
        decimal_places=1,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('5'))],
        help_text='0.0–5.0. Leave blank to hide the rating badge.',
    )

    calories = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text='Leave blank to hide the calorie badge.',
    )

    ingredients = models.CharField(
        max_length=500,
        blank=True,
        help_text='Comma-separated list, shown in the item detail modal. Leave blank to hide.',
    )

    preparation_time = models.PositiveIntegerField(
        default=15,
        help_text='minutes'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.food_name

    @property
    def active_offer(self):
        """The first currently-valid Offer for this item, or None."""
        now = timezone.now()
        for offer in self.offers.filter(is_active=True).order_by('-created_at'):
            if offer.starts_at and offer.starts_at > now:
                continue
            if offer.ends_at and offer.ends_at < now:
                continue
            return offer
        return None

    @property
    def effective_price(self):
        """Selling price after any active offer is applied."""
        offer = self.active_offer
        return offer.discounted_price(self.price) if offer else self.price


class Offer(models.Model):
    """An admin-defined discount applied to a single menu item."""

    DISCOUNT_TYPE = (
        ('percent', 'Percentage'),
        ('flat', 'Flat Amount (Rs.)'),
    )

    title = models.CharField(max_length=150)

    food = models.ForeignKey(
        FoodItem,
        on_delete=models.CASCADE,
        related_name='offers'
    )

    discount_type = models.CharField(
        max_length=10,
        choices=DISCOUNT_TYPE,
        default='percent'
    )

    value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text='Percent off (0–100) or a flat Rs. amount off, depending on type above.'
    )

    is_active = models.BooleanField(default=True)

    starts_at = models.DateTimeField(
        blank=True, null=True,
        help_text='Leave blank to start immediately.'
    )
    ends_at = models.DateTimeField(
        blank=True, null=True,
        help_text='Leave blank to run with no end date.'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} — {self.food.food_name}"

    def clean(self):
        if self.discount_type == 'percent' and self.value is not None:
            if self.value < 0 or self.value > 100:
                raise ValidationError({'value': 'Percentage discount must be between 0 and 100.'})
        elif self.discount_type == 'flat' and self.value is not None and self.value < 0:
            raise ValidationError({'value': 'Flat discount cannot be negative.'})
        if self.starts_at and self.ends_at and self.starts_at > self.ends_at:
            raise ValidationError({'ends_at': 'End date must be after the start date.'})

    def discounted_price(self, base_price=None):
        base_price = self.food.price if base_price is None else base_price
        if self.discount_type == 'percent':
            reduction = (base_price * self.value / Decimal('100'))
        else:
            reduction = self.value
        return max(base_price - reduction, Decimal('0')).quantize(Decimal('0.01'))


class VATSetting(models.Model):
    """
    Restaurant-wide VAT rate, editable by admin. Every new Bill is stamped
    with whichever row here has is_active=True at the moment it's created.
    """

    name = models.CharField(max_length=100, default='Standard VAT')
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=13)
    is_active = models.BooleanField(
        default=True,
        help_text='Only one VAT setting can be active — activating this one deactivates the others.'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'VAT Setting'
        verbose_name_plural = 'VAT Settings'
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.name} — {self.percent}%" + (' (active)' if self.is_active else '')

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            VATSetting.objects.exclude(pk=self.pk).update(is_active=False)

    @classmethod
    def current_percent(cls):
        setting = cls.objects.filter(is_active=True).order_by('-updated_at').first()
        return setting.percent if setting else Decimal('13')


class DiscountSetting(models.Model):
    """
    Restaurant-wide default discount (as a % of subtotal), editable by
    admin. Applied automatically when a new Bill is generated — staff can
    still adjust an individual bill's discount afterwards from Billing.
    """

    name = models.CharField(max_length=100, default='Standard Discount')
    percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text='% of the bill subtotal to discount automatically. Set to 0 for no default discount.'
    )
    is_active = models.BooleanField(
        default=False,
        help_text='Only one discount setting can be active — activating this one deactivates the others.'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Discount Setting'
        verbose_name_plural = 'Discount Settings'
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.name} — {self.percent}%" + (' (active)' if self.is_active else '')

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            DiscountSetting.objects.exclude(pk=self.pk).update(is_active=False)

    @classmethod
    def current_percent(cls):
        setting = cls.objects.filter(is_active=True).order_by('-updated_at').first()
        return setting.percent if setting else Decimal('0')


class QuickItem(models.Model):
    """
    Admin-managed catalog of small on-demand requests a table can tap for
    from the menu page's quick-order button — tissues, pickle, water,
    a cold drink, etc. Free items are just a service request for the
    kitchen; priced ones also get added to that table's bill.
    """

    name = models.CharField(max_length=100)

    icon = models.CharField(
        max_length=50, blank=True, default='fa-circle-plus',
        help_text='Font Awesome icon class, e.g. fa-glass-water, fa-tissue.'
    )

    is_free = models.BooleanField(default=True)

    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Only charged when "is free" is unchecked.'
    )

    is_active = models.BooleanField(default=True)

    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'Quick Item'
        verbose_name_plural = 'Quick Items'

    def __str__(self):
        return self.name if self.is_free else f"{self.name} (Rs. {self.price})"


class TableSession(models.Model):

    STATUS_CHOICES = (
        ('active', 'Active'),
        ('closed', 'Closed'),
    )

    table = models.ForeignKey(
        RestaurantTable,
        on_delete=models.CASCADE,
        related_name='sessions'
    )

    started_at = models.DateTimeField(auto_now_add=True)

    ended_at = models.DateTimeField(
        blank=True,
        null=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active'
    )

    # Generated after this session's first order (see menu.views.place_order
    # / quick_order) so a second device opening this table's URL has to
    # prove membership before it can browse or order. Blank until then;
    # cleared when the session closes (billing.views.set_bill_status).
    pin = models.CharField(max_length=4, blank=True)

    def __str__(self):
        return f"Table {self.table.table_number}"


class QuickRequest(models.Model):
    """A single quick-item request placed by a table (free or paid)."""

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('served', 'Served'),
    )

    session = models.ForeignKey(
        TableSession,
        on_delete=models.CASCADE,
        related_name='quick_requests'
    )

    item = models.ForeignKey(
        QuickItem,
        on_delete=models.CASCADE,
        related_name='requests'
    )

    quantity = models.PositiveIntegerField(default=1)

    # Snapshot of the item's free/price status at request time, so a later
    # admin price change never rewrites history for past requests/bills.
    is_free = models.BooleanField(default=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')

    requested_at = models.DateTimeField(auto_now_add=True)
    served_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.item.name} x{self.quantity} — Table {self.table_number}"

    @property
    def table_number(self):
        try:
            return self.session.table.table_number
        except Exception:
            return '—'

    @property
    def subtotal(self):
        return Decimal('0') if self.is_free else (self.unit_price * self.quantity)

class Order(models.Model):

    STATUS_CHOICES = (
        ('new', 'New'),
        ('preparing', 'Preparing'),
        ('ready', 'Ready'),
        ('delivered', 'Delivered'),
    )

    session = models.ForeignKey(
        TableSession,
        on_delete=models.CASCADE,
        related_name='orders'
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='new'
    )

    customer_note = models.TextField(
        blank=True
    )

    def __str__(self):
        return f"Order #{self.id}"

class OrderItem(models.Model):

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items'
    )

    food = models.ForeignKey(
        FoodItem,
        on_delete=models.CASCADE
    )

    quantity = models.PositiveIntegerField(default=1)

    special_instruction = models.CharField(
        max_length=255,
        blank=True
    )

    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    # A customer can cancel an item while the kitchen hasn't started it yet
    # (order.status == 'new'). Kept as a row rather than deleted so the
    # receipt/order-tracking history still shows what was cancelled.
    is_cancelled = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(blank=True, null=True)

    def save(self, *args, **kwargs):
        self.subtotal = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.food.food_name} x {self.quantity}"

class Bill(models.Model):

    PAYMENT_STATUS = (
        ('unpaid', 'Pending'),
        ('paid', 'Paid')
    )

    session = models.OneToOneField(
        TableSession,
        on_delete=models.CASCADE,
        related_name='bill'
    )

    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    discount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    vat_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=13
    )

    grand_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    payment_status = models.CharField(
        max_length=10,
        choices=PAYMENT_STATUS,
        default='unpaid'
    )

    requested_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"Bill #{self.id} — Table {self.session.table.table_number}"

    @property
    def table_number(self):
        try:
            return self.session.table.table_number
        except Exception:
            return '—'

    def recalculate(self, save=True):
        """
        Recompute subtotal / grand_total from every OrderItem placed in
        this bill's session (across all orders, cancelled ones excluded)
        plus any paid QuickRequests (free ones don't add to the bill).
        Discount and vat_percent are left untouched — staff can adjust
        those, this only refreshes the item-derived numbers.
        """
        items = OrderItem.objects.filter(
            order__session=self.session
        ).exclude(order__status='cancelled').exclude(is_cancelled=True)

        food_subtotal = sum((item.subtotal for item in items), Decimal('0'))
        quick_subtotal = sum(
            (qr.subtotal for qr in self.session.quick_requests.filter(is_free=False)),
            Decimal('0'),
        )

        subtotal = food_subtotal + quick_subtotal
        taxable = max(subtotal - self.discount, Decimal('0'))
        tax = (taxable * self.vat_percent / Decimal('100')).quantize(Decimal('0.01'))

        self.subtotal = subtotal
        self.grand_total = (taxable + tax).quantize(Decimal('0.01'))

        if save:
            self.save()
        return self.grand_total

    @classmethod
    def create_for_session(cls, session):
        """
        Create a fresh Bill for a table session, stamped with whatever
        VAT % and default discount % are currently active in admin.
        """
        bill = cls.objects.create(session=session, vat_percent=VATSetting.current_percent())
        bill.recalculate(save=False)

        discount_percent = DiscountSetting.current_percent()
        if discount_percent:
            bill.discount = (bill.subtotal * discount_percent / Decimal('100')).quantize(Decimal('0.01'))

        bill.recalculate()
        return bill


class Feedback(models.Model):
    """
    Post-payment customer feedback. One per Bill — a table session can cover
    several Orders (via "Add More Items"), but only ever has one Bill, so
    Bill is the correct 1:1 anchor for "how was this whole visit", not Order.
    """

    bill = models.OneToOneField(
        Bill,
        on_delete=models.CASCADE,
        related_name='feedback',
    )

    # Denormalized so admin list/filter doesn't need a join through
    # bill__session__table for every row.
    table_number = models.PositiveIntegerField()

    overall_rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    food_rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    accuracy_rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    speed_rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    qr_system_rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    value_rating = models.PositiveSmallIntegerField(
        blank=True, null=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='Optional — "Value for Money" is the one skippable question.',
    )

    comment = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"Feedback for Bill #{self.bill_id} — Table {self.table_number} ({self.overall_rating}/5)"