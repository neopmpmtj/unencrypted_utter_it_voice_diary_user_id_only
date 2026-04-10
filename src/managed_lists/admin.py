from django.contrib import admin

from .models import ManagedListProjection, TodoItem, TodoRecord


class TodoItemInline(admin.TabularInline):
    model = TodoItem
    extra = 0
    readonly_fields = ("id", "created_at")
    fields = (
        "item_index", "text", "priority", "completion_status",
        "due_date", "topic", "subtopic", "entity_name", "entity_type",
    )


@admin.register(TodoRecord)
class TodoRecordAdmin(admin.ModelAdmin):
    list_display = ("record_name", "user", "status", "source_item", "created_by", "created_at")
    list_filter = ("status", "is_deleted")
    search_fields = ("record_name", "record_context")
    raw_id_fields = ("user", "source_item", "created_by")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [TodoItemInline]


@admin.register(TodoItem)
class TodoItemAdmin(admin.ModelAdmin):
    list_display = (
        "text", "priority", "completion_status", "due_date",
        "topic", "entity_name", "is_deleted", "created_at",
    )
    list_filter = ("completion_status", "priority", "is_deleted")
    search_fields = ("text", "topic", "subtopic", "entity_name")
    raw_id_fields = ("todo_record", "parent", "entity")
    readonly_fields = ("id", "created_at", "is_deleted", "deleted_at")

    def get_queryset(self, request):
        return TodoItem.all_objects.get_queryset()


@admin.register(ManagedListProjection)
class ManagedListProjectionAdmin(admin.ModelAdmin):
    list_display = (
        "list_type", "title", "category", "item_status",
        "priority", "due_date", "entity_name", "user",
    )
    list_filter = ("list_type", "item_status", "entity_type")
    search_fields = ("title", "description", "category", "topic", "entity_name")
    raw_id_fields = ("user", "source_ingest_item")
    readonly_fields = ("id", "created_at", "updated_at")
