# Database Data Points Reference

This document lists application-owned database tables and their fields, organized by module, aligned with the Django models under `src/`. It is not an exhaustive list of every table in PostgreSQL (for example `django_*`, `auth_*`, and `sessions` are omitted).

---

## Accounts (`accounts_`)

### CustomUser (`accounts_customuser`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| password | str | Hashed password |
| last_login | datetime | Last login timestamp |
| is_superuser | bool | Superuser flag |
| first_name | str | First name |
| last_name | str | Last name |
| email | str | Unique email (login identifier) |
| is_staff | bool | Staff/admin access |
| is_active | bool | Account active flag |
| date_joined | datetime | Registration timestamp |
| profile_picture | file | Optional profile image |
| is_email_verified | bool | Email verification status |
| email_verification_token | str | Verification token |
| is_google_account | bool | Registered via Google OAuth |
| deletion_requested_at | datetime | Soft-delete request timestamp |
| tier | str | Plan: `free`, `pro`, `ultra` |
| is_test_user | bool | Bypasses quotas |
| is_app_admin | bool | App admin (no quotas) |

### UserProfile (`accounts_userprofile`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| user_id | FK | → CustomUser |
| bio | text | User bio |
| phone_number | str | Phone number |
| location | str | Location |
| website | url | Website URL |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### UserSecret (`accounts_usersecret`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| user_id | FK | → CustomUser |
| encrypted_google_access_token | text | Encrypted OAuth access token |
| encrypted_google_refresh_token | text | Encrypted refresh token |
| encrypted_google_token_expiry | text | Encrypted expiry timestamp |
| google_token_scopes | text | JSON array of granted scopes |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### UserPreferences (`accounts_userpreferences`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| user_id | FK | → CustomUser |
| preferred_language | str | ISO 639-1 (e.g. `en`) |
| audio_retention_days | int | Days to keep audio (0 = immediate delete) |
| enable_translation | bool | Auto-translate when language differs |
| onboarding_completed | bool | Onboarding done |
| dark_mode | bool | Dark mode enabled |
| accent_theme | str | `green`, `blue`, `indigo`, `purple`, `red`, `orange`, `yellow` |
| standalone_app_ui | bool | App-like mode when PWA |
| show_recording_timer | bool | Show recording duration |
| show_inline_rewrite | bool | Show Rewrite on voice/text composer screens |
| transcription_text_size | str | `small`, `medium`, `large` |
| interface_language | str | UI language (e.g. `pt-pt`, `en`) |
| drive_attachment_folder_name | str | GDrive folder path |
| drive_attachment_folder_id | str | GDrive folder ID |
| timezone | str | User timezone (e.g. `Europe/Lisbon`) |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### GlobalSettings (`accounts_globalsettings`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| key | str | Unique setting key (e.g. `recorder.max_duration`) |
| value | JSON | Setting value |
| description | text | Setting description |
| updated_at | datetime | Last update |

### APIUsageLog (`accounts_apiusagelog`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| user_id | FK | → CustomUser |
| service | str | e.g. `whisper`, `gpt-4o-mini` |
| usage_type | str | e.g. `audio_minutes`, `input_tokens`, `output_tokens` |
| amount | decimal | Usage amount |
| ingest_item_id | FK | → IngestItem (nullable) |
| origin | str | Originating function name (nullable, e.g. `process_audio_ingest`) |
| created_at | datetime | Creation time |

### UserFeatureConfig (`accounts_userfeatureconfig`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| user_id | FK | → CustomUser (OneToOne) |
| enable_auto_classification | bool | Classify items automatically |
| enable_calendar_integration | bool | Google Calendar event creation |
| calendar_trigger_tags | json | Tags that trigger calendar parsing |
| default_calendar_id | str | Google Calendar ID |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

---

## Ingestion (`ingestion_`)

### IngestRun (`ingestion_ingestrun`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| started_at | datetime | Run start |
| finished_at | datetime | Run end |
| note | str | Optional notes |

### IngestItem (`ingestion_ingestitem`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| ingest_run_id | FK | → IngestRun (nullable) |
| parent_item_id | FK | → self (nullable) |
| split_parent_id | FK | → self (nullable) |
| provider | str | `gmail`, `gdrive`, `filesystem`, `manual`, `other` |
| item_type | str | `audio`, `text`, `email`, `file`, `other` |
| template_type | str | `plain`, `list` |
| status | str | `new`, `processed`, `error`, `tagged` |
| external_id | str | External dedupe ID |
| external_thread_id | str | External thread ID |
| source_filename | str | Original filename when from attachment |
| occurred_at | datetime | When event happened |
| ingested_at | datetime | When ingested |
| title | str | Title (plaintext in DB) |
| content_text | text | Transcript/content (plaintext in DB) |
| summary_text | text | Summary (plaintext in DB) |
| is_deleted | bool | Soft delete flag |
| deleted_at | datetime | Deletion time |
| audio_duration_seconds | float | Original audio duration |
| audio_format | str | e.g. `webm`, `wav`, `mp3` |
| original_file_size | bigint | Original file size (bytes) |
| detected_language | str | ISO language code |
| audio_deletion_scheduled_at | datetime | When audio file will be deleted |

### ItemFile (`ingestion_itemfile`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| item_id | FK | → IngestItem |
| role | str | `original`, `attachment`, `transcript`, `processed`, `thumbnail`, `export`, `other` |
| filename | str | File name |
| mime_type | str | MIME type |
| storage_url | text | GDrive URL, local path, etc. |
| bytes | bigint | File size |
| drive_folder_id | str | Google Drive folder ID |
| created_at | datetime | Creation time |

### IngestItemEditLog (`ingestion_ingestitemeditlog`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| item_id | FK | → IngestItem |
| edited_by_id | FK | → CustomUser |
| edited_at | datetime | Edit time |
| fields_changed | JSON | List of changed field names |

### IngestJob (`ingestion_ingestjob`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| item_id | FK | → IngestItem |
| job_type | str | `fetch_gmail`, `process_audio`, `classify_item`, `parse_calendar`, `parse_list`, `parse_financial`, `parse_todo` |
| status | str | `queued`, `running`, `done`, `error` |
| attempt_count | int | Retry count |
| last_error | text | Error message |
| queued_at | datetime | Queue time |
| started_at | datetime | Start time |
| finished_at | datetime | End time |
| checkpoint | str | Pipeline step |
| checkpoint_data | JSON | Checkpoint state |

### GmailRawMessage (`ingestion_gmailrawmessage`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| item_id | FK | → IngestItem (OneToOne) |
| payload_json | JSON | Raw Gmail message |
| created_at | datetime | Creation time |

---

## Retrieval (`retrieval_`)

### ItemRetrievalProjection (`retrieval_itemretrievalprojection`)

Unified retrieval table: one row per IngestItem. Combines embeddings, keywords, and summary (from old EntryIndex) with taxonomy classification data, entities, and governance fields. Refreshed after every successful classification run.

| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| ingest_item_id | FK | → IngestItem (OneToOne) |
| user_id | FK | → CustomUser |
| latest_classification_run_id | FK | → ItemClassificationRun (nullable) |
| primary_subject_key | text | Primary subject taxonomy key |
| secondary_subject_keys | JSON | Secondary subject keys list |
| primary_intent_key | text | Primary intent taxonomy key |
| secondary_intent_keys | JSON | Secondary intent keys list |
| primary_context_key | text | Primary context taxonomy key |
| secondary_context_keys | JSON | Secondary context keys list |
| time_keys | JSON | Time dimension keys list |
| governance_key | text | Governance taxonomy key |
| entity_ids | JSON | EntityCatalog IDs list |
| entity_names_normalized | text | JSON string (list of normalized entity names) |
| entity_roles | text | JSON string (list of entity roles) |
| occurred_at | datetime | Event time |
| ingested_at | datetime | Ingestion time |
| detected_language | str | ISO language code |
| content_text_searchable | text | Plaintext content for search |
| summary_text_searchable | text | Plaintext summary for search |
| embedding_ready_text | text | Text used to compute embedding |
| summary | text | LLM-generated summary |
| keywords | text | JSON string (keyword list) |
| list_items_flat | text | Flattened list items |
| financial_items_flat | text | Flattened financial items |
| todo_items_flat | text | Flattened to-do items |
| embedding | vector(1536) | pgvector embedding |
| token_index | JSON | HMAC token index for blind search |
| has_attachment | bool | Has attachment |
| attachment_types | JSON | Attachment type list |
| overall_confidence | decimal | Classification confidence score |
| is_actionable | bool | Whether item is actionable |
| is_sensitive | bool | Whether item is sensitive |
| last_classified_at | datetime | When last classified |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### ChatSession (`retrieval_chatsession`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| title | text | Session title |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### UserChatMessage (`retrieval_userchatmessage`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| session_id | FK | → ChatSession |
| content | text | Message content |
| sequence_index | int | Per-session ordering |
| created_at | datetime | Creation time |

### AssistantChatMessage (`retrieval_assistantchatmessage`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| session_id | FK | → ChatSession |
| content | text | Message content |
| source_entries | text | JSON string (list of referenced entry metadata dicts) |
| sequence_index | int | Per-session ordering |
| status | str | `read`, `unread`; default `read` |
| metadata | JSON | Future-proof: `performed_actions`, `tool_calls`, etc.; default `{}` |
| created_at | datetime | Creation time |

---

## Batch Calendar — Legacy Calendar Tables

The `batch_calendar` app owns `CalendarEvent` and `CalendarWatchChannel`; they use legacy table names (`calendar_parser_calendarevent`, `calendar_parser_calendarwatchchannel`) from the deprecated calendar_parser module.

### CalendarEvent (`calendar_parser_calendarevent`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| source_item_id | FK | → IngestItem |
| replaces_event_id | FK | → self (nullable) |
| google_event_id | str | Google Calendar event ID |
| summary | text | Event summary |
| description | text | Event description |
| location | str | Event location |
| start_datetime | datetime | Start time |
| end_datetime | datetime | End time |
| timezone | str | Timezone |
| html_link | url | Google Calendar link |
| status | str | `success`, `failed`, `pending`, `pending_confirmation`, `cancelled`, `conflicted` |
| error_message | text | Error details |
| llm_response | JSON | Raw LLM extraction |
| api_response | JSON | Raw Google API response |
| conflicting_events | JSON | Conflicting event details |
| alternative_slots | JSON | Alternative time slots |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |
| is_deleted | bool | Soft delete |
| deleted_at | datetime | Deletion time |

### CalendarWatchChannel (`calendar_parser_calendarwatchchannel`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| channel_id | str | Google channel ID |
| resource_id | str | Resource ID |
| calendar_id | str | Calendar ID |
| sync_token | str | Sync token |
| expiration | datetime | Channel expiration |
| is_active | bool | Active flag |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

---

## Batch Calendar (`batch_calendar_`)

### BatchCalendarRequest (`batch_calendar_batchcalendarrequest`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| ingest_item_id | FK | → IngestItem (nullable) |
| input_text | text | Original user input |
| parsed_events_json | JSON | LLM-parsed events |
| error_message | text | Error details |
| status | str | `pending`, `confirmed`, `cancelled`, `failed`, `partial` |
| is_deleted | bool | Soft delete |
| deleted_at | datetime | Deletion time |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### BatchCalendarEvent (`batch_calendar_batchcalendarevent`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| batch_request_id | FK | → BatchCalendarRequest |
| event_index | int | Order in batch |
| event_data | JSON | Full event (Google format) |
| summary | str | Event summary |
| start_datetime | datetime | Start time |
| end_datetime | datetime | End time |
| timezone | str | Timezone |
| google_event_id | str | Google event ID |
| html_link | url | Calendar link |
| api_response | JSON | API response |
| status | str | `pending`, `pending_confirmation`, `success`, `failed`, `cancelled`, `skipped` |
| error_message | text | Error details |
| conflicting_events | JSON | Conflicting events |
| alternative_slots | JSON | Alternative slots |
| alternative_slots_by_day | JSON | Slots grouped by day for day-navigation UI |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

---

## List Parser (`list_parser_`)

### Unit (`list_parser_unit`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| name | str | Canonical unit name (unique) |
| display_name | str | Human-friendly label |
| aliases | JSON | Alternative spellings |
| is_active | bool | Active flag |
| sort_order | int | Display order |
| created_at | datetime | Creation time |

### ListRecord (`list_parser_listrecord`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| source_item_id | FK | → IngestItem (nullable) |
| created_by_id | FK | → CustomUser (nullable; manual lists) |
| list_name | str | Inferred list name |
| list_context | text | Optional context |
| llm_response | JSON | Raw LLM response |
| status | str | `success`, `failed`, `pending` |
| error_message | text | Error details |
| is_deleted | bool | Soft delete |
| deleted_at | datetime | Deletion time |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### ListItem (`list_parser_listitem`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| list_record_id | FK | → ListRecord |
| parent_id | FK | → self (nullable) |
| item_index | int | Order in list |
| text | text | Item content |
| description | text | Optional detail |
| due_date | date | Optional due date |
| quantity | decimal | Optional quantity |
| unit | str | Optional unit |
| item_data | JSON | Full LLM dict |
| deleted_at | datetime | Soft delete timestamp |
| created_at | datetime | Creation time |

---

## Financial Parser (`financial_parser_`)

### FinancialRecord (`financial_parser_financialrecord`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| source_item_id | FK | → IngestItem (nullable) |
| created_by_id | FK | → CustomUser (nullable; manual records) |
| record_name | str | Inferred record name |
| record_context | text | Optional context |
| llm_response | JSON | Raw LLM response |
| status | str | `success`, `failed`, `pending` |
| error_message | text | Error details |
| is_deleted | bool | Soft delete |
| deleted_at | datetime | Deletion time |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### FinancialItem (`financial_parser_financialitem`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| financial_record_id | FK | → FinancialRecord |
| item_index | int | Order in record |
| type | str | `expense`, `income` |
| amount | decimal | Amount (positive) |
| currency | str | e.g. `EUR` |
| category | str | Category |
| merchant | str | Merchant name |
| transaction_date | date | Transaction date |
| description | text | Description |
| payment_method | str | Payment method |
| item_data | JSON | Full LLM dict |
| deleted_at | datetime | Soft delete timestamp |
| created_at | datetime | Creation time |

### HypermarketLineItem (`financial_parser_hypermarketlineitem`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| financial_record_id | FK | → FinancialRecord |
| line_index | int | Order within invoice |
| description | text | Product name/description |
| quantity | decimal | Quantity |
| unit_price | decimal | Unit price |
| total | decimal | Line total |
| gmail_message_id | str | Gmail message ID (nullable) |
| gmail_filename | str | Gmail filename (nullable) |
| created_at | datetime | Creation time |

---

## Managed Lists (`managed_lists_`)

### TodoRecord (`managed_lists_todorecord`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| source_item_id | FK | → IngestItem (nullable) |
| created_by_id | FK | → CustomUser (nullable; manual records) |
| record_name | str | Inferred record name |
| record_context | text | Optional context |
| llm_response | JSON | Raw LLM response |
| status | str | `success`, `failed`, `pending` |
| error_message | text | Error details |
| is_deleted | bool | Soft delete |
| deleted_at | datetime | Deletion time |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### TodoItem (`managed_lists_todoitem`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| todo_record_id | FK | → TodoRecord |
| parent_id | FK | → self (nullable) |
| item_index | int | Order in record |
| text | text | Item content |
| description | text | Optional detail |
| priority | int | `1` (lowest) to `5` (urgent) |
| completion_status | str | `open`, `in_progress`, `on_hold`, `done`, `cancelled` |
| completed_at | datetime | When completed |
| due_date | date | Optional due date |
| due_time | time | Optional due time |
| topic | str | Topic category |
| subtopic | str | Finer granularity |
| recurrence_rule | str | Recurrence pattern |
| entity_id | FK | → EntityCatalog (nullable); Django field `entity` |
| entity_name | str | Denormalized entity name |
| entity_type | str | Denormalized entity type |
| item_data | JSON | Full LLM dict |
| is_deleted | bool | Soft delete |
| deleted_at | datetime | Deletion time |
| created_at | datetime | Creation time |

### ManagedListProjection (`managed_lists_managedlistprojection`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| source_ingest_item_id | FK | → IngestItem (nullable) |
| list_type | str | `shopping`, `todo`, `financial`, `contact`, `general` |
| record_id | UUID | PK of concrete record |
| item_id | UUID | PK of concrete item |
| title | text | Primary text/name |
| description | text | Optional detail |
| category | str | Maps to list_name, financial category, topic |
| topic | str | Topic |
| subtopic | str | Subtopic |
| item_status | str | `open`, `done`, `expense`, `income`, etc. |
| priority | int | Priority |
| due_date | date | Due date |
| amount | decimal | Amount |
| currency | str | Currency |
| quantity | decimal | Quantity |
| unit | str | Unit |
| entity_name | str | Denormalized entity name |
| entity_type | str | Denormalized entity type |
| entity_catalog_id | UUID | FK for drill-down (nullable) |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

---

## Intent Router (`intent_router_`)

### ItemTriageResult (`intent_router_itemtriageresult`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| item_id | FK | → IngestItem (OneToOne) |
| primary_route | str | Triage routing decision |
| confidence | float | Confidence score |
| contains_time_reference | bool | Has time reference |
| contains_multiple_items | bool | Has multiple items |
| raw_output | JSON | Raw triage output |
| created_at | datetime | Creation time |
| is_deleted | bool | Soft delete |
| deleted_at | datetime | Deletion time |

---

## Classification (`classification_`)

### TaxonomyNode (`classification_taxonomynode`)

Hierarchical taxonomy master table. Each node belongs to a dimension + pack. `key` is the dotted machine-facing path (e.g. `personal.health.appointment.dentist`).

| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| taxonomy_pack | str | `shared`, `personal`, `enterprise` |
| dimension | str | `subject`, `intent`, `context`, `time`, `governance` |
| level | smallint | Depth 1-4 in hierarchy |
| parent_id | FK | → self (nullable) |
| key | text | Dotted machine path (unique) |
| label | str | Human-readable label |
| description | text | Optional description |
| is_leaf | bool | Whether node is a leaf |
| is_selectable | bool | Whether LLM can select this node |
| is_active | bool | Active flag |
| sort_order | int | Display order |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### TaxonomyClosure (`classification_taxonomyclosure`)

Closure table for fast ancestor/descendant lookups. Every node is its own ancestor at depth=0.

| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| ancestor_id | FK | → TaxonomyNode |
| descendant_id | FK | → TaxonomyNode |
| depth | int | Distance between ancestor and descendant |

### TaxonomyAllowedCombination (`classification_taxonomyallowedcombination`)

Records allowed (or disallowed) combinations across dimensions. NULL in a slot means "any value".

| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| subject_node_id | FK | → TaxonomyNode (nullable) |
| intent_node_id | FK | → TaxonomyNode (nullable) |
| context_node_id | FK | → TaxonomyNode (nullable) |
| time_node_id | FK | → TaxonomyNode (nullable) |
| governance_node_id | FK | → TaxonomyNode (nullable) |
| is_allowed | bool | Allow or deny this combination |
| notes | text | Optional explanation |
| created_at | datetime | Creation time |

### ItemClassificationRun (`classification_itemclassificationrun`)

One row per classification attempt. Stores raw LLM outputs, confidence, and validation state.

| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| ingest_item_id | FK | → IngestItem |
| taxonomy_pack_used | str | `shared`, `personal`, `enterprise` |
| classifier_version | str | Classifier version tag |
| prompt_version | str | Prompt version tag |
| verifier_version | str | Verifier version (blank if not used) |
| verifier_prompt_version | str | Verifier prompt version |
| status | str | `pending`, `completed`, `rejected`, `error` |
| raw_model_output_json | JSON | Classifier raw JSON output |
| raw_verifier_output_json | JSON | Verifier raw JSON output (nullable) |
| reasoning_text | text | Classifier reasoning |
| verifier_reasoning_text | text | Verifier reasoning |
| overall_confidence | decimal | Overall confidence 0-1 |
| verifier_overall_confidence | decimal | Verifier confidence 0-1 |
| has_ambiguity | bool | Whether classification is ambiguous |
| ambiguity_notes | JSON | Ambiguity details |
| validation_errors_json | JSON | Deterministic validator errors |
| is_deleted | bool | Soft delete |
| deleted_at | datetime | Deletion time |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### ItemClassificationSelection (`classification_itemclassificationselection`)

One row per chosen taxonomy node per dimension per run. Primary + secondary selections distinguished by `is_primary` and `rank_order`.

| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| classification_run_id | FK | → ItemClassificationRun |
| ingest_item_id | FK | → IngestItem |
| dimension | str | `subject`, `intent`, `context`, `time`, `governance` |
| taxonomy_node_id | FK | → TaxonomyNode |
| path_key | text | Denormalized copy of taxonomy_node.key |
| is_primary | bool | Whether this is the primary selection |
| rank_order | smallint | Rank within dimension (1 = primary) |
| confidence | decimal | Dimension-level confidence 0-1 |
| selection_reason | text | Why this was selected |
| is_deleted | bool | Soft delete |
| deleted_at | datetime | Deletion time |
| created_at | datetime | Creation time |

### EntityCatalog (`classification_entitycatalog`)

User-scoped registry of known entities (people, orgs, projects, etc.).

| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| entity_type | str | `person`, `organization`, `project`, `location`, `device`, `account`, `document`, `product`, `contact`, `vendor`, `client`, `unknown` |
| canonical_name | str | Display name |
| normalized_name | str | Lowercased dedup key (unique per user+type) |
| external_ref | str | Optional external reference |
| metadata_json | JSON | Optional metadata |
| is_active | bool | Active flag |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### ItemEntityLink (`classification_itementitylink`)

Links an entity mention in an ingest item to the entity catalog, through a classification run.

| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| classification_run_id | FK | → ItemClassificationRun |
| ingest_item_id | FK | → IngestItem |
| entity_id | FK | → EntityCatalog (nullable) |
| entity_type | str | Entity type |
| raw_mention | text | Original mention text |
| normalized_mention | str | Lowercased mention |
| role | str | Entity role (e.g. `owner`, `subject`) |
| confidence | decimal | Confidence 0-1 |
| is_deleted | bool | Soft delete |
| deleted_at | datetime | Deletion time |
| created_at | datetime | Creation time |

### TaxonomyPermissionPolicy (`classification_taxonomypermissionpolicy`)

Access, encryption, retention, and visibility rules for governance taxonomy nodes.

| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| taxonomy_node_id | FK | → TaxonomyNode (OneToOne) |
| access_scope | str | e.g. `self_only`, `team_only`, `management_only` |
| encryption_policy | str | e.g. `user_key`, `tenant_key`, `server_key_plus_rbac` |
| retention_policy | str | e.g. `keep`, `ephemeral_30d`, `legal_hold`, `archive_7y` |
| visibility_rule | str | e.g. `hidden_by_default`, `searchable`, `restricted_searchable` |
| requires_elevated_access | bool | Requires elevated access |
| metadata_json | JSON | Optional metadata |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### TaxonomyParserRoute (`classification_taxonomyparserroute`)

Maps taxonomy key patterns to downstream parser actions. Supports prefix matching via `key_pattern`.

| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| taxonomy_node_id | FK | → TaxonomyNode (nullable) |
| dimension_match | str | Which dimension this route matches |
| key_pattern | str | Taxonomy key or prefix pattern (e.g. `intent.reminder.*`) |
| parser_action | str | Downstream parser: `calendar`, `list`, `financial`, `todo` |
| priority | int | Higher priority wins on conflict |
| is_active | bool | Active flag |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

---

## Billing (`billing_`)

### StripeCustomer (`billing_stripecustomer`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| user_id | FK | → CustomUser (OneToOne) |
| stripe_customer_id | str | Stripe customer ID |
| created_at | datetime | Creation time |

### Subscription (`billing_subscription`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| user_id | FK | → CustomUser (OneToOne) |
| stripe_subscription_id | str | Stripe subscription ID |
| stripe_price_id | str | Stripe price ID |
| tier | str | `pro` or `ultra` |
| status | str | `trialing`, `active`, `past_due`, `canceled`, `incomplete`, `incomplete_expired` |
| trial_end | datetime | Trial end (nullable) |
| current_period_start | datetime | Period start |
| current_period_end | datetime | Period end |
| cancel_at_period_end | bool | Will cancel at period end |
| canceled_at | datetime | When canceled (nullable) |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

### StripeWebhookEvent (`billing_stripewebokevent`)
| Field | Type | Description |
|-------|------|--------------|
| id | PK | Auto-generated |
| stripe_event_id | str | Stripe event ID (unique) |
| event_type | str | Webhook event type |
| processed_at | datetime | When processed |
| payload | JSON | Raw webhook payload |

*Note: the physical table name is `billing_stripewebokevent` (as set in Django `Meta.db_table`).*

---

## GIGO monitor (`gigo_`)

Input-quality metrics and nudge logging (`src.gigo`).

### GigoEntry (`gigo_gigoentry`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| ingest_item_id | FK | → IngestItem (nullable) |
| item_type | str | `audio`, `text` |
| word_count | int | Word count |
| rank | str | `low`, `medium`, `high` |
| created_at | datetime | Creation time |

### GigoUserState (`gigo_gigouserstate`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser (OneToOne) |
| consecutive_low_count | int | Consecutive low-rank inputs |
| alert_pending | bool | Nudge alert pending |
| last_updated | datetime | Last update |

### GigoNudgeLog (`gigo_gigonudgelog`)
| Field | Type | Description |
|-------|------|--------------|
| id | UUID | Primary key |
| user_id | FK | → CustomUser |
| created_at | datetime | When nudge was shown |

---

## Module Summary

| Module | Tables | Purpose |
|--------|--------|---------|
| **accounts** | 7 | Users, profiles, secrets, preferences, feature config, API usage |
| **ingestion** | 6 | Diary entries, files, edit logs, jobs, Gmail raw |
| **retrieval** | 4 | Unified retrieval projection, chat (session, user/assistant messages) |
| **batch_calendar** | 4 | Batch requests, batch events, CalendarEvent, CalendarWatchChannel (legacy tables) |
| **list_parser** | 3 | Units, list records, list items |
| **financial_parser** | 3 | Financial records, items, hypermarket line items |
| **managed_lists** | 3 | Todo records, todo items, managed list projection |
| **intent_router** | 1 | Item triage results |
| **classification** | 9 | Taxonomy hierarchy, classification runs, selections, entities, permissions, parser routes |
| **billing** | 3 | Stripe customer, subscription, webhook events |
| **gigo** | 3 | Input quality metrics, per-user GIGO state, nudge log |

---

## Key Relationships for Dashboard Queries

- **CustomUser** is the primary scope; most data is user-scoped via **user_id**.
- **IngestItem** is the core diary entry; linked to calendar events, lists, financial records, retrieval projection, classification runs, jobs, and triage results.
- **CustomUser** links to usage via **APIUsageLog** and feature flags via **UserFeatureConfig**. Subscription tier comes from **Subscription** (billing) or **CustomUser.tier**.
- **ItemRetrievalProjection** provides unencrypted search data (summary, keywords, embedding) and denormalized taxonomy keys/entities for each IngestItem.
- **ItemClassificationRun** → **ItemClassificationSelection** stores the full audit trail of every classification attempt and the taxonomy nodes selected per dimension.
- **EntityCatalog** → **ItemEntityLink** maps entity mentions to a user-scoped entity registry, enabling entity-based queries across diary entries.
- **TaxonomyNode** is the master hierarchy; referenced by classification selections, allowed combinations, permission policies, and parser routes.
- **TaxonomyParserRoute** enables data-driven dispatch to downstream parsers (calendar, list, financial, todo) based on taxonomy keys.
- **ManagedListProjection** provides a cross-list denormalized index for unified search across shopping lists, todos, and financial items.
- **ItemTriageResult** stores pre-classification routing decisions for pipeline optimization.
- **GigoEntry**, **GigoUserState**, and **GigoNudgeLog** track input-quality metrics and nudges per user (**GigoEntry** may link to **IngestItem**).
