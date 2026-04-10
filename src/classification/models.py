"""
Classification Models — v14

Hierarchical multi-dimensional taxonomy, classification runs, selections,
entity catalog, entity links, governance policies, and parser routing.
"""

import uuid

from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _


# ---------------------------------------------------------------------------
# Enums (TextChoices)
# ---------------------------------------------------------------------------

class TaxonomyDimension(models.TextChoices):
    SUBJECT = "subject", _("Subject")
    INTENT = "intent", _("Intent")
    CONTEXT = "context", _("Context")
    TIME = "time", _("Time")
    GOVERNANCE = "governance", _("Governance")


class TaxonomyPack(models.TextChoices):
    SHARED = "shared", _("Shared")
    PERSONAL = "personal", _("Personal")
    ENTERPRISE = "enterprise", _("Enterprise")


class EntityType(models.TextChoices):
    PERSON = "person", _("Person")
    ORGANIZATION = "organization", _("Organization")
    PROJECT = "project", _("Project")
    LOCATION = "location", _("Location")
    DEVICE = "device", _("Device")
    ACCOUNT = "account", _("Account")
    DOCUMENT = "document", _("Document")
    PRODUCT = "product", _("Product")
    CONTACT = "contact", _("Contact")
    VENDOR = "vendor", _("Vendor")
    CLIENT = "client", _("Client")
    UNKNOWN = "unknown", _("Unknown")


class ClassificationRunStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    COMPLETED = "completed", _("Completed")
    REJECTED = "rejected", _("Rejected")
    ERROR = "error", _("Error")


# ---------------------------------------------------------------------------
# Taxonomy master table
# ---------------------------------------------------------------------------

class TaxonomyNode(models.Model):
    """
    Hierarchical taxonomy node. Each node belongs to a dimension + pack.
    `key` is the dotted machine-facing path (e.g. personal.health.appointment.dentist).
    All taxonomy nodes are global/shared (application-wide).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    taxonomy_pack = models.CharField(max_length=20, choices=TaxonomyPack.choices)
    dimension = models.CharField(max_length=20, choices=TaxonomyDimension.choices)
    level = models.SmallIntegerField(help_text="Depth 1-4 in the hierarchy")

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="children",
    )

    key = models.TextField(help_text="Dotted machine-facing path, e.g. personal.health.appointment.dentist")
    label = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")

    is_leaf = models.BooleanField(default=False)
    is_selectable = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["key"], name="uq_taxonomy_node_key"),
            models.CheckConstraint(check=models.Q(level__gte=1, level__lte=4), name="chk_taxonomy_level"),
        ]
        indexes = [
            models.Index(
                fields=["taxonomy_pack", "dimension", "key"],
                name="idx_taxonomy_node_lookup",
            ),
        ]
        ordering = ["dimension", "sort_order", "key"]

    def __str__(self) -> str:
        return self.key


# ---------------------------------------------------------------------------
# Taxonomy closure (ancestor/descendant pairs for fast traversal)
# ---------------------------------------------------------------------------

class TaxonomyClosure(models.Model):
    """
    Closure table for fast ancestor/descendant lookups.
    Every node is its own ancestor at depth=0.
    """

    ancestor = models.ForeignKey(
        TaxonomyNode,
        on_delete=models.CASCADE,
        related_name="descendant_links",
    )
    descendant = models.ForeignKey(
        TaxonomyNode,
        on_delete=models.CASCADE,
        related_name="ancestor_links",
    )
    depth = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ancestor", "descendant"], name="uq_taxonomy_closure"),
            models.CheckConstraint(check=models.Q(depth__gte=0), name="chk_closure_depth"),
        ]

    def __str__(self) -> str:
        return f"{self.ancestor.key} -> {self.descendant.key} (depth={self.depth})"


# ---------------------------------------------------------------------------
# Allowed combinations (cross-dimension validation)
# ---------------------------------------------------------------------------

class TaxonomyAllowedCombination(models.Model):
    """
    Records allowed (or disallowed) combinations across dimensions.
    NULL in a dimension slot means "any value" for that dimension.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    subject_node = models.ForeignKey(
        TaxonomyNode, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    intent_node = models.ForeignKey(
        TaxonomyNode, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    context_node = models.ForeignKey(
        TaxonomyNode, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    time_node = models.ForeignKey(
        TaxonomyNode, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    governance_node = models.ForeignKey(
        TaxonomyNode, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )

    is_allowed = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "taxonomy allowed combinations"

    def __str__(self) -> str:
        parts = []
        for dim in ("subject", "intent", "context", "time", "governance"):
            node = getattr(self, f"{dim}_node")
            if node:
                parts.append(f"{dim}={node.key}")
        return f"{'ALLOW' if self.is_allowed else 'DENY'} {', '.join(parts)}"


# ---------------------------------------------------------------------------
# Soft-delete manager
# ---------------------------------------------------------------------------

class SoftDeleteManager(models.Manager):
    """Manager that excludes soft-deleted records by default."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


# ---------------------------------------------------------------------------
# Classification run
# ---------------------------------------------------------------------------

class ItemClassificationRun(models.Model):
    """
    One row per classification attempt (bundles classifier + verifier passes).
    Stores raw LLM outputs, confidence, and validation state.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="classification_runs")
    ingest_item = models.ForeignKey(
        "ingestion.IngestItem",
        on_delete=models.CASCADE,
        related_name="classification_runs",
    )

    taxonomy_pack_used = models.CharField(max_length=20, choices=TaxonomyPack.choices)
    classifier_version = models.CharField(max_length=60)
    prompt_version = models.CharField(max_length=60)

    verifier_version = models.CharField(max_length=60, blank=True, default="")
    verifier_prompt_version = models.CharField(max_length=60, blank=True, default="")

    status = models.CharField(
        max_length=20,
        choices=ClassificationRunStatus.choices,
        default=ClassificationRunStatus.PENDING,
    )

    raw_model_output_json = models.JSONField(null=True, blank=True)
    raw_verifier_output_json = models.JSONField(null=True, blank=True)
    reasoning_text = models.TextField(blank=True, default="")
    verifier_reasoning_text = models.TextField(blank=True, default="")

    overall_confidence = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )
    verifier_overall_confidence = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )

    has_ambiguity = models.BooleanField(default=False)
    ambiguity_notes = models.JSONField(null=True, blank=True)
    validation_errors_json = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(
                fields=["ingest_item", "-created_at"],
                name="idx_classrun_item_date",
            ),
            models.Index(fields=["is_deleted"], name="idx_classrun_is_deleted"),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Run {self.id} [{self.status}] for {self.ingest_item_id}"


# ---------------------------------------------------------------------------
# Classification selections
# ---------------------------------------------------------------------------

class ItemClassificationSelection(models.Model):
    """
    One row per chosen taxonomy node per dimension per run.
    Primary + secondary selections are distinguished by is_primary and rank_order.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    classification_run = models.ForeignKey(
        ItemClassificationRun,
        on_delete=models.CASCADE,
        related_name="selections",
    )
    ingest_item = models.ForeignKey(
        "ingestion.IngestItem",
        on_delete=models.CASCADE,
        related_name="classification_selections",
    )

    dimension = models.CharField(max_length=20, choices=TaxonomyDimension.choices)
    taxonomy_node = models.ForeignKey(
        TaxonomyNode, on_delete=models.RESTRICT, related_name="selections"
    )
    path_key = models.TextField(help_text="Denormalized copy of taxonomy_node.key")

    is_primary = models.BooleanField(default=False)
    rank_order = models.SmallIntegerField(default=1)
    confidence = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )
    selection_reason = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["classification_run", "dimension", "taxonomy_node", "rank_order"],
                name="uq_classselection_run_dim_node_rank",
            ),
        ]
        indexes = [
            models.Index(
                fields=["ingest_item", "dimension", "is_primary", "rank_order"],
                name="idx_classselection_item_dim",
            ),
            models.Index(fields=["is_deleted"], name="idx_classselection_is_deleted"),
        ]

    def __str__(self) -> str:
        tag = "PRIMARY" if self.is_primary else f"secondary#{self.rank_order}"
        return f"{self.dimension}:{self.path_key} ({tag})"


# ---------------------------------------------------------------------------
# Entity catalog
# ---------------------------------------------------------------------------

class EntityCatalog(models.Model):
    """
    User-scoped registry of known entities (people, orgs, projects, etc.).
    Deduplicated by (user, entity_type, normalized_name).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="entity_catalog")
    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    canonical_name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255)
    external_ref = models.CharField(max_length=255, blank=True, default="")
    metadata_json = models.JSONField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "entity catalog entries"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "entity_type", "normalized_name"],
                name="uq_entity_catalog",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "entity_type", "normalized_name"],
                name="idx_entity_catalog_lookup",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type}:{self.canonical_name}"


# ---------------------------------------------------------------------------
# Entity links per ingest item
# ---------------------------------------------------------------------------

class ItemEntityLink(models.Model):
    """
    Links an entity mention in an ingest item to the entity catalog,
    through a classification run.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    classification_run = models.ForeignKey(
        ItemClassificationRun,
        on_delete=models.CASCADE,
        related_name="entity_links",
    )
    ingest_item = models.ForeignKey(
        "ingestion.IngestItem",
        on_delete=models.CASCADE,
        related_name="entity_links",
    )
    entity = models.ForeignKey(
        EntityCatalog,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="item_links",
    )

    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    raw_mention = models.TextField()
    normalized_mention = models.CharField(max_length=255)
    role = models.CharField(max_length=60, blank=True, default="")
    confidence = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=["ingest_item"], name="idx_entitylink_item"),
            models.Index(fields=["is_deleted"], name="idx_entitylink_is_deleted"),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type}:{self.raw_mention} -> {self.entity_id or 'unlinked'}"


# ---------------------------------------------------------------------------
# Governance permission policy
# ---------------------------------------------------------------------------

class TaxonomyPermissionPolicy(models.Model):
    """
    Access, encryption, retention, and visibility rules for a governance taxonomy node.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    taxonomy_node = models.OneToOneField(
        TaxonomyNode,
        on_delete=models.CASCADE,
        related_name="permission_policy",
    )

    access_scope = models.CharField(
        max_length=60,
        help_text="e.g. self_only, team_only, management_only, shared_household",
    )
    encryption_policy = models.CharField(
        max_length=60,
        help_text="e.g. user_key, tenant_key, server_key_plus_rbac",
    )
    retention_policy = models.CharField(
        max_length=60,
        help_text="e.g. keep, ephemeral_30d, legal_hold, archive_7y",
    )
    visibility_rule = models.CharField(
        max_length=60,
        help_text="e.g. hidden_by_default, searchable, restricted_searchable",
    )
    requires_elevated_access = models.BooleanField(default=False)
    metadata_json = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "taxonomy permission policies"

    def __str__(self) -> str:
        return f"Policy for {self.taxonomy_node.key}: {self.access_scope}"


# ---------------------------------------------------------------------------
# Parser routing table
# ---------------------------------------------------------------------------

class TaxonomyParserRoute(models.Model):
    """
    Maps taxonomy key patterns to downstream parser actions.
    Supports prefix matching via key_pattern (e.g. 'intent.reminder.*').

    parser_action values: 'calendar', 'list', 'financial', 'todo'
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    taxonomy_node = models.ForeignKey(
        TaxonomyNode,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="parser_routes",
        help_text="Optional direct FK; key_pattern is the primary match field",
    )
    dimension_match = models.CharField(
        max_length=20,
        choices=TaxonomyDimension.choices,
        help_text="Which dimension this route matches on",
    )
    key_pattern = models.CharField(
        max_length=255,
        help_text="Taxonomy key or prefix pattern (e.g. 'intent.reminder.*')",
    )
    parser_action = models.CharField(
        max_length=40,
        help_text="Downstream parser: calendar, list, financial",
    )
    priority = models.IntegerField(
        default=0,
        help_text="Higher priority wins when multiple routes match",
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "key_pattern"]
        indexes = [
            models.Index(
                fields=["dimension_match", "is_active"],
                name="idx_parserroute_dim_active",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.dimension_match}:{self.key_pattern} -> {self.parser_action}"
