from django.db import models
from django.contrib.auth.hashers import make_password, check_password


# ---------------------------------------------------------------------------
# BILLING STAFF
# ---------------------------------------------------------------------------

class BillingStaff(models.Model):
    ROLE_CHOICES = [
        ('cashier',     'Cashier'),
        ('accountant',  'Accountant'),
        ('billing_mgr', 'Billing Manager'),
    ]

    name       = models.CharField(max_length=120)
    email      = models.EmailField(unique=True)
    role       = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cashier')
    password   = models.CharField(max_length=256)          # stored hashed
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Billing Staff'
        verbose_name_plural  = 'Billing Staff'
        ordering            = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)
