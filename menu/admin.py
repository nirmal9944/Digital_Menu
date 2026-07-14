from django.contrib import admin

from .models import (
    RestaurantDetail,
    RestaurantTable,
    Category,
    FoodItem,
    Offer,
    VATSetting,
    DiscountSetting,
    QuickItem,
    QuickRequest,
    TableSession,
    Order,
    OrderItem,
    Bill,
)

admin.site.register(RestaurantDetail)
admin.site.register(RestaurantTable)
admin.site.register(Category)
admin.site.register(TableSession)
admin.site.register(Order)
admin.site.register(OrderItem)
admin.site.register(Bill)


# ---------------------------------------------------------------------------
# FOOD ITEM + OFFERS
# ---------------------------------------------------------------------------

class OfferInline(admin.TabularInline):
    model = Offer
    extra = 0
    fields = ('title', 'discount_type', 'value', 'is_active', 'starts_at', 'ends_at')


@admin.register(FoodItem)
class FoodItemAdmin(admin.ModelAdmin):
    list_display = ('food_name', 'category', 'price', 'current_price', 'is_available', 'is_popular')
    list_filter = ('category', 'is_available', 'is_popular')
    search_fields = ('food_name',)
    inlines = [OfferInline]

    @admin.display(description='Price After Offer')
    def current_price(self, obj):
        offer = obj.active_offer
        if not offer:
            return '—'
        return f"Rs. {obj.effective_price} (was Rs. {obj.price})"


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ('title', 'food', 'discount_type', 'value', 'is_active', 'starts_at', 'ends_at')
    list_filter = ('is_active', 'discount_type')
    search_fields = ('title', 'food__food_name')


# ---------------------------------------------------------------------------
# VAT / DISCOUNT SETTINGS
# ---------------------------------------------------------------------------

@admin.register(VATSetting)
class VATSettingAdmin(admin.ModelAdmin):
    list_display = ('name', 'percent', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    readonly_fields = ('updated_at',)


@admin.register(DiscountSetting)
class DiscountSettingAdmin(admin.ModelAdmin):
    list_display = ('name', 'percent', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    readonly_fields = ('updated_at',)


# ---------------------------------------------------------------------------
# QUICK ITEMS (table-side quick requests: water, tissue, pickle, etc.)
# ---------------------------------------------------------------------------

@admin.register(QuickItem)
class QuickItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_free', 'price', 'is_active', 'sort_order')
    list_filter = ('is_free', 'is_active')
    search_fields = ('name',)
    list_editable = ('sort_order', 'is_active')


@admin.register(QuickRequest)
class QuickRequestAdmin(admin.ModelAdmin):
    list_display = ('item', 'table_number', 'quantity', 'is_free', 'unit_price', 'status', 'requested_at')
    list_filter = ('status', 'is_free')
    search_fields = ('item__name', 'session__table__table_number')
    readonly_fields = ('requested_at',)

    @admin.display(description='Table')
    def table_number(self, obj):
        return obj.table_number
