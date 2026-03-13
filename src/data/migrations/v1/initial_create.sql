-- =============================================================================
-- Migration  : V2__create_ticket_schema.sql
-- Service    : Ticket Service
-- Schema     : ticket
-- Database   : Shared DB (auth + ticket services)
-- Description: Creates the `ticket` schema, its ENUM types, and all Ticket
--              Service tables. Run AFTER V1 (auth schema).
--
-- ⚠️  VERIFY ENUM VALUES against your constants files before running:
--       src/constants/customer_constants.py  → CustomerTier  (must match auth.customertier values)
--       src/constants/sla_constants.py       → Severity, AuditAction, CommentSource
--       src/constants/issue_constants.py     → IssueCategory
--       src/constants/priority_constants.py  → Priority
--       src/constants/ticket_constants.py    → TicketStatus, TicketSource
--
-- CROSS-SERVICE REFERENCES:
--   tickets.customer_id    → auth.customers.user_id  (plain INTEGER, resolved at app layer)
--   tickets.team_id        → auth.teams.id           (plain INTEGER, resolved at app layer)
--   tickets.assigned_agent_id → auth.users.id        (plain INTEGER, resolved at app layer)
--   issue_resolvers.team_id   → auth.teams.id        (plain INTEGER, resolved at app layer)
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- SCHEMA
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS ticket;

-- ---------------------------------------------------------------------------
-- ENUM TYPES  (scoped to ticket schema)
-- ---------------------------------------------------------------------------

-- customertier is redefined here for the ticket schema.
-- ⚠️ Values MUST be identical to auth.customertier — both enums mirror the
--    same domain; they are kept separate so each service is independently deployable.
CREATE TYPE ticket.customertier AS ENUM (
    'smb',
    'enterprise'
);

-- ⚠️ Verify values against IssueCategory in issue_constants.py
CREATE TYPE ticket.issuecategory AS ENUM (
      "login_problem"
      "access_issue"
      "bug_or_error"
      "performance_issue"
      "feature_request"
      "billing_issue"
      "account_management"
      "data_issue"
      "integration_issue"
      "security_concern"
      "other"
);

CREATE TYPE ticket.severity AS ENUM (
    'low',
    'medium',
    'high',
    'critical'
);


CREATE TYPE ticket.priority AS ENUM (
    'p1',
    'p2',
    'p3',
    'p4'
);

CREATE TYPE ticket.priority_eff AS ENUM (
    'low',
    'medium',
    'high',
    'urgent'
);

-- ⚠️ Verify values against TicketStatus in ticket_constants.py
CREATE TYPE ticket.ticketstatus AS ENUM (
        "new"
        "acknowledged"
        "assigned"
        "open"
        "in_progress"
        "on_hold"
        "resolved"
        "closed"
        "reopened"
);

CREATE TYPE ticket.ticketsource AS ENUM (
    'portal',
    'email'
);

-- ⚠️ Verify values against CommentSource in sla_constants.py
CREATE TYPE ticket.commentsource AS ENUM (
    'portal',
    'email',
    'api'
);

-- ⚠️ Verify values against AuditAction in sla_constants.py
CREATE TYPE ticket.auditaction AS ENUM (
    'created',
    'assigned',
    'status_changed',
    'priority_changed',
    'escalated',
    'commented',
    'resolved',
    'closed',
    'reopened',
    'sla_updated'
);

CREATE TYPE ticket.notificationtype AS ENUM (
    'ticket_created',
    'ticket_assigned',
    'status_changed',
    'comment_received',
    'sla_breached'
);

CREATE TYPE ticket.notificationactor AS ENUM (
    'customer',
    'support_agent',
    'team_lead',
    'system'
);

-- ---------------------------------------------------------------------------
-- TABLE: ticket.issues
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket.issues (
    id          SERIAL                 PRIMARY KEY,
    name        VARCHAR(120)           NOT NULL UNIQUE,
    category    ticket.issuecategory   NOT NULL,
    description TEXT,
    is_active   BOOLEAN                NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ            NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ            NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- TABLE: ticket.sla_policies
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket.sla_policies (
    id                   SERIAL                PRIMARY KEY,
    name                 VARCHAR(120)          NOT NULL,
    customer_tier        ticket.customertier   NOT NULL,
    severity             ticket.severity       NOT NULL,
    response_time_mins   FLOAT                 NOT NULL,
    resolution_time_mins FLOAT                 NOT NULL,
    is_active            BOOLEAN               NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ           NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ           NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_sla_tier_severity UNIQUE (customer_tier, severity)
);

-- ---------------------------------------------------------------------------
-- TABLE: ticket.tickets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket.tickets (
    id                              SERIAL                NOT NULL,
    ticket_number                   VARCHAR(30)           NOT NULL UNIQUE,
    title                           VARCHAR(255)          NOT NULL,
    description                     TEXT                  NOT NULL,

    -- Customer (cross-service: plain int → auth.customers.user_id, no FK)
    customer_id                     INTEGER               NOT NULL,
    customer_email                  VARCHAR               NOT NULL,
    customer_tier                   ticket.customertier   NOT NULL,

    -- Issue
    issue_id                        INTEGER               REFERENCES ticket.issues(id) ON DELETE SET NULL,

    -- Priority
    customer_priority               ticket.priority       NOT NULL,
    priority                        ticket.priority_eff   NOT NULL,
    priority_overridden             BOOLEAN               NOT NULL DEFAULT FALSE,
    priority_override_justification TEXT,

    -- Severity
    severity                        ticket.severity       NOT NULL,

    -- Status & source
    status                          ticket.ticketstatus   NOT NULL DEFAULT 'new',
    source                          ticket.ticketsource   NOT NULL DEFAULT 'portal',

    -- Assignment (cross-service: plain ints → auth.teams.id / auth.users.id, no FK)
    team_id                         INTEGER,
    assigned_agent_id               INTEGER,

    -- SLA
    sla_id                          INTEGER               REFERENCES ticket.sla_policies(id) ON DELETE SET NULL,
    response_due_at                 TIMESTAMPTZ,
    resolution_due_at               TIMESTAMPTZ,
    first_response_at               TIMESTAMPTZ,
    resolved_at                     TIMESTAMPTZ,
    closed_at                       TIMESTAMPTZ,

    -- Escalation
    is_escalated                    BOOLEAN               NOT NULL DEFAULT FALSE,
    escalated_at                    TIMESTAMPTZ,

    -- Attachments
    attachments                     JSONB                 DEFAULT '[]',

    -- Timestamps
    created_at                      TIMESTAMPTZ           NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ           NOT NULL DEFAULT NOW(),

    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_tickets_ticket_number     ON ticket.tickets (ticket_number);
CREATE INDEX IF NOT EXISTS idx_tickets_customer_id       ON ticket.tickets (customer_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status            ON ticket.tickets (status);
CREATE INDEX IF NOT EXISTS idx_tickets_team_id           ON ticket.tickets (team_id);
CREATE INDEX IF NOT EXISTS idx_tickets_assigned_agent_id ON ticket.tickets (assigned_agent_id);

-- ---------------------------------------------------------------------------
-- TABLE: ticket.issue_resolvers
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket.issue_resolvers (
    id         SERIAL       PRIMARY KEY,
    issue_id   INTEGER      NOT NULL REFERENCES ticket.issues(id) ON DELETE CASCADE,
    team_id    INTEGER      NOT NULL,   -- plain int → auth.teams.id, resolved at app layer
    team_name  VARCHAR(120),
    is_active  BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- TABLE: ticket.comments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket.comments (
    id          SERIAL                  PRIMARY KEY,
    ticket_id   INTEGER                 NOT NULL REFERENCES ticket.tickets(id) ON DELETE CASCADE,
    author_id   INTEGER                 NOT NULL,
    author_role VARCHAR(30)             NOT NULL,
    content     TEXT                    NOT NULL,
    source      ticket.commentsource    NOT NULL DEFAULT 'portal',
    created_at  TIMESTAMPTZ             NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_comments_ticket_id ON ticket.comments (ticket_id);

-- ---------------------------------------------------------------------------
-- TABLE: ticket.ticket_audits
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket.ticket_audits (
    id         SERIAL                PRIMARY KEY,
    ticket_id  INTEGER               NOT NULL REFERENCES ticket.tickets(id) ON DELETE CASCADE,
    action     ticket.auditaction    NOT NULL,
    actor_id   INTEGER               NOT NULL,
    actor_role VARCHAR(30)           NOT NULL,
    old_value  JSONB,
    new_value  JSONB,
    notes      TEXT,
    created_at TIMESTAMPTZ           NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ticket_audits_ticket_id ON ticket.ticket_audits (ticket_id);

-- ---------------------------------------------------------------------------
-- TABLE: ticket.notifications
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket.notifications (
    id             BIGSERIAL                  PRIMARY KEY,
    recipient_id   INTEGER                    NOT NULL,
    recipient_role ticket.notificationactor   NOT NULL,
    ticket_id      INTEGER                    REFERENCES ticket.tickets(id) ON DELETE CASCADE,
    ticket_number  VARCHAR(30),
    type           ticket.notificationtype    NOT NULL,
    title          VARCHAR(120)               NOT NULL,
    message        TEXT                       NOT NULL,
    is_read        BOOLEAN                    NOT NULL DEFAULT FALSE,
    read_at        TIMESTAMPTZ,
    created_at     TIMESTAMPTZ                NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_recipient_id ON ticket.notifications (recipient_id);
CREATE INDEX IF NOT EXISTS idx_notifications_ticket_id    ON ticket.notifications (ticket_id);
CREATE INDEX IF NOT EXISTS idx_notifications_type         ON ticket.notifications (type);
CREATE INDEX IF NOT EXISTS idx_notifications_is_read      ON ticket.notifications (is_read);

COMMIT;