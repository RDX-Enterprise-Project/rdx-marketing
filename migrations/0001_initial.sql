-- RDX Marketing Engine — canonical PostgreSQL schema.
--
-- Generated from app/models.py:
--     python -c "from app.db import render_postgres_ddl; print(render_postgres_ddl())"
--
-- Logically separate from the RDX Daily Intelligence Engine database. The two
-- connect through marketing_events (sanitised signals), never cross-table.

BEGIN;

CREATE TABLE ai_usage (
	id SERIAL NOT NULL, 
	content_id VARCHAR(32), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	task VARCHAR(48) NOT NULL, 
	provider VARCHAR(48) NOT NULL, 
	model VARCHAR(128) NOT NULL, 
	tokens_in INTEGER NOT NULL, 
	tokens_out INTEGER NOT NULL, 
	cost_usd FLOAT NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	error_message TEXT, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_ai_usage_day ON ai_usage (created_at);

CREATE TABLE content_items (
	content_id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	campaign VARCHAR(128) NOT NULL, 
	pillar VARCHAR(64) NOT NULL, 
	topic VARCHAR(256) NOT NULL, 
	core_message TEXT NOT NULL, 
	target_audience VARCHAR(256), 
	cta TEXT, 
	media_requirement VARCHAR(64) NOT NULL, 
	disclosure_class VARCHAR(32), 
	classified_by VARCHAR(128), 
	classified_at TIMESTAMP WITH TIME ZONE, 
	approval_requirement VARCHAR(32) NOT NULL, 
	approval_status VARCHAR(32) NOT NULL, 
	lifecycle_status VARCHAR(32) NOT NULL, 
	origin VARCHAR(64) NOT NULL, 
	origin_reference VARCHAR(128), 
	notes JSONB, 
	PRIMARY KEY (content_id)
);

CREATE INDEX ix_content_lifecycle ON content_items (lifecycle_status);

CREATE INDEX ix_content_pillar ON content_items (pillar);

CREATE TABLE evidence_records (
	evidence_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	summary TEXT NOT NULL, 
	locator TEXT, 
	recorded_by VARCHAR(128) NOT NULL, 
	verified_at TIMESTAMP WITH TIME ZONE, 
	verified_by VARCHAR(128), 
	max_disclosure VARCHAR(32) NOT NULL, 
	detail JSONB, 
	PRIMARY KEY (evidence_id)
);

CREATE INDEX ix_evidence_kind ON evidence_records (kind);

CREATE TABLE media_assets (
	media_id VARCHAR(48) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	label VARCHAR(256) NOT NULL, 
	locator TEXT NOT NULL, 
	provenance VARCHAR(256) NOT NULL, 
	usage_rights VARCHAR(256) NOT NULL, 
	rights_expire_on DATE, 
	people_depicted JSONB NOT NULL, 
	release_on_file BOOLEAN NOT NULL, 
	added_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	use_count INTEGER NOT NULL, 
	PRIMARY KEY (media_id)
);

CREATE TABLE publication_events (
	event_id SERIAL NOT NULL, 
	publication_id VARCHAR(64) NOT NULL, 
	content_id VARCHAR(32) NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	event_type VARCHAR(40) NOT NULL, 
	actor VARCHAR(128) NOT NULL, 
	detail JSONB NOT NULL, 
	PRIMARY KEY (event_id)
);

CREATE INDEX ix_pub_event_content ON publication_events (content_id, occurred_at);

CREATE TABLE runs (
	run_id VARCHAR(64) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	business_date DATE NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	policy_version VARCHAR(64) NOT NULL, 
	slots_considered INTEGER NOT NULL, 
	slots_filled INTEGER NOT NULL, 
	slots_skipped INTEGER NOT NULL, 
	published INTEGER NOT NULL, 
	blocked INTEGER NOT NULL, 
	failed INTEGER NOT NULL, 
	ai_calls INTEGER NOT NULL, 
	ai_cost_usd FLOAT NOT NULL, 
	notes JSONB, 
	PRIMARY KEY (run_id)
);

CREATE TABLE approvals (
	approval_id SERIAL NOT NULL, 
	content_id VARCHAR(32) NOT NULL, 
	requested_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	decision VARCHAR(24) NOT NULL, 
	decided_by VARCHAR(128), 
	rationale TEXT, 
	policy_snapshot JSONB NOT NULL, 
	PRIMARY KEY (approval_id), 
	FOREIGN KEY(content_id) REFERENCES content_items (content_id)
);

CREATE INDEX ix_approval_decision ON approvals (decision);

CREATE TABLE content_evidence (
	id SERIAL NOT NULL, 
	content_id VARCHAR(32) NOT NULL, 
	evidence_id VARCHAR(64) NOT NULL, 
	claim TEXT NOT NULL, 
	linked_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_content_evidence UNIQUE (content_id, evidence_id, claim), 
	FOREIGN KEY(content_id) REFERENCES content_items (content_id), 
	FOREIGN KEY(evidence_id) REFERENCES evidence_records (evidence_id)
);

CREATE TABLE marketing_events (
	event_id VARCHAR(64) NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	source_system VARCHAR(64) NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	payload JSONB NOT NULL, 
	intake_status VARCHAR(40) NOT NULL, 
	rejection_reason TEXT, 
	content_id VARCHAR(32), 
	PRIMARY KEY (event_id), 
	FOREIGN KEY(content_id) REFERENCES content_items (content_id)
);

CREATE INDEX ix_event_source ON marketing_events (source_system, received_at);

CREATE TABLE platform_variants (
	variant_id VARCHAR(48) NOT NULL, 
	content_id VARCHAR(32) NOT NULL, 
	platform VARCHAR(32) NOT NULL, 
	post_type VARCHAR(32) NOT NULL, 
	body TEXT NOT NULL, 
	first_comment TEXT, 
	hashtags JSONB NOT NULL, 
	cta TEXT, 
	media_ids JSONB NOT NULL, 
	hook_type VARCHAR(48), 
	body_fingerprint VARCHAR(64) NOT NULL, 
	generated_by VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (variant_id), 
	CONSTRAINT uq_variant_per_platform UNIQUE (content_id, platform), 
	FOREIGN KEY(content_id) REFERENCES content_items (content_id)
);

CREATE INDEX ix_variant_fingerprint ON platform_variants (platform, body_fingerprint);

CREATE TABLE policy_decisions (
	id SERIAL NOT NULL, 
	content_id VARCHAR(32) NOT NULL, 
	evaluated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	policy_version VARCHAR(64) NOT NULL, 
	publish_allowed BOOLEAN NOT NULL, 
	requires_approval BOOLEAN NOT NULL, 
	blocking_reasons JSONB NOT NULL, 
	advisory_reasons JSONB NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(content_id) REFERENCES content_items (content_id)
);

CREATE TABLE schedule_slots (
	slot_id VARCHAR(96) NOT NULL, 
	slot_date DATE NOT NULL, 
	weekday VARCHAR(16) NOT NULL, 
	pillar VARCHAR(64) NOT NULL, 
	candidate_pillars JSONB NOT NULL, 
	platform VARCHAR(32) NOT NULL, 
	post_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	content_id VARCHAR(32), 
	status VARCHAR(40) NOT NULL, 
	skip_reason TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (slot_id), 
	FOREIGN KEY(content_id) REFERENCES content_items (content_id)
);

CREATE INDEX ix_slot_date ON schedule_slots (slot_date);

CREATE INDEX ix_slot_day_platform ON schedule_slots (slot_date, platform);

CREATE TABLE publications (
	publication_id VARCHAR(64) NOT NULL, 
	content_id VARCHAR(32) NOT NULL, 
	variant_id VARCHAR(48) NOT NULL, 
	platform VARCHAR(32) NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	provider_post_id VARCHAR(128), 
	scheduled_for TIMESTAMP WITH TIME ZONE, 
	published_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(24) NOT NULL, 
	attempts INTEGER NOT NULL, 
	last_error TEXT, 
	retryable BOOLEAN NOT NULL, 
	permalink TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (publication_id), 
	CONSTRAINT uq_publication_per_variant UNIQUE (variant_id), 
	FOREIGN KEY(content_id) REFERENCES content_items (content_id), 
	FOREIGN KEY(variant_id) REFERENCES platform_variants (variant_id)
);

CREATE INDEX ix_publication_status ON publications (status);

CREATE TABLE post_metrics (
	id SERIAL NOT NULL, 
	publication_id VARCHAR(64) NOT NULL, 
	collected_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	platform VARCHAR(32) NOT NULL, 
	impressions INTEGER, 
	reach INTEGER, 
	reactions INTEGER, 
	comments INTEGER, 
	shares INTEGER, 
	clicks INTEGER, 
	views INTEGER, 
	engagement_rate FLOAT, 
	campaign VARCHAR(128), 
	pillar VARCHAR(64), 
	topic VARCHAR(256), 
	cta TEXT, 
	asset_type VARCHAR(32), 
	hook_type VARCHAR(48), 
	posted_at TIMESTAMP WITH TIME ZONE, 
	raw JSONB, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_metric_sample UNIQUE (publication_id, collected_at), 
	FOREIGN KEY(publication_id) REFERENCES publications (publication_id)
);

CREATE INDEX ix_metrics_pillar ON post_metrics (pillar);

COMMIT;
