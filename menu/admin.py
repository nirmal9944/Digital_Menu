from django.contrib import admin

from django.contrib import admin
from .models import RestaurantDetail, RestaurantTable, Category, FoodItem,TableSession,Order,OrderItem

admin.site.register(RestaurantDetail)
admin.site.register(RestaurantTable)
admin.site.register(Category)
admin.site.register(FoodItem)
admin.site.register(TableSession)
admin.site.register(Order)
admin.site.register(OrderItem)