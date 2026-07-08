from django.db import models

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

    preparation_time = models.PositiveIntegerField(
        default=15,
        help_text='minutes'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.food_name
    


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

    def __str__(self):
        return f"Table {self.table.table_number}"

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

    def save(self, *args, **kwargs):
        self.subtotal = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.food.food_name} x {self.quantity}"

class Bill(models.Model):

    PAYMENT_STATUS = (
        ('unpaid', 'Unpaid'),
        ('paid', 'Paid')
    )

    session = models.OneToOneField(
        TableSession,
        on_delete=models.CASCADE
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