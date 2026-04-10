"""
When you write ingestion code (anywhere)

Do this every single time you create an ingest item:

Ensure you have a user context available.

Example:

IngestItem.objects.create(
    user=request.user,
    item_type=...,
    content_text=...,
)
"""
