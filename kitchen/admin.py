from django import forms
from django.contrib import admin
from django.contrib.auth.hashers import make_password

from .models import KitchenStaff, KitchenTicket, KitchenTicketItem


# ---------------------------------------------------------------------------
# KITCHEN STAFF FORMS
# ---------------------------------------------------------------------------

class KitchenStaffCreationForm(forms.ModelForm):
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    class Meta:
        model  = KitchenStaff
        fields = ('name', 'email', 'role', 'is_active')

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError('Passwords do not match.')
        return p2

    def save(self, commit=True):
        staff = super().save(commit=False)
        staff.set_password(self.cleaned_data['password1'])
        if commit:
            staff.save()
        return staff


class KitchenStaffChangeForm(forms.ModelForm):
    new_password1 = forms.CharField(
        label='New Password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        required=False,
        help_text='Leave blank to keep the current password.',
    )
    new_password2 = forms.CharField(
        label='Confirm New Password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        required=False,
    )

    class Meta:
        model  = KitchenStaff
        fields = ('name', 'email', 'role', 'is_active')

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('new_password1')
        p2 = cleaned.get('new_password2')
        if p1 or p2:
            if p1 != p2:
                raise forms.ValidationError('New passwords do not match.')
        return cleaned

    def save(self, commit=True):
        staff = super().save(commit=False)
        p1 = self.cleaned_data.get('new_password1')
        if p1:
            staff.set_password(p1)
        if commit:
            staff.save()
        return staff


# ---------------------------------------------------------------------------
# KITCHEN STAFF ADMIN
# ---------------------------------------------------------------------------

@admin.register(KitchenStaff)
class KitchenStaffAdmin(admin.ModelAdmin):
    list_display   = ('name', 'email', 'role', 'is_active', 'created_at')
    list_filter    = ('role', 'is_active')
    search_fields  = ('name', 'email')
    readonly_fields = ('created_at',)

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs['form'] = KitchenStaffCreationForm
        else:
            kwargs['form'] = KitchenStaffChangeForm
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (
                ('Personal Info', {
                    'fields': ('name', 'email', 'role', 'is_active')
                }),
                ('Set Password', {
                    'fields': ('password1', 'password2')
                }),
            )
        return (
            ('Personal Info', {
                'fields': ('name', 'email', 'role', 'is_active', 'created_at')
            }),
            ('Change Password', {
                'fields': ('new_password1', 'new_password2'),
                'description': 'Leave both fields blank to keep the current password.',
            }),
        )


# ---------------------------------------------------------------------------
# KITCHEN TICKET
# ---------------------------------------------------------------------------

class KitchenTicketItemInline(admin.TabularInline):
    model        = KitchenTicketItem
    extra        = 0
    readonly_fields = ('order_item', 'started_at', 'completed_at')


@admin.register(KitchenTicket)
class KitchenTicketAdmin(admin.ModelAdmin):
    list_display  = ('order', 'order_status', 'priority', 'table_number', 'is_acknowledged', 'received_at')
    list_filter   = ('priority', 'is_acknowledged', 'order__status')
    search_fields = ('order__id',)
    readonly_fields = ('received_at', 'acknowledged_at')
    inlines       = [KitchenTicketItemInline]

    @admin.display(description='Order Status')
    def order_status(self, obj):
        return obj.order.status


# ---------------------------------------------------------------------------
# KITCHEN TICKET ITEM
# ---------------------------------------------------------------------------

@admin.register(KitchenTicketItem)
class KitchenTicketItemAdmin(admin.ModelAdmin):
    list_display  = ('food_name', 'quantity', 'status', 'started_at', 'completed_at')
    list_filter   = ('status',)
    readonly_fields = ('started_at', 'completed_at')

# chef@restaurant.com
# yourpassword123