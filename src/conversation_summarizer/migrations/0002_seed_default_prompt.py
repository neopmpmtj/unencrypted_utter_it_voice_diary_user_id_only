"""
Seed the default summarizer prompt and two few-shot examples.

This makes the agent **usable and editable from day one**: the prompt text lives
in the database (Django admin → Conversation Summarizer → Summary prompt
templates), so it can be tuned without a code change or a deploy. Saving a change
to the prompt text bumps its ``version``, which every produced summary records.

The seeded default reflects what Pedro described (2026-09-13): most talks are
meetings, some are brainstorms, and the output must be structured JSON in the
language of the transcript.

Reversible: deleting the two rows below restores the pre-migration state.
"""

from django.db import migrations

DEFAULT_TEMPLATE_NAME = "default"

SYSTEM_PROMPT = """You summarize conversations for {{user_name}}'s voice diary.

You receive the transcript of ONE continuous spoken conversation. The recorder
caps a single recording at four minutes, so the app saved it as {{clip_count}}
consecutive clips (about {{duration_minutes}} minutes of talking in total). The
clips are consecutive parts of ONE conversation — never treat them as separate
topics, and never summarize only part of them.

What these conversations usually are, most common first:
- meeting     - people met and talked: who, what was discussed, what was decided
- brainstorm  - idea generation; no decisions are expected
- decision    - one or more decisions were made, with reasoning
- journal     - personal reflection; no action items expected
- other       - anything that does not fit the above

Return ONLY valid JSON, with no markdown fence and no preamble:

{"type": "meeting|brainstorm|decision|journal|other",
 "title": "up to 60 characters, factual, no trailing period",
 "summary": "up to 120 words, plain prose",
 "key_points": ["..."],
 "decisions": ["..."],
 "action_items": ["..."],
 "people": ["..."],
 "language": "ISO 639-1 code of the transcript"}

Rules:
- Write the summary in the SAME LANGUAGE as the transcript. Never translate.
- Include only what is actually present; use [] for sections with nothing.
- Prefer concrete nouns, names, numbers and dates over vague wording.
- Do not invent facts, names or commitments that are not in the transcript."""

USER_PROMPT = """Conversation transcript ({{clip_count}} clips, {{started_at}} → {{ended_at}}):

{{transcript}}"""

EXAMPLES = [
    {
        "label": "meeting with decisions and action items",
        "sort_order": 10,
        "input_excerpt": (
            "--- Clip 1/2 (240s, 2026-09-13 07:01 UTC) ---\n"
            "Right, so me and Joao went through the deploy setup. We agreed Contabo "
            "stays as staging and the DigitalOcean box will be production once the "
            "customer signs off. Anna will update the deployment doc this week and I "
            "need to send her the credentials.\n\n"
            "--- Clip 2/2 (157s, 2026-09-13 07:21 UTC) ---\n"
            "One thing that came up: the backups are still not scheduled anywhere, so "
            "we said we would sort that before going live. Otherwise nothing blocking."
        ),
        "expected_output": (
            '{"type": "meeting", "title": "Deploy split: Contabo staging, DigitalOcean prod", '
            '"summary": "Pedro and Joao reviewed the deployment setup. Contabo stays as staging and '
            'the DigitalOcean server becomes production after customer sign-off. Backups are not '
            'scheduled yet and must be fixed before going live.", '
            '"key_points": ["Contabo remains staging", "DigitalOcean becomes production after sign-off", '
            '"Backups are unscheduled"], '
            '"decisions": ["Keep Contabo as staging", "Use DigitalOcean for production after sign-off", '
            '"Fix backups before go-live"], '
            '"action_items": ["Anna to update the deployment doc this week", '
            '"Pedro to send Anna the credentials", "Schedule backups"], '
            '"people": ["Pedro", "Joao", "Anna"], "language": "en"}'
        ),
    },
    {
        "label": "brainstorm without decisions",
        "sort_order": 20,
        "input_excerpt": (
            "--- Clip 1/1 (240s, 2026-09-12 20:15 UTC) ---\n"
            "Just thinking out loud here. What if the diary app had an agent that watches "
            "the pipeline and bundles long recordings? Could also tag entries automatically. "
            "Another idea: export a weekly digest. No conclusions yet, just parking these."
        ),
        "expected_output": (
            '{"type": "brainstorm", "title": "Ideas: pipeline agent, auto-tagging, weekly digest", '
            '"summary": "Open-ended thinking about the diary app: an agent that watches the pipeline '
            'and bundles long recordings, automatic entry tagging, and a weekly digest export. '
            'No decisions were made.", '
            '"key_points": ["Agent watching the pipeline to bundle long recordings", '
            '"Automatic entry tagging", "Weekly digest export"], '
            '"decisions": [], "action_items": [], "people": ["Pedro"], "language": "en"}'
        ),
    },
]


def seed(apps, schema_editor):
    SummaryPromptTemplate = apps.get_model("conversation_summarizer", "SummaryPromptTemplate")
    SummaryExample = apps.get_model("conversation_summarizer", "SummaryExample")

    template, created = SummaryPromptTemplate.objects.get_or_create(
        name=DEFAULT_TEMPLATE_NAME,
        defaults={
            "conversation_type": "other",
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt": USER_PROMPT,
            "version": 1,
            "is_active": True,
            "is_default": True,
        },
    )
    if not created:
        return  # never overwrite a prompt the user has edited

    for example in EXAMPLES:
        SummaryExample.objects.get_or_create(
            template=template,
            label=example["label"],
            defaults={
                "input_excerpt": example["input_excerpt"],
                "expected_output": example["expected_output"],
                "sort_order": example["sort_order"],
                "is_active": True,
            },
        )


def unseed(apps, schema_editor):
    SummaryPromptTemplate = apps.get_model("conversation_summarizer", "SummaryPromptTemplate")
    SummaryPromptTemplate.objects.filter(name=DEFAULT_TEMPLATE_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("conversation_summarizer", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
