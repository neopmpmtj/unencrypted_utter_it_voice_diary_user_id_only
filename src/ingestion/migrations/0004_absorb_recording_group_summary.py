"""
Absorb ``ingestion.RecordingGroupSummary`` into ``conversation_summarizer.ConversationSummary``.

WHY
---
The first cut of recording-group summaries put the canonical row in the ingestion
app (table ``ingestion_recordinggroupsummary``). The conversation summarizer app
now owns that concept properly — it adds revision, status, provenance, structured
JSON output and an LLM audit trail — so there must be exactly ONE source of truth.

WHAT THIS DOES
--------------
1. Copies every existing row across (preserving user, group id, text, clip count,
   duration, start/end and created_at, marked READY at revision 1).
2. Drops the old model/table.

Order matters: the copy runs BEFORE the delete, inside the same migration
transaction, so no summary is lost.

Reverse: the table is recreated empty (the data stays in the new table). This is
deliberate — un-absorbing would mean maintaining two tables again.
"""

from django.db import migrations


def copy_rows(apps, schema_editor):
    OldSummary = apps.get_model("ingestion", "RecordingGroupSummary")
    NewSummary = apps.get_model("conversation_summarizer", "ConversationSummary")

    copied = 0
    for old in OldSummary.objects.all().iterator():
        NewSummary.objects.update_or_create(
            user_id=old.user_id,
            recording_group_id=old.recording_group_id,
            defaults={
                "title": "",
                "conversation_type": "",
                "summary_text": old.summary_text or "",
                "structured_data": None,
                "model_used": old.model_used or "",
                "clip_count": old.clip_count or 0,
                "total_duration_seconds": old.total_duration_seconds or 0,
                "started_at": old.started_at,
                "ended_at": old.ended_at,
                "revision": 1,
                "status": "ready",
                "attempts": 0,
                "last_error": "",
                "created_at": old.created_at,
            },
        )
        copied += 1

    if copied:
        print(f"\n  Absorbed {copied} recording-group summary row(s) into conversation_summarizer.")


class Migration(migrations.Migration):

    dependencies = [
        ("ingestion", "0003_recordinggroupsummary_and_more"),
        ("conversation_summarizer", "0002_seed_default_prompt"),
    ]

    operations = [
        migrations.RunPython(copy_rows, migrations.RunPython.noop),
        migrations.DeleteModel(name="RecordingGroupSummary"),
    ]
