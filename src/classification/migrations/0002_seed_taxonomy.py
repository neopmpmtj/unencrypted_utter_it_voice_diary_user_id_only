from django.db import migrations


def seed_taxonomy(apps, schema_editor):
    TaxonomyNode = apps.get_model("classification", "TaxonomyNode")
    TaxonomyClosure = apps.get_model("classification", "TaxonomyClosure")
    TaxonomyParserRoute = apps.get_model("classification", "TaxonomyParserRoute")

    nodes = [
        ("personal", "subject", 2, "personal.health.appointment.dentist", "Dentist"),
        ("personal", "subject", 2, "personal.daily.diary", "Diary"),
        ("personal", "subject", 2, "personal.finance.expense.groceries", "Groceries"),
        ("shared", "intent", 2, "intent.capture.note.freeform", "Freeform"),
        ("shared", "intent", 2, "intent.capture.note.list", "List"),
        ("shared", "intent", 2, "intent.reminder.future.followup", "Follow-up"),
        ("shared", "intent", 2, "intent.task.create.todo", "Todo"),
        ("shared", "intent", 2, "intent.task.modify.reschedule", "Reschedule"),
        ("shared", "context", 2, "context.self.daily.routine", "Daily routine"),
        ("shared", "governance", 2, "gov.personal.private.self_only", "Self only"),
        ("enterprise", "subject", 2, "enterprise.finance.accounts_payable.invoice", "Invoice"),
    ]
    for pack, dim, level, key, label in nodes:
        n = TaxonomyNode.objects.create(
            taxonomy_pack=pack,
            dimension=dim,
            level=level,
            parent_id=None,
            key=key,
            label=label,
            description="",
            is_leaf=True,
            is_selectable=True,
            is_active=True,
            sort_order=0,
        )
        TaxonomyClosure.objects.create(ancestor_id=n.id, descendant_id=n.id, depth=0)

    routes = [
        ("intent", "intent.task.modify.*", "calendar", 20),
        ("intent", "intent.task.create.*", "todo", 15),
        ("intent", "intent.reminder.*", "calendar", 10),
        ("subject", "personal.health.*", "calendar", 10),
        ("intent", "intent.capture.note.*", "list", 10),
        ("subject", "personal.finance.*", "financial", 10),
    ]
    for dim, pattern, action, priority in routes:
        TaxonomyParserRoute.objects.create(
            taxonomy_node_id=None,
            dimension_match=dim,
            key_pattern=pattern,
            parser_action=action,
            priority=priority,
            is_active=True,
        )


def unseed_taxonomy(apps, schema_editor):
    TaxonomyParserRoute = apps.get_model("classification", "TaxonomyParserRoute")
    TaxonomyClosure = apps.get_model("classification", "TaxonomyClosure")
    TaxonomyNode = apps.get_model("classification", "TaxonomyNode")
    TaxonomyParserRoute.objects.all().delete()
    TaxonomyClosure.objects.all().delete()
    TaxonomyNode.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("classification", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_taxonomy, unseed_taxonomy),
    ]
