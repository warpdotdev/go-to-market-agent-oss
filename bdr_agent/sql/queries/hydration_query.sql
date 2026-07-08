-- Hydrate the minimal Step 2 lead/contact/company context from a HubSpot Lead ID.
-- Parameter:
--   @lead_id STRING
--
-- Validated join path:
--   crm_leads.contact_id
--   -> crm_contacts.contact_id
--   -> crm_contacts.associated_company_id
--   -> crm_companies.company_id
--
-- Do not use crm_leads.company_id for this flow.
-- Do not use contact email domain as a company-domain fallback.
--
-- Dry-run validation note:
-- On 2026-05-18, this query validated successfully against BigQuery and estimated
-- 102,676,950 bytes processed for a single lead_id lookup. This is acceptable for
-- MVP-scale execution, but production volume may require a smaller prepared
-- hydration model/table or clustering/partitioning improvements.

select
  cast(lead.lead_id as string) as lead_id,
  lead.created_at as lead_created_at,
  cast(lead.hubspot_owner_id as string) as hubspot_owner_id,

  cast(contact.contact_id as string) as contact_id,
  contact.email as contact_email,
  contact.first_name as contact_first_name,
  contact.last_name as contact_last_name,
  contact.job_title as contact_job_title,
  cast(contact.associated_company_id as string) as contact_associated_company_id,

  cast(company.company_id as string) as company_id,
  company.company_name,
  company.email_domain as company_email_domain,
  company.alternative_email_domain as company_alternative_email_domain,
  company.website as company_website,
  company.industry as company_industry,
  company.num_employees as company_num_employees,
  company.icp_tier as company_icp_tier
from `example-gcp-project.analytics.crm_leads` as lead
left join `example-gcp-project.analytics.crm_contacts` as contact
  on cast(lead.contact_id as string) = cast(contact.contact_id as string)
left join `example-gcp-project.analytics.crm_companies` as company
  on cast(contact.associated_company_id as string) = cast(company.company_id as string)
where cast(lead.lead_id as string) = @lead_id
limit 1
