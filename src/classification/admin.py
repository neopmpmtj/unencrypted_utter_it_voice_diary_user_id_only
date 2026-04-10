from django.contrib import admin

from .models import (
    EntityCatalog,
    ItemClassificationRun,
    ItemClassificationSelection,
    ItemEntityLink,
    TaxonomyAllowedCombination,
    TaxonomyNode,
    TaxonomyParserRoute,
    TaxonomyPermissionPolicy,
)


@admin.register(TaxonomyNode)
class TaxonomyNodeAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "dimension", "taxonomy_pack", "level", "is_leaf", "is_active")
    list_filter = ("dimension", "taxonomy_pack", "is_active", "is_leaf", "level")
    search_fields = ("key", "label")
    raw_id_fields = ("parent",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(TaxonomyAllowedCombination)
class TaxonomyAllowedCombinationAdmin(admin.ModelAdmin):
    list_display = ("id", "is_allowed", "created_at")
    list_filter = ("is_allowed",)
    raw_id_fields = ("subject_node", "intent_node", "context_node", "time_node", "governance_node")


@admin.register(ItemClassificationRun)
class ItemClassificationRunAdmin(admin.ModelAdmin):
    list_display = ("ingest_item", "status", "taxonomy_pack_used", "overall_confidence", "is_deleted", "created_at")
    list_filter = ("status", "taxonomy_pack_used", "has_ambiguity", "is_deleted")
    raw_id_fields = ("ingest_item", "user")
    readonly_fields = ("created_at", "updated_at", "is_deleted", "deleted_at")

    def get_queryset(self, request):
        return ItemClassificationRun.all_objects.get_queryset()


@admin.register(ItemClassificationSelection)
class ItemClassificationSelectionAdmin(admin.ModelAdmin):
    list_display = ("ingest_item", "dimension", "path_key", "is_primary", "rank_order", "is_deleted", "confidence")
    list_filter = ("dimension", "is_primary", "is_deleted")
    raw_id_fields = ("classification_run", "ingest_item", "taxonomy_node")
    readonly_fields = ("created_at", "is_deleted", "deleted_at")

    def get_queryset(self, request):
        return ItemClassificationSelection.all_objects.get_queryset()


@admin.register(EntityCatalog)
class EntityCatalogAdmin(admin.ModelAdmin):
    list_display = ("canonical_name", "entity_type", "user", "is_active", "created_at")
    list_filter = ("entity_type", "is_active")
    search_fields = ("canonical_name", "normalized_name")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(ItemEntityLink)
class ItemEntityLinkAdmin(admin.ModelAdmin):
    list_display = ("ingest_item", "entity_type", "raw_mention", "role", "is_deleted", "confidence")
    list_filter = ("entity_type", "is_deleted")
    raw_id_fields = ("classification_run", "ingest_item", "entity")
    readonly_fields = ("created_at", "is_deleted", "deleted_at")

    def get_queryset(self, request):
        return ItemEntityLink.all_objects.get_queryset()


@admin.register(TaxonomyPermissionPolicy)
class TaxonomyPermissionPolicyAdmin(admin.ModelAdmin):
    list_display = ("taxonomy_node", "access_scope", "encryption_policy", "retention_policy", "requires_elevated_access")
    list_filter = ("access_scope", "requires_elevated_access")
    raw_id_fields = ("taxonomy_node",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(TaxonomyParserRoute)
class TaxonomyParserRouteAdmin(admin.ModelAdmin):
    list_display = ("key_pattern", "dimension_match", "parser_action", "priority", "is_active")
    list_filter = ("dimension_match", "parser_action", "is_active")
    raw_id_fields = ("taxonomy_node",)
    readonly_fields = ("created_at", "updated_at")
