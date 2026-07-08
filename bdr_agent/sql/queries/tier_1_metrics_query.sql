-- Step 2 Tier 1 internal product-usage evidence query.
--
-- Purpose:
--   Gather raw domain-level product adoption evidence for BDR hook writing.
--   This is intentionally NOT a PLG/PQL qualification query.
--
-- Design notes:
--   - Use PLG upsell SQL only as inspiration for useful source tables and
--     metrics, not for eligibility filtering.
--   - Do not require work-email status, active Build plans, minimum active
--     users, or absence from the Enterprise pipeline.
--   - Return historical and recent usage separately so synthesis can decide
--     whether there is a useful hook angle.
--   - Paid/team/AI usage are strong positive evidence, but free usage should
--     still be returned when present.
--
-- Parameter contract:
--   @resolved_company_domain STRING
--
-- Row contract:
--   - returns one row when the domain exists in known product, team, company,
--     commercial, or add-on-credit data
--   - returns zero rows when there is no internal data for the domain, so the
--     Python caller records tier_1_internal_metrics.status = "not_found"

with requested_domain as (
  select lower(trim(@resolved_company_domain)) as email_domain
),

domain_presence as (
  select distinct uf.email_domain
  from `example-gcp-project.analytics.users` uf
  inner join requested_domain rd using (email_domain)
  where uf.email_domain is not null

  union distinct

  select distinct tf.admin_email_domain as email_domain
  from `example-gcp-project.analytics.accounts` tf
  inner join requested_domain rd
    on tf.admin_email_domain = rd.email_domain
  where tf.admin_email_domain is not null

  union distinct

  select distinct kc.email_domain
  from `example-gcp-project.analytics.companies` kc
  inner join requested_domain rd using (email_domain)
  where kc.email_domain is not null

  union distinct

  select distinct ed.email_domain
  from `example-gcp-project.analytics.crm_deals` ed
  inner join requested_domain rd using (email_domain)
  where ed.email_domain is not null

  union distinct

  select distinct ac.email_domain
  from `example-gcp-project.analytics.credit_purchases` ac
  inner join requested_domain rd using (email_domain)
  where ac.email_domain is not null
),

user_base as (
  select
    uf.email_domain,
    logical_or(uf.is_public_email_domain) as is_public_email_domain,
    count(distinct uf.user_id) as known_users_total,
    count(distinct if(not uf.is_excluded, uf.user_id, null))
      as non_fraud_users_total,
    count(distinct if(
      not uf.is_excluded
        and uf.days_active_in_last_30 > 0,
      uf.user_id,
      null
    )) as active_users_30d,
    count(distinct if(
      not uf.is_excluded
        and uf.days_active_in_last_90 > 0,
      uf.user_id,
      null
    )) as active_users_90d,
    count(distinct if(
      not uf.is_excluded
        and uf.signup_date >= date_sub(current_date(), interval 30 day),
      uf.user_id,
      null
    )) as signup_users_30d,
    count(distinct if(
      not uf.is_excluded
        and uf.signup_date >= date_sub(current_date(), interval 90 day),
      uf.user_id,
      null
    )) as signup_users_90d,
    min(if(not uf.is_excluded, uf.signup_date, null)) as first_signup_date,
    max(if(not uf.is_excluded, uf.signup_date, null)) as latest_signup_date,
    max(if(not uf.is_excluded, uf.first_activity_at, null))
      as latest_first_activity_at,
    count(distinct if(
      not uf.is_excluded and uf.is_paid,
      uf.user_id,
      null
    )) as paid_users_any,
    count(distinct if(
      not uf.is_excluded
        and uf.is_on_account_with_active_subscription,
      uf.user_id,
      null
    )) as users_on_active_subscription,
    sum(if(
      not uf.is_excluded,
      coalesce(uf.feature_x_requests_last_30_days, 0),
      0
    )) as ai_requests_from_users_30d,
    sum(if(
      not uf.is_excluded,
      coalesce(uf.usage_units_t30d, 0),
      0
    )) as usage_units_from_users_30d,
    sum(if(
      not uf.is_excluded,
      coalesce(uf.prompts_with_model_a_t30d, 0)
        + coalesce(uf.prompts_with_model_b_t30d, 0)
        + coalesce(uf.prompts_with_model_c_t30d, 0)
        + coalesce(uf.prompts_with_model_d_t30d, 0)
        + coalesce(uf.prompts_with_other_model_t30d, 0),
      0
    )) as ai_prompts_30d,
    sum(if(
      not uf.is_excluded,
      coalesce(uf.saved_objects_created_t30d, 0),
      0
    )) as saved_objects_30d
  from `example-gcp-project.analytics.users` uf
  inner join requested_domain rd using (email_domain)
  group by uf.email_domain
),

weekly_activity as (
  select
    email_domain,
    avg(if(_week >= date_sub(date_trunc(current_date(), week(sunday)), interval 28 day), weekly_wau, null))
      as avg_wau_last_4_weeks,
    max(weekly_wau) as peak_wau_last_12_weeks,
    countif(weekly_wau > 0) as active_weeks_last_12_weeks,
    max(if(weekly_wau > 0, _week, null)) as latest_active_week
  from (
    select
      uf.email_domain,
      w._week,
      count(distinct w.user_id) as weekly_wau
    from `example-gcp-project.analytics.weekly_active_users` w
    inner join `example-gcp-project.analytics.users` uf
      on w.user_id = uf.user_id
    inner join requested_domain rd
      on uf.email_domain = rd.email_domain
    where w._week >= date_sub(date_trunc(current_date(), week(sunday)), interval 84 day)
      and w._week < date_trunc(current_date(), week(sunday))
      and not uf.is_excluded
    group by uf.email_domain, w._week
  )
  group by email_domain
),

ai_usage as (
  select
    uf.email_domain,
    count(distinct if(amr.event_date >= date_sub(current_date(), interval 30 day), amr.user_id, null))
      as ai_feature_users_30d,
    count(distinct amr.user_id) as ai_feature_users_90d,
    countif(amr.event_date >= date_sub(current_date(), interval 30 day)) as ai_requests_30d,
    count(*) as ai_requests_90d,
    cast(sum(if(
      amr.event_date >= date_sub(current_date(), interval 30 day),
      amr.usage_units,
      0
    )) as float64) as usage_units_30d,
    cast(sum(amr.usage_units) as float64) as usage_units_90d,
    safe_divide(
      cast(sum(if(
        amr.event_date >= date_sub(current_date(), interval 30 day),
        amr.usage_units,
        0
      )) as float64),
      nullif(count(distinct if(
        amr.event_date >= date_sub(current_date(), interval 30 day),
        amr.user_id,
        null
      )), 0)
    ) as usage_units_per_ai_user_30d,
    max(amr.event_timestamp) as latest_ai_request_at
  from `example-gcp-project.analytics.usage_events` amr
  inner join `example-gcp-project.analytics.users` uf
    on amr.user_id = uf.user_id
  inner join requested_domain rd
    on uf.email_domain = rd.email_domain
  where amr.event_date >= date_sub(current_date(), interval 90 day)
    and not uf.is_excluded
  group by uf.email_domain
),

paywall_pressure as (
  select
    uf.email_domain,
    count(*) as limit_hits_14d,
    count(distinct pw.user_id) as users_hitting_limits_14d,
    max(pw.event_timestamp) as latest_limit_hit_at
  from `example-gcp-project.analytics.paywall_events` pw
  inner join `example-gcp-project.analytics.users` uf
    on pw.user_id = uf.user_id
  inner join requested_domain rd
    on uf.email_domain = rd.email_domain
  where pw.event_date >= date_sub(current_date(), interval 14 day)
    and pw.feature = 'ai_feature'
    and pw.entrypoint = 'agent'
    and not uf.is_excluded
  group by uf.email_domain
),

addon_credits as (
  select
    ac.email_domain,
    cast(sum(ac.invoice_amount) as float64) as reload_dollars_90d,
    count(distinct ac.invoice_id) as reload_count_90d,
    max(ac.invoice_created_at) as latest_reload_at
  from `example-gcp-project.analytics.credit_purchases` ac
  inner join requested_domain rd using (email_domain)
  where ac.invoice_created_at >= timestamp_sub(current_timestamp(), interval 90 day)
    and ac.invoice_status = 'paid'
  group by ac.email_domain
),

paid_conversions as (
  select
    uf.email_domain,
    count(distinct pc.user_id) as users_upgraded_90d,
    max(pc.first_upgrade_at) as latest_upgrade_at
  from `example-gcp-project.analytics.paid_conversions` pc
  inner join `example-gcp-project.analytics.users` uf
    on pc.user_id = uf.user_id
  inner join requested_domain rd
    on uf.email_domain = rd.email_domain
  where pc.did_upgrade = true
    and pc.first_upgrade_at >= timestamp_sub(current_timestamp(), interval 90 day)
    and not uf.is_excluded
  group by uf.email_domain
),

team_adoption as (
  select
    tf.admin_email_domain as email_domain,
    count(distinct tf.account_id) as teams_total,
    count(distinct if(tf.subscription_status = 'active', tf.account_id, null))
      as active_subscription_teams,
    count(distinct if(
      tf.subscription_status = 'active'
        and tf.plan_type in ('team', 'team_plus'),
      tf.account_id,
      null
    )) as active_standard_teams,
    sum(if(tf.subscription_status = 'active', coalesce(tf.plan_seats, 0), 0)) as paid_plan_seats,
    sum(coalesce(tf.active_account_members, 0)) as active_team_members,
    sum(coalesce(tf.account_members_using_ai, 0)) as team_members_using_ai,
    sum(coalesce(tf.num_active_automations, 0)) as active_automations,
    sum(coalesce(tf.num_active_documents, 0)) as active_documents,
    sum(coalesce(tf.active_weeks_last_month, 0)) as active_team_weeks_last_month,
    array_agg(distinct tf.plan_type ignore nulls order by tf.plan_type limit 20) as plan_types,
    max(tf.created_at) as latest_team_created_at
  from `example-gcp-project.analytics.accounts` tf
  inner join requested_domain rd
    on tf.admin_email_domain = rd.email_domain
  group by tf.admin_email_domain
),

team_growth as (
  select
    tf.admin_email_domain as email_domain,
    count(distinct if(
      tme.event_name in ('join account', 'join account via discovery'),
      tme.user_id,
      null
    )) as new_domain_members_30d,
    countif(tme.event_name in ('invited members', 'send member invite email')) as team_invites_30d,
    max(tme.event_timestamp) as latest_team_management_event_at
  from `example-gcp-project.analytics.account_membership_events` tme
  inner join `example-gcp-project.analytics.accounts` tf
    on tme.account_id = tf.account_id
  inner join requested_domain rd
    on tf.admin_email_domain = rd.email_domain
  where tme.event_date >= date_sub(current_date(), interval 30 day)
  group by tf.admin_email_domain
),

enterprise_domain_status as (
  select
    ed.email_domain,
    true as is_enterprise_domain
  from `example-gcp-project.analytics.crm_deals` ed
  inner join requested_domain rd using (email_domain)
  where ed.email_domain is not null
    and not ed.is_closed_lost
  group by ed.email_domain
),

assembled as (
  select
    dp.email_domain,
    current_timestamp() as metrics_as_of,
    coalesce(eds.is_enterprise_domain, false) as is_enterprise_domain,
    coalesce(ub.is_public_email_domain, false) as is_public_email_domain,
    coalesce(ub.known_users_total, 0) as known_users_total,
    coalesce(ub.non_fraud_users_total, 0) as non_fraud_users_total,
    coalesce(ub.active_users_30d, 0) as active_users_30d,
    coalesce(ub.active_users_90d, 0) as active_users_90d,
    coalesce(ub.signup_users_30d, 0) as signup_users_30d,
    coalesce(ub.signup_users_90d, 0) as signup_users_90d,
    ub.first_signup_date,
    ub.latest_signup_date,
    (
      select max(activity_at)
      from unnest([
        ub.latest_first_activity_at,
        timestamp(wa.latest_active_week),
        amu.latest_ai_request_at,
        pp.latest_limit_hit_at,
        ac.latest_reload_at,
        pc.latest_upgrade_at,
        ta.latest_team_created_at,
        tg.latest_team_management_event_at
      ]) as activity_at
    ) as latest_observed_product_activity_at,
    coalesce(wa.avg_wau_last_4_weeks, 0) as avg_wau_last_4_weeks,
    coalesce(wa.peak_wau_last_12_weeks, 0) as peak_wau_last_12_weeks,
    coalesce(wa.active_weeks_last_12_weeks, 0) as active_weeks_last_12_weeks,
    wa.latest_active_week,
    coalesce(amu.ai_feature_users_30d, 0) as ai_feature_users_30d,
    coalesce(amu.ai_feature_users_90d, 0) as ai_feature_users_90d,
    coalesce(amu.ai_requests_30d, ub.ai_requests_from_users_30d, 0)
      as ai_requests_30d,
    coalesce(amu.ai_requests_90d, 0) as ai_requests_90d,
    coalesce(amu.usage_units_30d, cast(ub.usage_units_from_users_30d as float64), 0)
      as usage_units_30d,
    coalesce(amu.usage_units_90d, 0) as usage_units_90d,
    coalesce(amu.usage_units_per_ai_user_30d, 0) as usage_units_per_ai_user_30d,
    coalesce(ub.ai_prompts_30d, 0) as ai_prompts_30d,
    coalesce(ub.saved_objects_30d, 0) as saved_objects_30d,
    coalesce(pp.limit_hits_14d, 0) as limit_hits_14d,
    coalesce(pp.users_hitting_limits_14d, 0) as users_hitting_limits_14d,
    coalesce(ac.reload_dollars_90d, 0) as reload_dollars_90d,
    coalesce(ac.reload_count_90d, 0) as reload_count_90d,
    coalesce(pc.users_upgraded_90d, 0) as users_upgraded_90d,
    coalesce(ub.paid_users_any, 0) as paid_users_any,
    coalesce(ub.users_on_active_subscription, 0) as users_on_active_subscription,
    coalesce(ta.teams_total, 0) as teams_total,
    coalesce(ta.active_subscription_teams, 0) as active_subscription_teams,
    coalesce(ta.active_standard_teams, 0) as active_standard_teams,
    coalesce(ta.paid_plan_seats, 0) as paid_plan_seats,
    coalesce(ta.active_team_members, 0) as active_team_members,
    coalesce(ta.team_members_using_ai, 0) as team_members_using_ai,
    coalesce(ta.active_automations, 0) as active_automations,
    coalesce(ta.active_documents, 0) as active_documents,
    coalesce(ta.active_team_weeks_last_month, 0) as active_team_weeks_last_month,
    coalesce(ta.plan_types, []) as plan_types,
    coalesce(tg.new_domain_members_30d, 0) as new_domain_members_30d,
    coalesce(tg.team_invites_30d, 0) as team_invites_30d
  from domain_presence dp
  left join user_base ub using (email_domain)
  left join weekly_activity wa using (email_domain)
  left join ai_usage amu using (email_domain)
  left join paywall_pressure pp using (email_domain)
  left join addon_credits ac using (email_domain)
  left join paid_conversions pc using (email_domain)
  left join team_adoption ta using (email_domain)
  left join team_growth tg using (email_domain)
  left join enterprise_domain_status eds using (email_domain)
),

classified as (
  select
    *,
    (
      non_fraud_users_total > 0
      or teams_total > 0
      or ai_requests_90d > 0
      or usage_units_90d > 0
      or reload_count_90d > 0
      or users_upgraded_90d > 0
    ) as has_product_usage,
    (
      active_users_30d > 0
      or active_users_90d > 0
      or ai_feature_users_30d > 0
      or ai_feature_users_90d > 0
      or ai_requests_30d > 0
      or usage_units_30d > 0
      or active_team_weeks_last_month > 0
      or new_domain_members_30d > 0
      or team_invites_30d > 0
    ) as has_recent_product_usage,
    (
      paid_users_any > 0
      or users_on_active_subscription > 0
      or active_subscription_teams > 0
      or paid_plan_seats > 0
      or reload_count_90d > 0
      or users_upgraded_90d > 0
    ) as has_paid_signal
  from assembled
)

select
  email_domain,
  metrics_as_of,
  is_enterprise_domain,
  is_public_email_domain,
  has_product_usage,
  has_recent_product_usage,
  has_paid_signal,
  array_concat(
    if(is_public_email_domain, ['public_email_domain'], []),
    if(has_product_usage and not has_recent_product_usage, ['historical_usage_only'], []),
    if(active_users_30d = 0 and active_users_90d > 0, ['active_in_90d_not_30d'], []),
    if(non_fraud_users_total = 0 and teams_total = 0, ['no_product_user_or_team_rows'], []),
    if(is_enterprise_domain, ['enterprise_pipeline_domain'], [])
  ) as data_notes,
  known_users_total,
  non_fraud_users_total,
  active_users_30d,
  active_users_90d,
  signup_users_30d,
  signup_users_90d,
  first_signup_date,
  latest_signup_date,
  latest_observed_product_activity_at,
  avg_wau_last_4_weeks,
  peak_wau_last_12_weeks,
  active_weeks_last_12_weeks,
  latest_active_week,
  ai_feature_users_30d,
  ai_feature_users_90d,
  ai_requests_30d,
  ai_requests_90d,
  usage_units_30d,
  usage_units_90d,
  usage_units_per_ai_user_30d,
  ai_prompts_30d,
  saved_objects_30d,
  limit_hits_14d,
  users_hitting_limits_14d,
  reload_dollars_90d,
  reload_count_90d,
  users_upgraded_90d,
  paid_users_any,
  users_on_active_subscription,
  teams_total,
  active_subscription_teams,
  active_standard_teams,
  paid_plan_seats,
  active_team_members,
  team_members_using_ai,
  active_automations,
  active_documents,
  active_team_weeks_last_month,
  plan_types,
  new_domain_members_30d,
  team_invites_30d
from classified
